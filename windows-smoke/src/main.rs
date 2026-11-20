//! Runs the Windows backend of `reims-vgpu-vulkan` (`os/windows/`) against this host: the VM's
//! allocations and page table, its refusals, the clocks, threads, and the IOSurface stand-in.
//!
//! The crate's own test target cannot build on Windows (`core::testkit` is unix-only), so this
//! standalone probe links the library without the `testkit` feature and drives the public API of
//! the very code the unit tests would.

use std::ptr::NonNull;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use reims_vgpu_vulkan::core::host::{HostPtr, PhysicalMemoryRange};
use reims_vgpu_vulkan::core::platform::Handle;
use reims_vgpu_vulkan::core::platform::os::clock::Clock as _;
use reims_vgpu_vulkan::core::platform::os::surface::{IoSurfaces, Surface, SurfaceSpec};
use reims_vgpu_vulkan::core::platform::os::threads::Threads as _;
use reims_vgpu_vulkan::core::platform::os::vm::{HostVm as _, VmProt};
use reims_vgpu_vulkan::os::{
    OsClock, OsSurface, OsSurfaces, OsThreads, OsVm, PrivateMemory, RegionMemory,
};
use reims_vgpu_vulkan::VulkanPlatform;

const BGRA: u32 = u32::from_be_bytes(*b"BGRA");

/// One check, named for the report.
fn check(what: &str, ok: bool) {
    println!("{:<64} {}", what, if ok { "PASS" } else { "FAIL" });
    assert!(ok, "{what}");
}

fn main() {
    let vm = OsVm::new();
    let page = vm.page_size();
    check("GetSystemInfo's page size is 4 KiB", page == 0x1000);
    check("page shift is 12", vm.page_shift() == 12);

    // A fresh reservation: zero-filled, readable and writable, protected one page at a time,
    // released only as the whole reservation.
    let two = vm.allocate_anywhere(2 * page).expect("a reservation");
    let two_ptr = two.as_ptr::<u8>();
    check(
        "VirtualAlloc's memory is zero-filled",
        // SAFETY: the reservation's two pages, mapped read and write.
        unsafe { (0..(2 * page) as usize).all(|i| *two_ptr.add(i) == 0) },
    );
    // SAFETY: the first page, mapped read and write.
    unsafe {
        two_ptr.write_volatile(0xa5);
        two_ptr.add(page as usize).write_volatile(0x5a);
    }
    check(
        "fresh memory reads as readable",
        vm.readable(two, 2 * page),
    );
    vm.protect_max(two, page, VmProt::READ)
        .expect("VirtualProtect of the first page");
    check(
        "a read-only page still reads",
        vm.readable(two, 2 * page),
    );
    check(
        "a subrange of a reservation is not released",
        !vm.deallocate(two, page).is_success(),
    );
    check(
        "the whole reservation is released",
        vm.deallocate(two, 2 * page).is_success(),
    );
    check(
        "released memory no longer reads",
        !vm.readable(two, page),
    );

    // An aliasable allocation is the same private memory here.
    let aliasable = vm.allocate_aliasable(2 * page).expect("an aliasable allocation");
    check(
        "allocate_aliasable gives readable memory",
        vm.readable(aliasable, 2 * page),
    );
    check(
        "the aliasable allocation is released whole",
        vm.deallocate(aliasable, 2 * page).is_success(),
    );

    // PrivateMemory: whole pages of zeros, readable, and unmapped at its drop.
    let private = PrivateMemory::new(page + 1).expect("private memory");
    let base = HostPtr::from_addr(private.base() as usize);
    check(
        "PrivateMemory is readable",
        vm.readable(HostPtr::from_addr(private.base() as usize), 2 * page),
    );
    check(
        "PrivateMemory is whole pages of zeros",
        // SAFETY: its two pages, mapped read and write.
        unsafe {
            (0..(2 * page) as usize).all(|i| *private.base().add(i) == 0)
        },
    );
    drop(private);
    check(
        "PrivateMemory is unmapped at its drop",
        !vm.readable(base, 2 * page),
    );
    check(
        "RegionMemory is uninhabited on Windows",
        RegionMemory::new(page).is_none(),
    );

    // The clocks: the counter moves, the wall clock is the Unix epoch's.
    let clock = OsClock::new();
    let a = clock.now_mono();
    std::thread::sleep(Duration::from_millis(5));
    let b = clock.now_mono();
    check(
        "QueryPerformanceCounter moves forward",
        b.as_nanos() >= a.as_nanos() + 5_000_000,
    );
    let wall = Duration::from_nanos(clock.now_wall().as_nanos());
    let system = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("a wall clock after 1970");
    check(
        "GetSystemTimePreciseAsFileTime is the Unix epoch's",
        wall.abs_diff(system) < Duration::from_secs(1),
    );

    // Threads: a spawned thread keeps its whole name.
    let threads = OsThreads::new();
    let (tx, rx) = std::sync::mpsc::channel();
    threads.spawn(
        "reims-vgpu.probe-with-a-long-name",
        256 * 1024,
        Box::new(move || {
            tx.send(std::thread::current().name().map(str::to_owned))
                .expect("the probe's receiver");
        }),
    );
    check(
        "a spawned thread keeps its whole name",
        rx.recv_timeout(Duration::from_secs(10))
            .ok()
            .flatten()
            .as_deref()
            == Some("reims-vgpu.probe-with-a-long-name"),
    );

    let surfaces = OsSurfaces::new();
    let host_only = SurfaceSpec {
        pixel_format: BGRA,
        alloc_size: 1024,
        width: 16,
        height: 16,
        bytes_per_element: 4,
        bytes_per_row: 64,
        element_width: 1,
        element_height: 1,
        ..SurfaceSpec::default()
    };
    let s = <OsSurfaces as IoSurfaces<VulkanPlatform>>::create(&surfaces, &host_only)
        .expect("a host-only surface");
    let surface_base = <OsSurface as Surface>::base_address(&s).as_ptr::<u8>() as usize;
    check(
        "a host-only surface's memory is readable",
        vm.readable(HostPtr::from_addr(surface_base), page),
    );
    check(
        "a host-only surface answers its spec",
        <OsSurface as Surface>::alloc_size(&s) == 1024
            && <OsSurface as Surface>::width(&s) == 16
            && <OsSurface as Surface>::bytes_per_row(&s) == 64,
    );
    // SAFETY: the surface's first four bytes, its own private memory.
    unsafe {
        (surface_base as *mut u8).write_volatile(0x42);
    }
    let looked_up = <OsSurface as Surface>::lookup_by_port(&s).expect("a lookup");
    check(
        "a lookup reads the surface's bytes",
        // SAFETY: the same memory, mapped read and write.
        unsafe { (surface_base as *const u8).read_volatile() == 0x42 },
    );
    check(
        "a lookup counts its own references",
        <OsSurface as Surface>::retain_count(&s) == 1
            && <OsSurface as Surface>::retain_count(&looked_up) == 1
            && <OsSurface as Surface>::retain_count(&<OsSurface as Handle>::clone_ref(&looked_up))
                == 2,
    );
    drop((s, looked_up));
    check(
        "a host-only surface's memory is unmapped with its last object",
        !vm.readable(HostPtr::from_addr(surface_base), page),
    );

    // A surface over the guest's pages: one contiguous run is that run; scattered ranges and a
    // zero tail beyond the run are refused.
    let guest = vm.allocate_aliasable(2 * page).expect("stand-in guest pages");
    let guest_at = guest.as_ptr::<u8>() as usize;
    let ranges: Vec<PhysicalMemoryRange> = (0..2)
        .map(|i| PhysicalMemoryRange {
            physical_address: guest_at as u64 + i * page,
            physical_length: page,
        })
        .collect();
    let over = |spec: SurfaceSpec, ranges: Vec<PhysicalMemoryRange>| SurfaceSpec {
        client_address_ranges: Some(ranges),
        ..spec
    };
    let over_pages = over(
        SurfaceSpec {
            alloc_size: 2 * page,
            ..host_only.clone()
        },
        ranges.clone(),
    );
    let s = <OsSurfaces as IoSurfaces<VulkanPlatform>>::create(&surfaces, &over_pages)
        .expect("a surface over one run of the guest's pages");
    check(
        "a surface over the guest's pages is that run",
        <OsSurface as Surface>::base_address(&s).as_ptr::<u8>() as usize == guest_at,
    );
    drop(s);
    let scattered: Vec<PhysicalMemoryRange> = ranges.iter().rev().copied().collect();
    check(
        "scattered ranges are refused",
        <OsSurfaces as IoSurfaces<VulkanPlatform>>::create(
            &surfaces,
            &over(
                SurfaceSpec {
                    alloc_size: 2 * page,
                    ..host_only.clone()
                },
                scattered,
            ),
        )
        .is_none(),
    );
    check(
        "an offset past the run is refused",
        <OsSurfaces as IoSurfaces<VulkanPlatform>>::create(
            &surfaces,
            &over(
                SurfaceSpec {
                    alloc_size: 2 * page,
                    offset: 0x40,
                    ..host_only.clone()
                },
                ranges.clone(),
            ),
        )
        .is_none(),
    );
    check(
        "a protected surface over the guest's pages is refused",
        <OsSurfaces as IoSurfaces<VulkanPlatform>>::create(
            &surfaces,
            &SurfaceSpec {
                protection_options: 1,
                ..over_pages.clone()
            },
        )
        .is_none(),
    );
    let read_only = over(
        SurfaceSpec {
            alloc_size: 2 * page,
            client_direction: Some(2),
            ..host_only.clone()
        },
        ranges.clone(),
    );
    let s = <OsSurfaces as IoSurfaces<VulkanPlatform>>::create(&surfaces, &read_only)
        .expect("a read-only surface over the guest's pages");
    drop(s);
    check(
        "the guest's pages are released whole after the surface goes",
        vm.deallocate(guest, 2 * page).is_success(),
    );
    check(
        "refused layouts are refused",
        <OsSurfaces as IoSurfaces<VulkanPlatform>>::create(
            &surfaces,
            &SurfaceSpec {
                alloc_size: 1023,
                ..host_only.clone()
            },
        )
        .is_none(),
    );

    // The typed refusals of the remap a Windows host has no form of.
    let fixed = vm.allocate_aliasable(page).expect("a page to refuse");
    check(
        "allocate_fixed_overwrite is a typed refusal",
        NonNull::new(fixed.as_ptr::<u8>())
            .is_some_and(|_| vm.allocate_fixed_overwrite(fixed, page).is_err()),
    );
    check(
        "remap_shared_overwrite is a typed refusal",
        vm.remap_shared_overwrite(fixed, fixed, page).is_err(),
    );
    check(
        "the page is still released whole",
        vm.deallocate(fixed, page).is_success(),
    );

    println!("\nprobe: PASS");
}
