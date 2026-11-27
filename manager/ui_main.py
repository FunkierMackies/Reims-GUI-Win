"""PySide6 main window for the Reims GUI Manager.

Homescreen : VM list on the left, applied-config summary on
the right, and a top toolbar with Start VM / Shutdown VM / Configure VM icon
buttons. The config editor is shown only after Configure VM is clicked.
"""

from __future__ import annotations

import copy
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QSpinBox, QComboBox,
    QPushButton, QToolButton, QPlainTextEdit, QFileDialog, QMessageBox,
    QListWidget, QListWidgetItem, QStackedWidget, QScrollArea, QSizePolicy,
)

import host_detect
import presets
from config_model import VMConfig, VMS_DIR
from gpu_profiles import PROFILES, ALL_KECTS, RESOLUTIONS
from launch_gen import write_launch
from kext_switch import apply_apple_services, apply_profile, apply_smbios
from paths import (MACOS_DISKS, MANAGER, ROOT, pwsh_exe, qemu_for_profile,
                   qemu_img_exe, runtime_env)
from smbios import MODELS, generate as gen_smbios, model_warning, normalize_rom

SECTIONS = ["Host", "VM Settings", "Graphics", "SMBIOS", "Install"]
ICONS = MANAGER / "icons"
KECTS = MANAGER / "kexts"


def icon(name: str) -> QIcon:
    p = ICONS / name
    return QIcon(str(p)) if p.is_file() else QIcon()


class SmbiosWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, model: str):
        super().__init__()
        self.model = model

    def run(self):
        try:
            self.finished_ok.emit(gen_smbios(self.model))
        except Exception as exc:
            self.failed.emit(str(exc))


def make_qcow2(parent: QWidget, name: str, size_gb: int) -> Path | None:
    img = qemu_img_exe()
    if not img.is_file():
        QMessageBox.critical(parent, "qemu-img missing", f"Not found: {img}")
        return None
    MACOS_DISKS.mkdir(parents=True, exist_ok=True)
    name = (name or "macOS").strip().replace(" ", "_")
    target = MACOS_DISKS / f"{name}.qcow2"
    if target.exists():
        QMessageBox.warning(parent, "Already exists", f"Refusing to overwrite:\n{target}")
        return None
    proc = subprocess.run(
        [str(img), "create", "-f", "qcow2", str(target), f"{size_gb}G"],
        capture_output=True, text=True, env=runtime_env())
    if proc.returncode != 0:
        QMessageBox.critical(parent, "Disk creation failed",
                             (proc.stderr or proc.stdout)[-600:])
        return None
    return target


class CreateVmDialog(QDialog):
    """Three-layer wizard: Name, ISO selector, disk (fresh or reuse)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New VM")
        self.resize(620, 520)
        lay = QVBoxLayout(self)

        name_grp = QGroupBox("1. VM name")
        nf = QFormLayout(name_grp)
        self.name_edit = QLineEdit("macOS Tahoe")
        nf.addRow("Name", self.name_edit)
        lay.addWidget(name_grp)

        iso_grp = QGroupBox("2. macOS installer ISO (optional)")
        inf = QFormLayout(iso_grp)
        self.iso_edit = QLineEdit()
        pick_iso = QPushButton("Pick ISO...")
        pick_iso.clicked.connect(self.pick_iso)
        iso_row = QHBoxLayout()
        iso_row.addWidget(self.iso_edit)
        iso_row.addWidget(pick_iso)
        inf.addRow("Installer ISO", iso_row)
        inf.addRow("", QLabel(
            "Any CreateInstallMedia ISO or pkg-converted image. "
            "Can be left empty and attached later in the Install section."))
        lay.addWidget(iso_grp)

        disk_grp = QGroupBox("3. macOS disk (.qcow2)")
        df = QFormLayout(disk_grp)
        self.disk_edit = QLineEdit()
        pick_disk = QPushButton("Pick existing qcow2...")
        pick_disk.clicked.connect(self.pick_disk)
        disk_row = QHBoxLayout()
        disk_row.addWidget(self.disk_edit)
        disk_row.addWidget(pick_disk)
        df.addRow("Disk (reuse installed macOS)", disk_row)

        fresh = QGroupBox("Or create a fresh qcow2 in macOS-disks")
        ff = QFormLayout(fresh)
        self.newdisk_name = QLineEdit("macOS")
        self.newdisk_size = QSpinBox()
        self.newdisk_size.setRange(20, 4096)
        self.newdisk_size.setSingleStep(20)
        self.newdisk_size.setValue(120)
        create_disk = QPushButton("Create qcow2")
        create_disk.clicked.connect(self.create_disk)
        ff.addRow("Disk name", self.newdisk_name)
        ff.addRow("Size (GB)", self.newdisk_size)
        ff.addRow("", create_disk)
        df.addRow("", fresh)
        lay.addWidget(disk_grp)

        btns = QHBoxLayout()
        self.create_btn = QPushButton("Create New VM")
        self.create_btn.setStyleSheet(
            "QPushButton { background-color: #1a73e8; color: white; "
            "font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #1765cc; }")
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(self.create_btn)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self.create_btn.clicked.connect(self.accept)

    def pick_iso(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose macOS installer ISO", "",
            "Disc images (*.iso *.dmg *.cdr);;All files (*)")
        if path:
            self.iso_edit.setText(path)

    def pick_disk(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose existing qcow2 disk", str(MACOS_DISKS),
            "QEMU disk images (*.qcow2 *.qcow *.img);;All files (*)")
        if path:
            self.disk_edit.setText(path)

    def create_disk(self):
        path = make_qcow2(self, self.newdisk_name.text(), self.newdisk_size.value())
        if path:
            self.disk_edit.setText(str(path))

    def values(self) -> tuple[str, str, str]:
        return (self.name_edit.text().strip(), self.iso_edit.text().strip(),
                self.disk_edit.text().strip())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reims GUI Manager - macOS VM")
        self.resize(1150, 760)
        app_icon = QIcon(str(ICONS / "app.png"))
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        # Host detection is expensive (~2.5s via PowerShell); do it ONCE here.
        self.host = host_detect.detect_host()
        self.choice = presets.choose_preset(self.host)
        self.cfg = VMConfig()
        self.smbios_values = None
        self.worker = None
        self._busy = False
        self._edit_snapshot = None
        self._launch_proc = None
        self._launch_ticks = 0
        self._shutdown_pids: list[int] = []
        self._shutdown_elapsed = 0
        self._shutdown_confirmed = False

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_launch)
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.timeout.connect(self._poll_shutdown)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        # ---- top toolbar -------------------------------------------------
        toolbar = QHBoxLayout()
        self.new_vm_btn = QPushButton("Create New VM")
        self.new_vm_btn.clicked.connect(self.create_new_vm)
        toolbar.addWidget(self.new_vm_btn)
        self.import_btn = QPushButton("Import VM")
        self.import_btn.setToolTip("Import a VM from a saved .json config file")
        self.import_btn.clicked.connect(self.import_vm)
        toolbar.addWidget(self.import_btn)
        toolbar.addStretch(1)

        self.start_action = self._tool_button("Start VM", "start.png",
                                              self.launch_selected_vm)
        self.shutdown_action = self._tool_button("Shutdown VM", "shutdown.png",
                                                 self.shutdown_selected_vm)
        self.configure_action = self._tool_button("Configure VM", "settings.png",
                                                  self.open_config_editor)
        for b in (self.start_action, self.shutdown_action, self.configure_action):
            toolbar.addWidget(b)
        outer.addLayout(toolbar)

        # ---- two screens: homescreen and config editor -------------------
        self.screens = QStackedWidget()
        outer.addWidget(self.screens)
        self._build_homescreen()
        self._build_editor()

        self.refresh_vm_list()
        self.cfg = VMConfig()  # nothing selected on startup
        self.preview_stack.setCurrentIndex(0)
        self.show_homescreen()

    def _tool_button(self, text: str, icon_file: str, slot) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setIcon(icon(icon_file))
        b.setIconSize(QSize(36, 36))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setMinimumWidth(96)
        b.clicked.connect(slot)
        return b

    # ---- homescreen -----------------------------------------------------
    def _build_homescreen(self):
        w = QWidget()
        lay = QHBoxLayout(w)

        left = QVBoxLayout()
        left.addWidget(QLabel("Virtual machines:"))
        self.vm_list = QListWidget()
        self.vm_list.itemClicked.connect(self.select_vm)
        left.addWidget(self.vm_list)
        self.remove_btn = QPushButton("Remove VM")
        self.remove_btn.clicked.connect(self.remove_selected_vm)
        left.addWidget(self.remove_btn)
        lw = QWidget()
        lw.setLayout(left)
        lay.addWidget(lw, 1)

        # Right pane swaps between the logo (nothing selected) and the
        # applied-config summary (a VM is actively selected).
        self.preview_stack = QStackedWidget()

        logo_page = QWidget()
        ll = QVBoxLayout(logo_page)
        ll.addStretch(1)
        self.logo_label = QLabel()
        logo = QImage(str(ICONS / "app.png")) if (ICONS / "app.png").is_file() else QImage()
        if not logo.isNull():
            self.logo_label.setPixmap(
                QPixmap.fromImage(logo).scaled(420, 420, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))
        self.logo_label.setAlignment(Qt.AlignCenter)
        ll.addWidget(self.logo_label)
        title = QLabel("Reims GUI Manager")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("", 16, QFont.Bold))
        ll.addWidget(title)
        hint = QLabel("Select a VM to view its applied settings, or create / import one.")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #9aa0a6;")
        ll.addWidget(hint)
        ll.addStretch(1)
        self.preview_stack.addWidget(logo_page)

        right = QVBoxLayout()
        self.right_page = QWidget()
        self.right_page.setLayout(right)
        self.summary_name = QLabel("No VM selected")
        self.summary_name.setFont(QFont("", 14, QFont.Bold))
        right.addWidget(self.summary_name)
        self.summary_status = QLabel("")
        right.addWidget(self.summary_status)
        grp = QGroupBox("General")
        form = QFormLayout(grp)
        self.summary_fields = {}
        for label in ("Display Name", "CPU preset", "Cores allocated",
                      "Memory (MB)", "Graphics profile", "VM resolution",
                      "macOS disk (qcow2)", "Secondary Drive (qcow2/vhd)",
                      "Installer ISO", "SMBIOS model",
                      "Config file", "Launch script"):
            lbl = QLabel("-")
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.summary_fields[label] = lbl
            form.addRow(label + ":", lbl)
        right.addWidget(grp)
        right.addStretch(1)
        self.preview_stack.addWidget(self.right_page)
        rw = QScrollArea()
        rw.setWidget(self.preview_stack)
        rw.setWidgetResizable(True)
        lay.addWidget(rw, 3)
        self.screens.addWidget(w)
        self.preview_stack.setCurrentIndex(0)

    def show_homescreen(self):
        self.screens.setCurrentIndex(0)
        has_vm = self.vm_list.currentItem() is not None
        self.start_action.setEnabled(has_vm)
        self.shutdown_action.setEnabled(has_vm)
        self.configure_action.setEnabled(has_vm)
        self.remove_btn.setEnabled(has_vm)
        self.preview_stack.setCurrentIndex(1 if has_vm else 0)

    def refresh_vm_list(self):
        self.vm_list.clear()
        for name in VMConfig.list_names():
            self.vm_list.addItem(QListWidgetItem(name))

    def update_summary(self):
        cfg = self.cfg
        self.summary_name.setText(cfg.name or "No VM selected")
        running = self._launch_proc is not None and self._launch_proc.poll() is None
        self.summary_status.setText("Status: Running" if running else "Status: Stopped")
        self.summary_fields["Display Name"].setText(cfg.name)
        self.summary_fields["CPU preset"].setText(cfg.cpu or "-")
        self.summary_fields["Cores allocated"].setText(str(cfg.cores))
        self.summary_fields["Memory (MB)"].setText(str(cfg.memory_mb))
        self.summary_fields["Graphics profile"].setText(
            PROFILES.get(cfg.gpu_profile, PROFILES["reims"]).title)
        self.summary_fields["VM resolution"].setText(cfg.resolution)
        self.summary_fields["macOS disk (qcow2)"].setText(cfg.disk or "-")
        self.summary_fields["Secondary Drive (qcow2/vhd)"].setText(
            getattr(cfg, "secondary_drive", "") or "-")
        self.summary_fields["Installer ISO"].setText(cfg.install_iso or "-")
        model = (cfg.smbios or {}).get("SystemProductName", "-")
        self.summary_fields["SMBIOS model"].setText(model)
        self.summary_fields["Config file"].setText(str(cfg.config_path))
        self.summary_fields["Launch script"].setText(str(cfg.launch_script))

    def create_new_vm(self):
        dlg = CreateVmDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        name, iso, disk = dlg.values()
        if not name:
            QMessageBox.warning(self, "Name required", "Enter a VM name.")
            return
        if name in VMConfig.list_names():
            QMessageBox.warning(self, "VM exists", f"A VM named '{name}' already exists.")
            return
        cfg = VMConfig(name=name, install_iso=iso, disk=disk)
        cfg.cpu = self.choice.cpu
        cfg.gpu_profile = self.choice.gpu_default
        cfg.cores = min(max(1, self.host.physical_cores or 4), cfg.cores)
        try:
            apply_apple_services(cfg.prepare_firmware())
            cfg.save()
            write_launch(cfg)
        except Exception as exc:
            QMessageBox.critical(self, "Create failed", str(exc))
            return
        self.refresh_vm_list()
        items = self.vm_list.findItems(name, Qt.MatchExactly)
        if items:
            self.vm_list.setCurrentItem(items[0])
            self.select_vm(items[0])
        QMessageBox.information(
            self, "VM created",
            f"Created:\n{cfg.config_path}\n{cfg.launch_script}")

    def import_vm(self):
        """Import a VM from a .json config file (json files only).

        Re-anchors {ROOT} paths to the source tree when those files are not
        present in the local tree, and copies VM-local firmware/vars so an
        import from another drive actually boots.
        """
        import shutil
        source, _ = QFileDialog.getOpenFileName(
            self, "Import VM configuration", str(VMS_DIR),
            "VM config (*.json);;All files (*)")
        if not source:
            return
        source_path = Path(source)
        try:
            data = json.loads(source_path.read_text(encoding="utf-8"))
            cfg = VMConfig.from_dict(data)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"Cannot read config:\n{exc}")
            return
        cfg.name = (cfg.name or source_path.stem).strip()
        if not cfg.name:
            QMessageBox.warning(self, "Import failed", "Config has no VM name.")
            return
        if cfg.name in VMConfig.list_names():
            QMessageBox.warning(
                self, "VM exists", f"A VM named '{cfg.name}' already exists.")
            return

        # The JSON's {ROOT} tokens expanded to this tree's root. If the target
        # file doesn't exist here, try the source tree (parent of <root>/vms/).
        source_root = source_path.parent.parent.parent
        reanchored = []
        for attr in ("disk", "secondary_drive", "firmware_image",
                     "install_iso", "qemu_exe"):
            value = getattr(cfg, attr, "")
            if not value:
                continue
            p = Path(value)
            if p.exists():
                continue
            try:
                rel = p.relative_to(ROOT)
            except ValueError:
                continue  # absolute user path - leave as-is
            candidate = source_root / rel
            if candidate.exists():
                setattr(cfg, attr, str(candidate))
                reanchored.append(f"{attr} -> {candidate}")

        try:
            cfg.vm_dir.mkdir(parents=True, exist_ok=True)
            # Copy VM-specific firmware/vars from the source VM directory;
            # prepare_firmware() would otherwise use the generic base image.
            src_dir = source_path.parent
            for name in ("OpenCore.img", "vars.fd"):
                src = src_dir / name
                if src.is_file() and not (cfg.vm_dir / name).exists():
                    shutil.copy2(src, cfg.vm_dir / name)
            apply_apple_services(cfg.prepare_firmware())
            cfg.save()
            write_launch(cfg)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self.refresh_vm_list()
        items = self.vm_list.findItems(cfg.name, Qt.MatchExactly)
        if items:
            self.vm_list.setCurrentItem(items[0])
            self.select_vm(items[0])
        detail = (f"Imported '{cfg.name}'.\n{cfg.config_path}\n{cfg.launch_script}")
        if reanchored:
            detail += "\n\nPaths re-anchored to source tree:\n" + "\n".join(reanchored)
        QMessageBox.information(self, "VM imported", detail)

    def select_vm(self, item: QListWidgetItem):
        if self._busy:
            return
        self.vm_list.setCurrentItem(item)
        try:
            cfg = VMConfig.load(item.text())
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        self.cfg = cfg
        self.smbios_values = None
        self._edit_snapshot = copy.deepcopy(cfg.to_dict())
        self._populate_from_cfg()
        self.show_homescreen()
        self.update_summary()

    def _populate_from_cfg(self):
        cfg = self.cfg
        self.name_edit.setText(cfg.name)
        self.disk_edit.setText(cfg.disk)
        self.secondary_edit.setText(getattr(cfg, "secondary_drive", "") or "")
        self.cpu_edit.setText(cfg.cpu)
        self.cores_spin.setValue(cfg.cores)
        self.mem_spin.setValue(cfg.memory_mb)
        self.nvme_model.setText(cfg.nvme_model)
        self.nvme_serial.setText(cfg.nvme_serial)
        self.audio_combo.setCurrentText(cfg.audio_backend)
        self.accel_edit.setText(cfg.accel)
        self.iso_edit.setText(cfg.install_iso)
        if self.choice:
            self.preset_reason.setText(self.choice.reason)
        for i in range(self.gpu_combo.count()):
            if self.gpu_combo.itemData(i) == cfg.gpu_profile:
                self.gpu_combo.setCurrentIndex(i)
                break
        if cfg.resolution in RESOLUTIONS:
            self.res_combo.setCurrentText(cfg.resolution)
        if cfg.smbios:
            self.smbios_output.setPlainText("\n".join(
                f"{k}: {v}" for k, v in cfg.smbios.items()))
            self.smbios_values = None
        self.update_gpu_labels()
        self._update_host_labels()

    # ---- config editor --------------------------------------------------
    def _build_editor(self):
        editor = QWidget()
        el = QVBoxLayout(editor)
        body = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setMaximumWidth(150)
        self.nav.addItems(SECTIONS)
        self.nav.currentRowChanged.connect(self.show_section)
        body.addWidget(self.nav)
        self.stack = QStackedWidget()
        self._build_host_page()
        self._build_vm_page()
        self._build_graphics_page()
        self._build_smbios_page()
        self._build_install_page()
        body.addWidget(self.stack)
        el.addLayout(body)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save_btn = QPushButton("Save VM Settings")
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #1a73e8; color: white; "
            "font-weight: bold; padding: 6px 14px; }")
        self.save_btn.clicked.connect(self.save_vm_settings)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_settings)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.cancel_btn)
        el.addLayout(actions)
        self.screens.addWidget(editor)

    def open_config_editor(self):
        if not self.vm_list.currentItem():
            QMessageBox.warning(self, "No VM", "Select a VM first.")
            return
        self._edit_snapshot = copy.deepcopy(self.cfg.to_dict())
        self.nav.setCurrentRow(0)
        self.screens.setCurrentIndex(1)

    def show_section(self, row: int):
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    def _build_host_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("Detected host specification (read-only)")
        form = QFormLayout(grp)
        self.host_cpu = QLabel("-")
        self.host_vendor = QLabel("-")
        self.host_cores = QLabel("-")
        self.host_avx = QLabel("-")
        self.host_gpu = QLabel("-")
        self.host_rtx = QLabel("-")
        for label, widget in (
            ("CPU", self.host_cpu), ("Vendor", self.host_vendor),
            ("Physical cores", self.host_cores),
            ("AVX / AVX2 / AVX-512", self.host_avx),
            ("GPUs", self.host_gpu), ("NVIDIA RTX present", self.host_rtx),
        ):
            form.addRow(label, widget)
        lay.addWidget(grp)
        redetect = QPushButton("Re-detect host and re-apply preset")
        redetect.clicked.connect(self.refresh_and_apply)
        lay.addWidget(redetect)
        why = QLabel(
            "Why host detection? macOS cannot run natively on all host CPUs, so "
            "the manager spoofs an Intel CPU (-cpu) matching your host's features "
            "and core count. It also detects NVIDIA RTX presence, which decides "
            "the default GPU profile: Reims-Graphics or Apple-GFX.")
        why.setWordWrap(True)
        why.setStyleSheet("color: #9aa0a6; padding: 6px;")
        lay.addWidget(why)
        self.host_notes = QLabel("")
        self.host_notes.setWordWrap(True)
        lay.addWidget(self.host_notes)
        lay.addStretch(1)
        self.stack.addWidget(w)

    def _update_host_labels(self):
        h = self.host
        self.host_cpu.setText(h.cpu_name or "-")
        self.host_vendor.setText(h.cpu_vendor)
        self.host_cores.setText(str(h.physical_cores or "-"))
        self.host_avx.setText(
            f"{self._tri(h.avx)} / {self._tri(h.avx2)} / {self._tri(h.avx512)}")
        self.host_gpu.setText("\n".join(h.gpus) or "-")
        self.host_rtx.setText("Yes" if h.has_nvidia_rtx else "No")
        self.host_notes.setText("  ".join(h.detection_notes))

    def refresh_and_apply(self):
        # Explicit re-detection only (once) - not on every VM click.
        self.host = host_detect.detect_host()
        self.choice = presets.choose_preset(self.host)
        self.apply_preset_to_vm()
        self._update_host_labels()

    @staticmethod
    def _tri(v) -> str:
        return "yes" if v is True else ("no" if v is False else "?")

    def apply_preset_to_vm(self):
        h = self.host
        self.choice = presets.choose_preset(h)
        self.cpu_edit.setText(self.choice.cpu)
        self.cfg.cpu = self.choice.cpu
        max_cores = max(1, h.physical_cores or 4)
        self.cores_spin.setMaximum(max_cores)
        if self.cores_spin.value() > max_cores:
            self.cores_spin.setValue(max_cores)
        self.preset_reason.setText(self.choice.reason)
        self.update_gpu_labels()

    def _build_vm_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("Virtual machine settings")
        form = QFormLayout(grp)
        self.name_edit = QLineEdit()
        self.disk_edit = QLineEdit()
        pick_disk = QPushButton("Pick qcow2...")
        pick_disk.clicked.connect(self.pick_disk)
        disk_row = QHBoxLayout()
        disk_row.addWidget(self.disk_edit)
        disk_row.addWidget(pick_disk)
        self.secondary_edit = QLineEdit()
        self.secondary_edit.setPlaceholderText("None (optional)")
        pick_secondary = QPushButton("Pick qcow2/vhd...")
        pick_secondary.clicked.connect(self.pick_secondary_drive)
        secondary_row = QHBoxLayout()
        secondary_row.addWidget(self.secondary_edit)
        secondary_row.addWidget(pick_secondary)
        self.cpu_edit = QLineEdit()
        self.cpu_edit.setDisabled(True)
        self.cpu_edit.setStyleSheet(
            "QLineEdit:disabled { color: #808080; background: #2b2b2b; }")
        self.cores_spin = QSpinBox()
        self.cores_spin.setRange(1, 256)
        self.mem_spin = QSpinBox()
        self.mem_spin.setRange(1024, 65536)
        self.mem_spin.setSingleStep(1024)
        self.preset_reason = QLabel("")
        self.preset_reason.setWordWrap(True)
        form.addRow("VM name", self.name_edit)
        form.addRow("macOS disk (qcow2)", disk_row)
        form.addRow("Secondary Drive (qcow2/vhd)", secondary_row)
        form.addRow("CPU preset (-cpu)", self.cpu_edit)
        form.addRow("Cores allocated", self.cores_spin)
        form.addRow("Memory (MB)", self.mem_spin)
        form.addRow("Preset reason", self.preset_reason)
        lay.addWidget(grp)

        diskgrp = QGroupBox("Create fresh qcow2 (alternative to picking one)")
        df = QFormLayout(diskgrp)
        self.newdisk_name = QLineEdit("macOS")
        self.newdisk_size = QSpinBox()
        self.newdisk_size.setRange(20, 4096)
        self.newdisk_size.setSingleStep(20)
        self.newdisk_size.setValue(120)
        create = QPushButton("Create qcow2 in macOS-disks")
        create.clicked.connect(self.create_disk)
        df.addRow("Disk name", self.newdisk_name)
        df.addRow("Size (GB)", self.newdisk_size)
        df.addRow("", create)
        lay.addWidget(diskgrp)

        nv = QGroupBox("Storage / audio identity")
        nf = QFormLayout(nv)
        self.nvme_model = QLineEdit(self.cfg.nvme_model)
        self.nvme_serial = QLineEdit(self.cfg.nvme_serial)
        self.audio_combo = QComboBox()
        self.audio_combo.addItems(["dsound", "wav", "none"])
        self.accel_edit = QLineEdit(self.cfg.accel)
        nf.addRow("NVMe model", self.nvme_model)
        nf.addRow("NVMe serial", self.nvme_serial)
        nf.addRow("Host audio backend", self.audio_combo)
        nf.addRow("Accelerator", self.accel_edit)
        lay.addWidget(nv)
        lay.addStretch(1)
        self.stack.addWidget(w)

    def pick_disk(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose macOS qcow2 disk", str(MACOS_DISKS),
            "QEMU disk images (*.qcow2 *.qcow *.img);;All files (*)")
        if path:
            self.disk_edit.setText(path)

    def pick_secondary_drive(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose secondary drive (qcow2/vhd)", str(MACOS_DISKS),
            "Virtual disks (*.qcow2 *.vhd);;All files (*)")
        if path:
            self.secondary_edit.setText(path)

    def create_disk(self):
        path = make_qcow2(self, self.newdisk_name.text(), self.newdisk_size.value())
        if path:
            self.disk_edit.setText(str(path))

    def _build_graphics_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("Graphics selector (interchangeable profiles)")
        form = QFormLayout(grp)
        self.gpu_combo = QComboBox()
        self.gpu_combo.addItem("Reims-Graphics", "reims")
        self.gpu_combo.addItem("Apple-GFX", "apple-gfx")
        self.gpu_combo.currentIndexChanged.connect(self.on_gpu_changed)
        self.res_combo = QComboBox()
        self.res_combo.addItems(RESOLUTIONS)
        self.res_combo.currentTextChanged.connect(self.on_res_changed)
        self.gpu_detail = QLabel("")
        self.gpu_detail.setWordWrap(True)
        self.kext_status = QPlainTextEdit()
        self.kext_status.setReadOnly(True)
        self.kext_status.setMaximumHeight(140)
        form.addRow("GPU profile", self.gpu_combo)
        form.addRow("VM resolution", self.res_combo)
        form.addRow("Details", self.gpu_detail)
        lay.addWidget(grp)
        lay.addWidget(QLabel("Kext bundle availability (6 expected)"))
        lay.addWidget(self.kext_status)
        lay.addStretch(1)
        self.stack.addWidget(w)

    def update_gpu_labels(self):
        if not self.choice:
            return
        items = [
            (0, "reims", "Reims-Graphics (FOR SYSTEM WITH NVIDIA RTX)"),
            (1, "apple-gfx", "Apple-GFX (FOR SYSTEM WITHOUT NVIDIA RTX)"),
        ]
        for index, key, text in items:
            self.gpu_combo.setItemText(index, text)
        key = self.gpu_combo.currentData() or "reims"
        p = PROFILES[key]
        self.gpu_detail.setText(
            f"Device: {p.qemu_device}\nROM: {p.rom_name}\n"
            f"Max resolution: {p.max_resolution}\n"
            f"QEMU: {qemu_for_profile(key).name}\n"
            f"Enable kexts: {', '.join(p.enable)}\n"
            f"Disable kexts: {', '.join(p.disable)}\n{p.notes}")
        max_idx = RESOLUTIONS.index(p.max_resolution)
        for i in range(self.res_combo.count()):
            self.res_combo.model().item(i).setEnabled(i <= max_idx)
        if RESOLUTIONS.index(self.res_combo.currentText()) > max_idx:
            self.res_combo.setCurrentText(p.max_resolution)
        staged = KECTS
        lines = [f"{k}: {'staged' if (staged / k).is_dir() else 'NOT STAGED'}"
                 for k in ALL_KECTS]
        self.kext_status.setPlainText("\n".join(lines))

    def on_res_changed(self):
        self.cfg.resolution = self.res_combo.currentText()

    def on_gpu_changed(self):
        if self.choice:
            self.cfg.gpu_profile = self.gpu_combo.currentData()
            self.update_gpu_labels()

    def _build_smbios_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("SMBIOS generator (GenSMBIOS / macserial, headless)")
        form = QFormLayout(grp)
        self.model_combo = QComboBox()
        for model, note in MODELS:
            self.model_combo.addItem(f"{model} - {note}", model)
        self.model_combo.currentIndexChanged.connect(self.on_model_changed)
        self.smbios_warning = QLabel("")
        self.smbios_warning.setWordWrap(True)
        self.smbios_output = QPlainTextEdit()
        self.smbios_output.setReadOnly(True)
        gen = QPushButton("Generate new SMBIOS")
        gen.clicked.connect(self.generate_smbios)
        form.addRow("Model", self.model_combo)
        form.addRow("", self.smbios_warning)
        form.addRow("Generated values", self.smbios_output)
        form.addRow("", gen)
        lay.addWidget(grp)
        lay.addStretch(1)
        self.stack.addWidget(w)
        self.on_model_changed()

    def on_model_changed(self):
        warn = model_warning(self.model_combo.currentData())
        self.smbios_warning.setText(warn)
        self.smbios_warning.setStyleSheet("color: #ffb000;" if warn else "")

    def generate_smbios(self):
        self.worker = SmbiosWorker(self.model_combo.currentData())
        self.worker.finished_ok.connect(self.on_smbios_done)
        self.worker.failed.connect(
            lambda e: QMessageBox.critical(self, "Generator failed", e))
        self.worker.start()

    def on_smbios_done(self, values):
        self.smbios_values = values
        self.cfg.smbios = values.as_dict()
        self.smbios_output.setPlainText("\n".join(
            f"{k}: {v}" for k, v in values.as_dict().items()))

    def _build_install_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("macOS installer ISO")
        form = QFormLayout(grp)
        self.iso_edit = QLineEdit()
        pick = QPushButton("Pick ISO...")
        pick.clicked.connect(self.pick_iso)
        row = QHBoxLayout()
        row.addWidget(self.iso_edit)
        row.addWidget(pick)
        form.addRow("Installer ISO", row)
        note = QLabel(
            "Attach any macOS installer ISO made with CreateInstallMedia, "
            "or converted from a .pkg (VMware-style). It is connected on a "
            "dedicated SATA/AHCI controller (bootindex 2).")
        note.setWordWrap(True)
        form.addRow("", note)
        lay.addWidget(grp)
        lay.addStretch(1)
        self.stack.addWidget(w)

    def pick_iso(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose macOS installer ISO", "",
            "Disc images (*.iso *.dmg *.cdr);;All files (*)")
        if path:
            self.iso_edit.setText(path)

    # ---- save / cancel --------------------------------------------------
    def collect_settings(self):
        cfg = self.cfg
        cfg.name = self.name_edit.text().strip() or cfg.name
        cfg.disk = self.disk_edit.text().strip()
        cfg.secondary_drive = self.secondary_edit.text().strip()
        cfg.install_iso = self.iso_edit.text().strip()
        cfg.cores = self.cores_spin.value()
        cfg.memory_mb = self.mem_spin.value()
        cfg.nvme_model = self.nvme_model.text()
        cfg.nvme_serial = self.nvme_serial.text()
        cfg.audio_backend = self.audio_combo.currentText()
        cfg.accel = self.accel_edit.text()
        cfg.gpu_profile = self.gpu_combo.currentData() or "reims"
        cfg.resolution = self.res_combo.currentText()
        if self.choice and not cfg.cpu:
            cfg.cpu = self.choice.cpu

    def save_vm_settings(self):
        if self._busy:
            return
        self._busy = True
        try:
            self.collect_settings()
            changed = self.cfg.to_dict() != self._edit_snapshot
            if changed:
                image = self.cfg.prepare_firmware()
                staged = KECTS
                apply_profile(image, self.cfg.gpu_profile,
                              staged_kexts=staged if staged.exists() else None)
                if self.smbios_values:
                    apply_smbios(image, self.smbios_values.as_dict())
                    self.cfg.smbios = self.smbios_values.as_dict()
                elif (self.cfg.smbios or {}).get("ROM"):
                    rom = normalize_rom(self.cfg.smbios["ROM"])
                    if rom:
                        self.cfg.smbios["ROM"] = rom
                        apply_smbios(image, {"ROM": rom})
                self.cfg.save()
                write_launch(self.cfg)
                self._edit_snapshot = copy.deepcopy(self.cfg.to_dict())
                self.refresh_vm_list()
                self.update_summary()
            self.show_homescreen()
            self._timed_popup(
                "VM Settings",
                "New VM Settings Saved" if changed else
                "No New VM Settings Found --- Close Only")
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
        finally:
            self._busy = False

    def cancel_settings(self):
        if self._busy:
            return
        self._busy = True
        try:
            if self._edit_snapshot is not None:
                self.cfg = VMConfig.from_dict(self._edit_snapshot)
                self._populate_from_cfg()
            self.show_homescreen()
            self.update_summary()
        finally:
            self._busy = False

    # ---- VM actions -----------------------------------------------------
    def _timed_popup(self, title: str, text: str, ms: int = 3000):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Information)
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.show()
        QTimer.singleShot(ms, box.close)

    def launch_selected_vm(self, *_):
        if self._busy or not self.cfg.name:
            QMessageBox.warning(self, "No VM", "Select or create a VM first.")
            return
        try:
            path = write_launch(self.cfg)
        except Exception as exc:
            QMessageBox.critical(self, "Launch failed", str(exc))
            return
        log = self.cfg.vm_dir / "qemu-launch.log"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            fh = open(log, "w", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [pwsh_exe(), "-NoProfile", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-File", str(path)],
                env=runtime_env(self.cfg.gpu_profile),
                stdout=fh, stderr=subprocess.STDOUT,
                creationflags=flags)
            fh.close()
        except Exception as exc:
            QMessageBox.critical(self, "Launch failed", str(exc))
            return
        self._launch_proc = proc
        self._launch_ticks = 0
        self._poll_timer.start(500)
        self.update_summary()

    def _poll_launch(self):
        proc = self._launch_proc
        if proc is None:
            self._poll_timer.stop()
            return
        rc = proc.poll()
        if rc is not None:
            self._poll_timer.stop()
            self._launch_proc = None
            if rc != 0:
                log = self.cfg.vm_dir / "qemu-launch.log"
                text = log.read_text(encoding="utf-8", errors="replace")[-1500:] \
                    if log.is_file() else "(no QEMU log)"
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                detail = lines[-1] if lines else "Unknown QEMU error"
                box = QMessageBox(self)
                box.setWindowTitle("VM failed to start")
                box.setIcon(QMessageBox.Critical)
                box.setText(f"{detail}\nExit Code: {rc}")
                box.setDetailedText(text)
                box.setAttribute(Qt.WA_DeleteOnClose, True)
                box.show()
                QTimer.singleShot(3000, box.close)
            self.update_summary()
            return
        self._launch_ticks += 1
        if self._launch_ticks >= 8:  # 4s alive = started
            self._poll_timer.stop()
            self.update_summary()

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Check a Windows process ID without spawning another process."""
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        # 259 == STILL_ACTIVE; zombies report a real exit code.
        return bool(ok) and code.value == 259

    def shutdown_selected_vm(self, *_):
        """Terminate this VM's QEMU with taskkill, then confirm when it exits."""
        if not self.vm_list.currentItem():
            QMessageBox.warning(self, "No VM", "Select a VM first.")
            return

        pids: list[int] = []
        try:
            from config_model import safe_name
            frag = "vms/" + safe_name(self.cfg.name)
            frag_b = frag.replace("/", "\\")
            script = (
                "$p=Get-CimInstance Win32_Process -Filter "
                "\"Name='qemu-system-x86_64.exe'\"; "
                "$p | Where-Object { $_.CommandLine.Contains('" + frag +
                "') -or $_.CommandLine.Contains('" + frag_b + "') } | "
                "ForEach-Object { $_.ProcessId }"
            )
            raw = subprocess.run(
                [pwsh_exe(), "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            pids = [int(x) for x in raw.stdout.split() if x.strip().isdigit()]
        except Exception:
            pass
        # If no direct QEMU match, fall back to the launcher's process tree.
        if not pids and self._launch_proc is not None \
                and self._launch_proc.poll() is None:
            pids = [self._launch_proc.pid]
        if not pids:
            QMessageBox.warning(
                self, "Shutdown", "No running QEMU process found for this VM.")
            return

        self._shutdown_pids = pids
        self._shutdown_elapsed = 0
        self._shutdown_confirmed = False
        self._shutdown_timer.start(500)
        self._timed_popup("Shutdown", "Shutdown Requested")

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, text=True,
                               creationflags=flags, timeout=15)
            except Exception:
                pass

    def _poll_shutdown(self):
        """Wait until every targeted QEMU PID is gone, then show success."""
        self._shutdown_elapsed += 0.5
        alive = [p for p in self._shutdown_pids if self._pid_alive(p)]
        if alive and self._shutdown_elapsed < 20:
            return
        self._shutdown_timer.stop()
        self._shutdown_pids = []
        if self._shutdown_confirmed:
            return
        self._shutdown_confirmed = True
        self._launch_proc = None
        self._poll_timer.stop()
        self.update_summary()
        if alive:
            self._timed_popup("Shutdown", "Shutdown Failed - QEMU still running.")
        else:
            self._timed_popup("Shutdown", "Shutdown Success")

    def remove_selected_vm(self, *_):
        """Delete everything belonging to the selected VM, including its disk."""
        if not self.vm_list.currentItem():
            QMessageBox.warning(self, "No VM", "Select a VM first.")
            return
        cfg = self.cfg
        disks = [Path(d) for d in (cfg.disk, getattr(cfg, "secondary_drive", "")) if d]
        targets = [cfg.vm_dir] + disks
        listing = "\n".join(str(p) for p in targets if p.exists())
        shared = cfg.disk_in_use_elsewhere()
        warning = (
            "⚠️ THIS WILL PERMANENTLY DELETE ALL FILES FOR THIS VM. "
            "This cannot be undone.\n\n"
            + listing
            + "\n\nThe qcow2 disk will also be deleted"
            + (" (NOTE: it is referenced by another VM - that VM will break!)."
               if shared else ".")
        )
        answer = QMessageBox.warning(
            self, "Remove VM", warning,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            include_disk = not shared
            removed = cfg.delete_all(include_disk=include_disk)
        except Exception as exc:
            QMessageBox.critical(self, "Remove failed", str(exc))
            return
        self.cfg = VMConfig()
        self.vm_list.clearSelection()
        self.refresh_vm_list()
        self.preview_stack.setCurrentIndex(0)
        self.show_homescreen()
        QMessageBox.information(
            self, "VM removed", "Deleted:\n" + "\n".join(str(p) for p in removed)
            + ("\n\nDisk kept (shared with another VM)." if shared else ""))


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
