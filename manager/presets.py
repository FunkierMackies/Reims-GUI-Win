"""CPU preset + GPU default rules, transcribed exactly from the manager spec.

The six CPU cases are the full matrix of:
    vendor (Intel / AMD) x AVX-512 (no / yes) x physical cores (2-8 / >8)
for NVIDIA RTX hosts. The -cpu strings are verbatim; do not reformat them.
"""

from __future__ import annotations

from dataclasses import dataclass

from host_detect import HostSpec

# ---- verbatim preset strings -------------------------------------------------
# Intel presets use `check` (not `enforce`): reims-ctl's QEMU build rejects
# unsupported features cleanly instead of aborting the VM.
INTEL_2_8 = (
    "Skylake-Client-v4,vendor=GenuineIntel,"
    "model-id=Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz,"
    "+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,-arat,check"
)
INTEL_GT8 = (
    "Skylake-Client-v4,vendor=GenuineIntel,"
    "model-id=Intel(R) Xeon(R) W-2191B CPU @ 2.30GHz,"
    "+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,-arat,check"
)
INTEL_AVX512 = (
    "Cascadelake-Server-v4,vendor=GenuineIntel,"
    "model-id=Intel(R) Xeon(R) W-3275M CPU @ 2.50GHz,"
    "+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,-arat,check"
)

AMD_2_8 = (
    "Skylake-Client-v4,vendor=GenuineIntel,"
    "model-id=Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz,"
    "+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,"
    "-arat,-pcid,-invpcid,check"
)
AMD_GT8 = (
    "Skylake-Client-v4,vendor=GenuineIntel,"
    "model-id=Intel(R) Xeon(R) W-2191B CPU @ 2.30GHz,"
    "+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,"
    "-arat,-pcid,-invpcid,check"
)
AMD_AVX512 = (
    "Cascadelake-Server-v4,vendor=GenuineIntel,"
    "model-id=Intel(R) Xeon(R) W-3275M CPU @ 2.50GHz,"
    "+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,"
    "-arat,-pcid,-invpcid,check"
)

# Safe generic fallback when detection is inconclusive (e.g. no AVX info).
GENERIC_FALLBACK = (
    "Skylake-Client-v4,vendor=GenuineIntel,"
    "model-id=Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz,"
    "+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,"
    "-arat,-pcid,-invpcid,check"
)


@dataclass(frozen=True)
class PresetChoice:
    key: str
    cpu: str
    reason: str
    gpu_default: str          # "reims" or "apple-gfx"
    reims_label: str
    apple_label: str


# Non-RTX hosts: Apple-gfx is recommended, Reims is DWYOR.
NON_RTX_LABELS = (
    "Not Recommended, DWYOR",
    "Recommended",
)
# RTX hosts: Reims is most recommended, Apple-gfx is not recommended.
RTX_LABELS = (
    "Most Recommended",
    "Not Recommended",
)


def choose_preset(host: HostSpec) -> PresetChoice:
    """Apply the spec's decision table to a detected (or overridden) host."""
    rtx = host.has_nvidia_rtx
    reims_label, apple_label = RTX_LABELS if rtx else NON_RTX_LABELS
    gpu_default = "reims" if rtx else "apple-gfx"

    def make(key: str, cpu: str, reason: str) -> PresetChoice:
        return PresetChoice(key, cpu, reason, gpu_default, reims_label, apple_label)

    if host.cpu_vendor == "Intel":
        if host.avx512:
            return make("intel-avx512", INTEL_AVX512,
                        "Intel host with AVX/AVX2/AVX-512 + NVIDIA RTX")
        if host.core_band == "high":
            return make("intel-gt8", INTEL_GT8,
                        "Intel host, AVX/AVX2 (no AVX-512), >8 physical cores + NVIDIA RTX")
        if host.core_band == "low":
            return make("intel-2-8", INTEL_2_8,
                        "Intel host, AVX/AVX2 (no AVX-512), 2-8 physical cores + NVIDIA RTX")
    elif host.cpu_vendor == "AMD":
        if host.avx512:
            return make("amd-avx512", AMD_AVX512,
                        "AMD host with AVX/AVX2/AVX-512 + NVIDIA RTX")
        if host.core_band == "high":
            return make("amd-gt8", AMD_GT8,
                        "AMD host, AVX/AVX2 (no AVX-512), >8 physical cores + NVIDIA RTX")
        if host.core_band == "low":
            return make("amd-2-8", AMD_2_8,
                        "AMD host, AVX/AVX2 (no AVX-512), 2-8 physical cores + NVIDIA RTX")

    # The spec only defines CPU presets for RTX hosts. For everything else we
    # keep the exact i7-10700 string with the conservative -pcid/-invpcid/check
    # tail, and flag why it was chosen.
    reason = "Fallback preset: host detection was inconclusive"
    if not rtx:
        reason = ("Fallback preset: no NVIDIA RTX detected; GPU defaults to "
                  "Apple-gfx. Set CPU features/vendor manually for an exact preset.")
    return make("fallback", GENERIC_FALLBACK, reason)


PRESET_TABLE = [
    ("Intel, AVX+AVX2, no AVX-512, 2-8 cores, RTX", INTEL_2_8, "reims"),
    ("Intel, AVX+AVX2, no AVX-512, >8 cores, RTX", INTEL_GT8, "reims"),
    ("Intel, AVX+AVX2+AVX-512, RTX", INTEL_AVX512, "reims"),
    ("AMD, AVX+AVX2, no AVX-512, 2-8 cores, RTX", AMD_2_8, "reims"),
    ("AMD, AVX+AVX2, no AVX-512, >8 cores, RTX", AMD_GT8, "reims"),
    ("AMD, AVX+AVX2+AVX-512, RTX", AMD_AVX512, "reims"),
]
