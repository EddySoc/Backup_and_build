"""
Combined Build + Spec/Requirements GUI
Merges spec generation, requirements freeze and PyInstaller build into one tool.
The individual standalone tools remain available for manual use.
"""

import sys
import customtkinter as ctk
from tkinter import filedialog, messagebox, Menu
import os
import subprocess
import json
import queue
import shutil
import re
import zipfile
import traceback
from datetime import datetime
from threading import Thread

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class BuildCombinedGUI(ctk.CTk):
    # ──────────────────────────────────────────────────────────────
    # Init
    # ──────────────────────────────────────────────────────────────
    def __init__(self):
        super().__init__()

        self.title("🛠️ Build + Backup Combo Tool")
        self.resizable(True, True)
        self.update_idletasks()
        try:
            import ctypes
            monitor = ctypes.windll.user32.MonitorFromWindow(0, 1)  # MONITOR_DEFAULTTOPRIMARY
            info = ctypes.create_string_buffer(40)
            ctypes.c_uint32.from_buffer(info, 0).value = 40          # cbSize
            ctypes.windll.user32.GetMonitorInfoW(monitor, info)
            # work area is bytes 20-35: left, top, right, bottom (4×int32)
            import struct
            work_left, work_top, work_right, work_bottom = struct.unpack_from("4i", info, 20)
            usable_h = work_bottom - work_top
        except Exception:
            usable_h = self.winfo_screenheight() - 72
        self.geometry(f"1020x{usable_h}+0+0")

        if getattr(sys, 'frozen', False):
            self.app_dir = os.path.dirname(sys.executable)
        else:
            self.app_dir = os.path.dirname(os.path.abspath(__file__))
        self.settings_file = os.path.join(self.app_dir, "build_backup_cfg.json")

        # ── shared
        self.project_dir = ctk.StringVar(value=os.path.dirname(self.app_dir))

        # ── spec / requirements tab vars
        self.entry_script = ctk.StringVar(value="")
        self.app_name     = ctk.StringVar(value="")
        self.build_mode   = ctk.StringVar(value="Onefile")
        self.windowed     = ctk.BooleanVar(value=True)

        # ── build tab vars
        self.spec_file      = ctk.StringVar(value="")
        self.build_type     = ctk.StringVar(value="Auto (from spec)")
        self.output_dir     = ctk.StringVar(value=os.path.join(os.path.dirname(self.app_dir), "Builds"))
        self.clean_build    = ctk.BooleanVar(value=True)
        self.console_mode   = ctk.BooleanVar(value=False)
        self.upx_compression = ctk.BooleanVar(value=False)
        self.icon_path      = ctk.StringVar(value="")

        # ── python executable (for subprocesses)
        _default_py = "" if getattr(sys, 'frozen', False) else sys.executable
        self.python_exe = ctk.StringVar(value=_default_py)

        # ── backup tab vars
        self.backup_dir        = ctk.StringVar(value=os.path.join(os.path.dirname(self.app_dir), "Backups"))
        self.include_venv      = ctk.BooleanVar(value=False)
        self.include_pycache   = ctk.BooleanVar(value=False)
        self.include_builds    = ctk.BooleanVar(value=False)
        self.backup_after_build = ctk.BooleanVar(value=True)

        # ── changelog
        self.change_description = ctk.StringVar(value="")

        # ── state
        self.is_working = False
        self.is_backing_up = False
        self._active_btn = None
        self._active_btn_orig = {}
        self.progress_dots = 0
        self.progress_animation_id = None
        self.log_queue = queue.Queue()
        self._session_log_lines = []
        self._build_log_file_path = None
        self._cancel_requested = False
        self._current_process = None
        self.stop_btn = None
        self._text_context_menu = None
        self._context_target_widget = None

        self.load_settings()
        self.create_widgets()
        self._setup_text_context_menu()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.process_log_queue()

        if not self.entry_script.get():
            self._auto_fill_entry_script()
        if not self.spec_file.get():
            self._auto_pick_spec()

    # ──────────────────────────────────────────────────────────────
    # Settings
    # ──────────────────────────────────────────────────────────────
    def load_settings(self):
        try:
            if not os.path.exists(self.settings_file):
                self.save_settings()
                return
            with open(self.settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            s = settings.get("combined_tool", {}) if isinstance(settings, dict) else {}
            if s.get("project_dir"):      self.project_dir.set(s["project_dir"])
            if s.get("entry_script"):     self.entry_script.set(s["entry_script"])
            if s.get("app_name"):         self.app_name.set(s["app_name"])
            saved_mode = s.get("build_mode")
            saved_type = s.get("build_type")
            if saved_mode in ("Onefile", "Onedir"):
                self.build_mode.set(saved_mode)
            if "windowed" in s:           self.windowed.set(bool(s["windowed"]))
            if s.get("spec_file"):        self.spec_file.set(s["spec_file"])
            if saved_type:                 self.build_type.set(saved_type)
            preferred_mode = self._sync_build_selection(preferred=saved_type or saved_mode, spec_path=self.spec_file.get())
            if preferred_mode in ("Onefile", "Onedir"):
                preferred_spec = self._find_spec_for_type(self.project_dir.get().strip(), preferred_mode)
                if preferred_spec:
                    self.spec_file.set(preferred_spec)
            if s.get("output_dir"):       self.output_dir.set(s["output_dir"])
            if "clean_build" in s:        self.clean_build.set(bool(s["clean_build"]))
            if "console_mode" in s:       self.console_mode.set(bool(s["console_mode"]))
            if "upx_compression" in s:    self.upx_compression.set(bool(s["upx_compression"]))
            if s.get("icon_path"):        self.icon_path.set(s["icon_path"])
            if s.get("python_exe"):        self.python_exe.set(s["python_exe"])
            if s.get("change_description"): self.change_description.set(s["change_description"])
            # backup
            b = settings.get("backup_tab", {})
            if b.get("backup_dir"):      self.backup_dir.set(b["backup_dir"])
            if "include_venv" in b:      self.include_venv.set(bool(b["include_venv"]))
            if "include_pycache" in b:   self.include_pycache.set(bool(b["include_pycache"]))
            if "include_builds" in b:    self.include_builds.set(bool(b["include_builds"]))
            if "backup_after_build" in b: self.backup_after_build.set(bool(b["backup_after_build"]))
        except Exception as exc:
            print(f"Error loading combined tool settings: {exc}")

    def save_settings(self):
        try:
            settings = {}
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            if not isinstance(settings, dict):
                settings = {}
            settings["combined_tool"] = {
                "project_dir":    self.project_dir.get(),
                "entry_script":   self.entry_script.get(),
                "app_name":       self.app_name.get(),
                "build_mode":     self.build_mode.get(),
                "windowed":       self.windowed.get(),
                "spec_file":      self.spec_file.get(),
                "build_type":     self.build_mode.get(),
                "output_dir":     self.output_dir.get(),
                "clean_build":    self.clean_build.get(),
                "console_mode":   self.console_mode.get(),
                "upx_compression": self.upx_compression.get(),
                "icon_path":      self.icon_path.get(),
                "python_exe":          self.python_exe.get(),
                "change_description":  self.change_description.get(),
            }
            settings["backup_tab"] = {
                "backup_dir":         self.backup_dir.get(),
                "include_venv":       self.include_venv.get(),
                "include_pycache":    self.include_pycache.get(),
                "include_builds":     self.include_builds.get(),
                "backup_after_build": self.backup_after_build.get(),
            }
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=4, ensure_ascii=False)
        except Exception as exc:
            print(f"Error saving combined tool settings: {exc}")

    # ──────────────────────────────────────────────────────────────
    # Widgets
    # ──────────────────────────────────────────────────────────────
    def create_widgets(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            main,
            text="🛠️ Build + Backup Combo Tool",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(pady=(0, 14))

        # ── shared project folder ──────────────────────────────────
        pf = ctk.CTkFrame(main)
        pf.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(pf, text="📁 Project Folder", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(12, 5)
        )
        pr = ctk.CTkFrame(pf, fg_color="transparent")
        pr.pack(fill="x", padx=15, pady=(0, 12))
        ctk.CTkEntry(pr, textvariable=self.project_dir, height=34).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(pr, text="Browse...", width=110, command=self.browse_project).pack(side="right")

        # ── python executable ─────────────────────────────────────
        pyf = ctk.CTkFrame(main)
        pyf.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(pyf, text="🐍 Python Executable (voor build)", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(12, 5)
        )
        pyr = ctk.CTkFrame(pyf, fg_color="transparent")
        pyr.pack(fill="x", padx=15, pady=(0, 12))
        self.python_exe_entry = ctk.CTkEntry(pyr, textvariable=self.python_exe, height=34,
                                             placeholder_text="Pad naar python.exe van je .venv")
        self.python_exe_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(pyr, text="Browse...", width=110, command=self.browse_python_exe).pack(side="right", padx=(0, 6))
        ctk.CTkButton(pyr, text="Auto", width=60, command=self._auto_detect_python).pack(side="right")

        # ── Build settings ─────────────────────────────────
        bs = ctk.CTkFrame(main)
        bs.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(bs, text="🔨 Build Instellingen", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(8, 4)
        )
        er = ctk.CTkFrame(bs, fg_color="transparent")
        er.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(er, text="Entry Script:", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(er, textvariable=self.entry_script, height=30).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ctk.CTkButton(er, text="Pick .py...", width=100, command=self.browse_entry_script).pack(side="right")
        anr = ctk.CTkFrame(bs, fg_color="transparent")
        anr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(anr, text="App Naam:", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(anr, textvariable=self.app_name, height=30,
                     placeholder_text="Auto vanuit scriptnaam").pack(side="left", fill="x", expand=True)
        sfr = ctk.CTkFrame(bs, fg_color="transparent")
        sfr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(sfr, text="Spec File:", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(sfr, textvariable=self.spec_file, height=30).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ctk.CTkButton(sfr, text="Pick...", width=70, command=self.browse_spec).pack(side="right", padx=(0, 6))
        ctk.CTkButton(sfr, text="Auto", width=55, command=self._auto_pick_spec).pack(side="right")
        bmr = ctk.CTkFrame(bs, fg_color="transparent")
        bmr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(bmr, text="Build Mode:", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(bmr, values=["Onefile", "Onedir"], variable=self.build_mode, width=140, command=lambda v: self._auto_pick_spec(v)).pack(side="left", padx=(0, 16))
        ctk.CTkCheckBox(bmr, text="Windowed (geen console)", variable=self.windowed).pack(side="left")
        ofr = ctk.CTkFrame(bs, fg_color="transparent")
        ofr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(ofr, text="Output Folder:", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(ofr, textvariable=self.output_dir, height=30).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ctk.CTkButton(ofr, text="Browse...", width=100, command=self.browse_output).pack(side="right")
        bor = ctk.CTkFrame(bs, fg_color="transparent")
        bor.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkCheckBox(bor, text="--clean", variable=self.clean_build).pack(side="left", padx=(0, 16))
        ctk.CTkCheckBox(bor, text="Console mode", variable=self.console_mode).pack(side="left", padx=(0, 16))
        ctk.CTkCheckBox(bor, text="UPX compressie", variable=self.upx_compression).pack(side="left")
        icr = ctk.CTkFrame(bs, fg_color="transparent")
        icr.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(icr, text="Icon (opt.):", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(icr, textvariable=self.icon_path, height=30).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ctk.CTkButton(icr, text="Browse...", width=100, command=self.browse_icon).pack(side="right")

        # ── Backup settings ─────────────────────────────────
        bks = ctk.CTkFrame(main)
        bks.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(bks, text="💾 Backup Instellingen", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(8, 4)
        )
        bkdr = ctk.CTkFrame(bks, fg_color="transparent")
        bkdr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(bkdr, text="Backup Folder:", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(bkdr, textvariable=self.backup_dir, height=30).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ctk.CTkButton(bkdr, text="Browse...", width=100, command=self.browse_backup_dir).pack(side="right")
        bkbr = ctk.CTkFrame(bks, fg_color="transparent")
        bkbr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(bkbr, text="Build Folder:", width=120, anchor="w").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(bkbr, textvariable=self.output_dir, height=30).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ctk.CTkButton(bkbr, text="Browse...", width=100, command=self.browse_output).pack(side="right")
        bkopt = ctk.CTkFrame(bks, fg_color="transparent")
        bkopt.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkCheckBox(bkopt, text="Include .venv", variable=self.include_venv).pack(side="left", padx=(0, 14))
        ctk.CTkCheckBox(bkopt, text="Include __pycache__", variable=self.include_pycache).pack(side="left", padx=(0, 14))
        ctk.CTkCheckBox(bkopt, text="Include builds", variable=self.include_builds).pack(side="left", padx=(0, 14))
        ctk.CTkCheckBox(bkopt, text="Auto backup na build", variable=self.backup_after_build).pack(side="left")

        # ── Changelog description ───────────────────────────
        chg = ctk.CTkFrame(main)
        chg.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(chg, text="📝 Wat is er veranderd?", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(8, 4)
        )
        self.change_entry = ctk.CTkEntry(
            chg,
            textvariable=self.change_description,
            height=32,
            placeholder_text="bv. nieuw scherm toegevoegd, bug gefixed...",
        )
        self.change_entry.pack(fill="x", padx=12, pady=(0, 8))

        # ── Action buttons ──────────────────────────────────
        btn_row = ctk.CTkFrame(main, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 4))
        self.build_btn = ctk.CTkButton(
            btn_row,
            text="🚀 Bouwen",
            height=46,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self.start_build,
        )
        self.build_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.backup_btn = ctk.CTkButton(
            btn_row,
            text="💾 Backup",
            height=46,
            font=ctk.CTkFont(size=13, weight="bold"),
            width=120,
            command=self.start_backup,
        )
        self.backup_btn.pack(side="left", padx=(0, 6))
        self.venv_btn = ctk.CTkButton(
            btn_row,
            text="♻️ Venv",
            height=46,
            font=ctk.CTkFont(size=13, weight="bold"),
            width=100,
            command=self.recreate_venv,
        )
        self.venv_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ctk.CTkButton(
            btn_row,
            text="⛔ Stop",
            height=46,
            font=ctk.CTkFont(size=13, weight="bold"),
            width=90,
            state="disabled",
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._request_cancel,
        )
        self.stop_btn.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            btn_row,
            text="📁 Output",
            height=46,
            width=90,
            command=self.open_output,
        ).pack(side="left")

        self.status_label = ctk.CTkLabel(main, text="Ready", text_color="gray")
        self.status_label.pack(pady=(4, 2))

        # ── Log ─────────────────────────────────────────────
        log_frame = ctk.CTkFrame(main)
        log_frame.pack(fill="both", expand=True)
        hdr = ctk.CTkFrame(log_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(hdr, text="📋 Log", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="Clear", width=90, command=self.clear_log).pack(side="right")
        self.log_text = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(size=10, family="Consolas"), height=160)
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # ── Spec + Requirements tab ────────────────────────────────────
    def _build_spec_tab(self, parent):
        ef = ctk.CTkFrame(parent)
        ef.pack(fill="x", pady=(10, 8))
        ctk.CTkLabel(ef, text="🐍 Entry Script", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        row = ctk.CTkFrame(ef, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkEntry(row, textvariable=self.entry_script, height=32).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(row, text="Pick .py...", width=110, command=self.browse_entry_script).pack(side="right")

        af = ctk.CTkFrame(parent)
        af.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(af, text="🏷️ App Name", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkEntry(
            af,
            textvariable=self.app_name,
            height=32,
            placeholder_text="Auto from script name if empty",
        ).pack(fill="x", padx=12, pady=(0, 10), expand=True)

        opt = ctk.CTkFrame(parent)
        opt.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(opt, text="⚙️ Spec Options", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 6)
        )
        mr = ctk.CTkFrame(opt, fg_color="transparent")
        mr.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(mr, text="Build Mode:").pack(side="left", padx=(0, 8))
        ctk.CTkOptionMenu(mr, values=["Onefile", "Onedir"], variable=self.build_mode, width=140).pack(side="left")
        ctk.CTkCheckBox(opt, text="Windowed app (no console)", variable=self.windowed).pack(
            anchor="w", padx=12, pady=(0, 10)
        )

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=0, pady=(0, 8))
        self.req_btn = ctk.CTkButton(
            btn_row,
            text="📦 Generate requirements.txt",
            height=38,
            command=self.generate_requirements,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.req_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.spec_btn = ctk.CTkButton(
            btn_row,
            text="🧩 Generate .spec",
            height=38,
            command=self.generate_spec,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.spec_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.venv_btn = ctk.CTkButton(
            btn_row,
            text="♻️ Recreate .venv",
            height=38,
            command=self.recreate_venv,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.venv_btn.pack(side="left", fill="x", expand=True)

    # ── Backup tab ────────────────────────────────────────────────
    def _build_backup_tab(self, parent):
        bd = ctk.CTkFrame(parent)
        bd.pack(fill="x", pady=(10, 8))
        ctk.CTkLabel(bd, text="📦 Backup Folder", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        row = ctk.CTkFrame(bd, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkEntry(row, textvariable=self.backup_dir, height=32).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(row, text="Browse...", width=110, command=self.browse_backup_dir).pack(side="right")

        opt = ctk.CTkFrame(parent)
        opt.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(opt, text="⚙️ Include in Backup", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkCheckBox(opt, text="Include .venv folder", variable=self.include_venv).pack(
            anchor="w", padx=12, pady=2
        )
        ctk.CTkCheckBox(opt, text="Include __pycache__ folders", variable=self.include_pycache).pack(
            anchor="w", padx=12, pady=2
        )
        ctk.CTkCheckBox(opt, text="Include build artifacts (build/ dist/ Builds/)", variable=self.include_builds).pack(
            anchor="w", padx=12, pady=(2, 10)
        )

        self.backup_btn = ctk.CTkButton(
            parent,
            text="💾 Create Backup ZIP",
            height=44,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.start_backup,
        )
        self.backup_btn.pack(fill="x", padx=0, pady=(4, 4))

    # ── Build tab ─────────────────────────────────────────────────
    def _build_build_tab(self, parent):
        sf = ctk.CTkFrame(parent)
        sf.pack(fill="x", pady=(10, 8))
        ctk.CTkLabel(sf, text="🧩 Spec File", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        row = ctk.CTkFrame(sf, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkEntry(row, textvariable=self.spec_file, height=32).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(row, text="Pick...", width=90, command=self.browse_spec).pack(side="right", padx=(0, 6))
        ctk.CTkButton(row, text="Auto", width=70, command=self._auto_pick_spec).pack(side="right")

        tr = ctk.CTkFrame(sf, fg_color="transparent")
        tr.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(tr, text="Build Type:").pack(side="left", padx=(0, 8))
        ctk.CTkOptionMenu(
            tr,
            values=["Auto (from spec)", "Onefile", "Onedir"],
            variable=self.build_type,
            width=180,
            command=self._on_build_type_change,
        ).pack(side="left")

        of = ctk.CTkFrame(parent)
        of.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(of, text="📦 Output Folder", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        row = ctk.CTkFrame(of, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkEntry(row, textvariable=self.output_dir, height=32).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(row, text="Browse...", width=110, command=self.browse_output).pack(side="right")

        opt = ctk.CTkFrame(parent)
        opt.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(opt, text="⚙️ Build Options", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkCheckBox(opt, text="Clean build (--clean)", variable=self.clean_build).pack(
            anchor="w", padx=12, pady=2
        )
        ctk.CTkCheckBox(opt, text="Console mode (spec override)", variable=self.console_mode).pack(
            anchor="w", padx=12, pady=2
        )
        ctk.CTkCheckBox(opt, text="UPX compression (spec override)", variable=self.upx_compression).pack(
            anchor="w", padx=12, pady=2
        )
        ir = ctk.CTkFrame(opt, fg_color="transparent")
        ir.pack(fill="x", padx=12, pady=(6, 10))
        ctk.CTkLabel(ir, text="Icon (optional):").pack(side="left", padx=(0, 8))
        ctk.CTkEntry(ir, textvariable=self.icon_path, height=30).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(ir, text="Browse...", width=90, command=self.browse_icon).pack(side="right")

        self.build_btn = ctk.CTkButton(
            parent,
            text="🔨 Build Only",
            height=38,
            command=self.start_build,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.build_btn.pack(fill="x", padx=0, pady=(0, 4))

    # ──────────────────────────────────────────────────────────────
    # Browse helpers
    # ──────────────────────────────────────────────────────────────
    def browse_project(self):
        d = filedialog.askdirectory(title="Select Project Folder", initialdir=self.project_dir.get())
        if d:
            self.project_dir.set(d)
            self._auto_fill_entry_script()
            self._auto_pick_spec()
            self.save_settings()

    def browse_entry_script(self):
        f = filedialog.askopenfilename(
            title="Select Entry Script",
            initialdir=self.project_dir.get(),
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if f:
            self.entry_script.set(f)
            if not self.app_name.get().strip():
                self.app_name.set(os.path.splitext(os.path.basename(f))[0])
            self.save_settings()

    def browse_spec(self):
        f = filedialog.askopenfilename(
            title="Select PyInstaller Spec",
            initialdir=self.project_dir.get(),
            filetypes=[("PyInstaller spec", "*.spec"), ("All files", "*.*")],
        )
        if f:
            self.spec_file.set(f)
            self._sync_build_selection(spec_path=f)
            self.save_settings()

    def browse_output(self):
        d = filedialog.askdirectory(title="Select Output Folder", initialdir=self.output_dir.get())
        if d:
            self.output_dir.set(d)

    def browse_icon(self):
        f = filedialog.askopenfilename(
            title="Select Icon File",
            filetypes=[("Icon files", "*.ico"), ("All files", "*.*")],
            initialdir=self.project_dir.get(),
        )
        if f:
            self.icon_path.set(f)

    def browse_backup_dir(self):
        d = filedialog.askdirectory(title="Select Backup Folder", initialdir=self.backup_dir.get())
        if d:
            self.backup_dir.set(d)
            self.save_settings()

    def browse_python_exe(self):
        f = filedialog.askopenfilename(
            title="Select python.exe",
            filetypes=[("Python executable", "python.exe python3.exe"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.python_exe.get()) if self.python_exe.get() else self.app_dir,
        )
        if f:
            self.python_exe.set(f)
            self.save_settings()

    def _auto_detect_python(self):
        """Try to find python.exe in a .venv next to the project folder."""
        project = self.project_dir.get().strip()
        candidates = [
            os.path.join(self.app_dir, ".venv", "Scripts", "python.exe"),
            os.path.join(project, ".venv", "Scripts", "python.exe"),
        ]
        if not getattr(sys, 'frozen', False):
            candidates.insert(0, sys.executable)
        for c in candidates:
            if os.path.isfile(c):
                self.python_exe.set(c)
                self.save_settings()
                self.log(f"✅ Python auto-detected: {c}")
                return
        messagebox.showwarning("Not found", "Geen python.exe gevonden. Stel handmatig in.")

    # ──────────────────────────────────────────────────────────────
    # Python executable helper
    # ──────────────────────────────────────────────────────────────
    def _get_python(self) -> str:
        """Return the configured python executable, fallback to sys.executable or 'python'."""
        p = self.python_exe.get().strip()
        if p and os.path.isfile(p):
            return p
        if not getattr(sys, 'frozen', False):
            return sys.executable
        # frozen but no valid python_exe configured → warn user
        self.log("⚠️  Geen geldig Python pad ingesteld! Stel de Python Executable in boven de tabs.")
        return "python"

    # ──────────────────────────────────────────────────────────────
    # Auto-fill helpers
    # ──────────────────────────────────────────────────────────────
    def _auto_fill_entry_script(self):
        project = self.project_dir.get().strip()
        if not project or not os.path.isdir(project):
            return
        if self.entry_script.get().strip() and os.path.isfile(self.entry_script.get().strip()):
            return
        for name in ("main.py", "app.py", "run.py", "__main__.py"):
            path = os.path.join(project, name)
            if os.path.isfile(path):
                self.entry_script.set(path)
                if not self.app_name.get().strip():
                    self.app_name.set(os.path.splitext(name)[0])
                return

    def _infer_build_mode_from_spec(self, spec_path: str):
        if not spec_path or not os.path.isfile(spec_path):
            return None
        low = os.path.basename(spec_path).lower()
        if any(token in low for token in ("onedir", "onefolder", "one-folder")):
            return "Onedir"
        if any(token in low for token in ("onefile", "portable")):
            return "Onefile"
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec_text = f.read()
            if re.search(r"(?m)^\s*coll\s*=\s*COLLECT\s*\(", spec_text) or "exclude_binaries=True" in spec_text:
                return "Onedir"
            return "Onefile"
        except Exception:
            return None

    def _sync_build_selection(self, preferred=None, spec_path=None):
        mode = preferred if preferred in ("Onefile", "Onedir") else None
        if mode is None and self.build_mode.get() in ("Onefile", "Onedir"):
            mode = self.build_mode.get()
        if mode is None and self.build_type.get() in ("Onefile", "Onedir"):
            mode = self.build_type.get()
        if mode is None:
            candidate_spec = spec_path or self.spec_file.get().strip()
            mode = self._infer_build_mode_from_spec(candidate_spec)
        if mode in ("Onefile", "Onedir"):
            self.build_mode.set(mode)
            self.build_type.set(mode)
        return mode

    def _find_spec_for_type(self, project: str, build_type: str):
        if not project or not os.path.isdir(project):
            return None
        specs = sorted(x for x in os.listdir(project) if x.lower().endswith(".spec"))
        if not specs:
            return None

        def pick_with(keywords, avoid=()):
            for name in specs:
                low = name.lower()
                if any(k in low for k in keywords) and not any(a in low for a in avoid):
                    return os.path.join(project, name)
            return None

        if build_type == "Onedir":
            p = pick_with(["onedir", "onefolder", "one-folder"])
            if p:
                return p
        elif build_type == "Onefile":
            p = pick_with(["onefile", "portable"], ["onedir", "onefolder", "one-folder"])
            if p:
                return p
        p = pick_with([".spec"], ["onedir", "onefolder", "one-folder"])
        return p or os.path.join(project, specs[0])

    def _auto_pick_spec(self, build_type=None):
        project = self.project_dir.get().strip()
        requested = self._sync_build_selection(preferred=build_type)
        pick = self._find_spec_for_type(project, requested or self.build_mode.get())
        if pick:
            self.spec_file.set(pick)
            self._sync_build_selection(spec_path=pick)

    def _on_build_type_change(self, value):
        self._sync_build_selection(preferred=value)
        self._auto_pick_spec(value)

    # ──────────────────────────────────────────────────────────────
    # Log
    # ──────────────────────────────────────────────────────────────
    def log(self, message):
        self.log_queue.put(message)

    def process_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                ts = datetime.now().strftime("%H:%M:%S")
                line = f"[{ts}] {msg}\n"
                self.log_text.insert("end", line)
                self.log_text.see("end")
                self._session_log_lines.append(line)
                if self._build_log_file_path:
                    try:
                        with open(self._build_log_file_path, "a", encoding="utf-8") as f:
                            f.write(line)
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self.after(100, self.process_log_queue)

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def _setup_text_context_menu(self):
        """Attach one reusable right-click menu to all text input widgets."""
        self._text_context_menu = Menu(self, tearoff=0)
        self._text_context_menu.add_command(label="Undo", command=lambda: self._run_text_action("undo"))
        self._text_context_menu.add_command(label="Redo", command=lambda: self._run_text_action("redo"))
        self._text_context_menu.add_separator()
        self._text_context_menu.add_command(label="Cut", command=lambda: self._run_text_action("cut"))
        self._text_context_menu.add_command(label="Copy", command=lambda: self._run_text_action("copy"))
        self._text_context_menu.add_command(label="Paste", command=lambda: self._run_text_action("paste"))
        self._text_context_menu.add_command(label="Select All", command=lambda: self._run_text_action("select_all"))
        self._text_context_menu.add_separator()
        self._text_context_menu.add_command(label="Clear Textbox", command=lambda: self._run_text_action("clear"))

        # Global fallback binds.
        for cls_name in ("Entry", "TEntry", "Text"):
            self.bind_class(cls_name, "<Button-3>", self._show_text_context_menu, add="+")
            self.bind_class(cls_name, "<ButtonRelease-3>", self._show_text_context_menu, add="+")
            self.bind_class(cls_name, "<Control-Button-1>", self._show_text_context_menu, add="+")

        # Direct binds on CTk internal widgets are the most reliable in frozen builds.
        self.after(100, self._bind_context_menu_to_text_inputs)

    def _bind_context_menu_to_text_inputs(self):
        """Bind right-click menu to all currently mounted CTk entry/text widgets."""
        for ctk_widget in self._iter_ctk_text_widgets(self):
            inner = getattr(ctk_widget, "_entry", None) or getattr(ctk_widget, "_textbox", None)
            target = inner or ctk_widget
            for sequence in ("<Button-3>", "<ButtonRelease-3>", "<Control-Button-1>"):
                target.bind(sequence, self._show_text_context_menu, add="+")

    def _iter_ctk_text_widgets(self, parent):
        for child in parent.winfo_children():
            if isinstance(child, (ctk.CTkEntry, ctk.CTkTextbox)):
                yield child
            yield from self._iter_ctk_text_widgets(child)

    def _show_text_context_menu(self, event):
        widget = event.widget
        self._context_target_widget = widget
        try:
            self._text_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._text_context_menu.grab_release()
        return "break"

    def _run_text_action(self, action):
        widget = self._context_target_widget
        if widget is None:
            return
        try:
            widget.focus_set()
            if action == "undo":
                widget.event_generate("<<Undo>>")
            elif action == "redo":
                widget.event_generate("<<Redo>>")
            elif action == "cut":
                widget.event_generate("<<Cut>>")
            elif action == "copy":
                widget.event_generate("<<Copy>>")
            elif action == "paste":
                widget.event_generate("<<Paste>>")
            elif action == "select_all":
                if widget.winfo_class() == "Entry":
                    widget.selection_range(0, "end")
                    widget.icursor("end")
                else:
                    widget.tag_add("sel", "1.0", "end")
                    widget.mark_set("insert", "end")
            elif action == "clear":
                if widget.winfo_class() == "Entry":
                    widget.delete(0, "end")
                else:
                    widget.delete("1.0", "end")
        except Exception:
            # Ignore no-op actions on read-only widgets or unsupported states.
            pass

    def _set_status(self, text):
        self.status_label.configure(text=text)

    # ──────────────────────────────────────────────────────────────
    # Active-button highlight helpers
    # ──────────────────────────────────────────────────────────────
    def _highlight_active_btn(self, btn):
        """Colour a button yellow with black text to show it is the running task."""
        if btn is None:
            return
        self._active_btn = btn
        self._active_btn_orig = {
            "fg_color":    btn.cget("fg_color"),
            "text_color":  btn.cget("text_color"),
            "hover_color": btn.cget("hover_color"),
        }
        btn.configure(fg_color="#FFD700", text_color="#000000", hover_color="#FFC200")

    def _restore_active_btn(self):
        """Restore the previously highlighted button to its original colours."""
        if self._active_btn is None:
            return
        self._active_btn.configure(
            fg_color=self._active_btn_orig.get("fg_color"),
            text_color=self._active_btn_orig.get("text_color"),
            hover_color=self._active_btn_orig.get("hover_color"),
        )
        self._active_btn = None
        self._active_btn_orig = {}

    # ──────────────────────────────────────────────────────────────
    # Busy state
    # ──────────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool, label: str = "", active_btn=None):
        self.is_working = busy
        state = "disabled" if busy else "normal"
        for btn in (self.build_btn, self.backup_btn, self.venv_btn):
            if btn is None:
                continue
            if busy and active_btn and btn is active_btn:
                # Keep active button visually enabled so the yellow stands out
                self._highlight_active_btn(btn)
            else:
                btn.configure(state=state)
        if self.stop_btn is not None:
            self.stop_btn.configure(state="normal" if busy else "disabled")
        if busy and label:
            self.build_btn.configure(text=label)
        elif not busy:
            self.build_btn.configure(text="🚀 Bouwen")
            self._restore_active_btn()

    def _animate_auto(self):
        if not self.is_working:
            return
        base = "🚀 Running"
        dots = "." * (self.progress_dots % 4)
        self.build_btn.configure(text=f"{base}{dots:<3}")
        self.progress_dots += 1
        self.progress_animation_id = self.after(400, self._animate_auto)

    def _stop_animation(self):
        if self.progress_animation_id:
            self.after_cancel(self.progress_animation_id)
            self.progress_animation_id = None

    # ──────────────────────────────────────────────────────────────
    # Validate
    # ──────────────────────────────────────────────────────────────
    def _validate_project(self):
        project = self.project_dir.get().strip()
        if not project or not os.path.isdir(project):
            messagebox.showerror("Error", "Project folder is invalid.")
            return None
        return project

    # ──────────────────────────────────────────────────────────────
    # Requirements generation
    # ──────────────────────────────────────────────────────────────
    def generate_requirements(self):
        if self.is_working:
            return
        project = self._validate_project()
        if not project:
            return
        self.save_settings()
        self._set_busy(True, active_btn=self.build_btn)
        self._set_status("Generating requirements...")
        Thread(target=self._requirements_worker, args=(project,), daemon=True).start()

    def _requirements_worker(self, project):
        try:
            req_path = os.path.join(project, "requirements.txt")
            cmd = [self._get_python(), "-m", "pip", "freeze"]
            self.log(f"Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, cwd=project, capture_output=True, text=True, check=False,
                                    stdin=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "pip freeze failed")
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(result.stdout.strip() + "\n")
            self.after(0, lambda: self._requirements_done(req_path))
        except Exception as exc:
            _msg = f"Requirements generation failed: {exc}"
            self.after(0, lambda m=_msg: self._job_error(m))

    def _requirements_done(self, req_path, callback=None):
        self._set_busy(False)
        self._set_status("Requirements generated")
        self.log(f"✅ Created: {req_path}")
        if callback:
            callback()
        else:
            messagebox.showinfo("Done", f"requirements.txt created:\n{req_path}")

    # ──────────────────────────────────────────────────────────────
    # Spec generation
    # ──────────────────────────────────────────────────────────────
    def generate_spec(self, callback=None):
        if self.is_working and callback is None:
            return
        project = self._validate_project()
        if not project:
            return

        entry = self.entry_script.get().strip()
        if not entry or not os.path.isfile(entry):
            messagebox.showerror("Error", "Entry script is invalid.")
            return

        app_name = self.app_name.get().strip() or os.path.splitext(os.path.basename(entry))[0]
        self.app_name.set(app_name)
        self.save_settings()

        if callback is None:
            self._set_busy(True, active_btn=self.build_btn)
        self._set_status("Generating spec...")
        Thread(target=self._spec_worker, args=(project, entry, app_name, callback), daemon=True).start()

    def _spec_worker(self, project, entry, app_name, callback):
        try:
            # Spec generation needs PyInstaller, which lives in the BUILD TOOL's venv,
            # not necessarily in the project's venv. When running from source,
            # sys.executable is the build tool's Python. Only use the configured
            # python_exe when running frozen (the build tool is a .exe itself).
            if getattr(sys, "frozen", False):
                spec_python = self._get_python()
            else:
                spec_python = sys.executable

            scripts_dir = os.path.dirname(spec_python)
            pyi_makespec = os.path.join(scripts_dir, "pyi-makespec.exe")
            if not os.path.isfile(pyi_makespec):
                pyi_makespec = os.path.join(scripts_dir, "pyi-makespec")

            mode_flag = "--onefile" if self.build_mode.get() == "Onefile" else "--onedir"
            if os.path.isfile(pyi_makespec):
                cmd = [pyi_makespec]
            else:
                cmd = [spec_python, "-m", "PyInstaller", "--noconfirm"]
            cmd += [
                mode_flag,
                "--name", app_name,
                "--specpath", project,
            ]
            if self.windowed.get():
                cmd.append("--windowed")
            cmd.append(entry)

            self.log(f"Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, cwd=project, capture_output=True, text=True, check=False,
                                    stdin=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            # always log output so failures are visible
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            for line in output.splitlines()[-20:]:
                if line.strip():
                    self.log(line)
            if result.returncode != 0:
                raise RuntimeError(output.splitlines()[-1] if output else "pyi-makespec failed (no output)")

            default_spec = os.path.join(project, f"{app_name}.spec")
            mode_suffix = "onefile" if self.build_mode.get() == "Onefile" else "onedir"
            target_spec = os.path.join(project, f"{app_name}.{mode_suffix}.spec")
            if os.path.abspath(default_spec) != os.path.abspath(target_spec):
                if os.path.isfile(target_spec):
                    os.remove(target_spec)
                if os.path.isfile(default_spec):
                    os.replace(default_spec, target_spec)
            spec_path = target_spec if os.path.isfile(target_spec) else default_spec
            self.after(0, lambda: self._spec_done(spec_path, callback))
        except Exception as exc:
            _msg = f"Spec generation failed: {exc}"
            self.after(0, lambda m=_msg: self._job_error(m))

    def _spec_done(self, spec_path, callback=None):
        self.log(f"✅ Spec created: {spec_path}")
        self.spec_file.set(spec_path)
        self._set_status("Spec generated")
        if callback:
            callback(spec_path)
        else:
            self._set_busy(False)
            messagebox.showinfo("Done", f"Spec file created:\n{spec_path}")

    # ──────────────────────────────────────────────────────────────
    # Recreate venv
    # ──────────────────────────────────────────────────────────────
    def recreate_venv(self):
        if self.is_working:
            return
        script = os.path.join(self.app_dir, "recreate_venv.bat")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return
        if not messagebox.askyesno(
            "Recreate .venv",
            "This opens a terminal, runs recreate_venv.bat, and closes this GUI.\n\nContinue?",
        ):
            return
        self.save_settings()
        try:
            subprocess.Popen(f'start "Recreate Venv" cmd /k "\"{script}\" /y"', cwd=self.app_dir, shell=True)
        except Exception as exc:
            messagebox.showerror("Error", f"Could not start recreate_venv.bat:\n{exc}")
            return
        messagebox.showinfo("Started", "Recreate venv started in a new terminal window.")
        self.destroy()

    # ──────────────────────────────────────────────────────────────
    # Build (standalone)
    # ──────────────────────────────────────────────────────────────
    def start_build(self):
        if self.is_working:
            messagebox.showwarning("Bezig", "Er loopt al een taak.")
            return
        project = self._validate_project()
        if not project:
            return

        entry = self.entry_script.get().strip()
        if not entry or not os.path.isfile(entry):
            messagebox.showerror("Error", "Entry script is niet geldig.")
            return
        if not self.output_dir.get().strip():
            messagebox.showerror("Error", "Output folder is vereist.")
            return

        self._cancel_requested = False
        self.save_settings()
        self._session_log_lines = []
        self._build_log_file_path = None
        self.clear_log()
        self._set_busy(True, active_btn=self.build_btn)
        self._set_status("Stap 1/3: Requirements genereren...")
        self.progress_dots = 0
        self._animate_auto()
        self.log("▶ Stap 1/3: requirements.txt genereren...")
        Thread(target=self._auto_chain_requirements, args=(project,), daemon=True).start()

    # ──────────────────────────────────────────────────────────────
    # Auto: Spec → Build (chained)
    # ──────────────────────────────────────────────────────────────
    def auto_spec_and_build(self):
        self.start_build()

    def _auto_chain_requirements(self, project):
        if self._cancel_requested:
            return
        try:
            req_path = os.path.join(project, "requirements.txt")
            result = subprocess.run(
                [self._get_python(), "-m", "pip", "freeze"],
                cwd=project, capture_output=True, text=True, check=False,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "pip freeze failed")
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(result.stdout.strip() + "\n")
            self.log(f"✅ requirements.txt: {req_path}")
            self.after(0, lambda: self._auto_chain_spec(project))
        except Exception as exc:
            _msg = f"Requirements generation failed: {exc}"
            self.after(0, lambda m=_msg: self._job_error(m))

    def _auto_chain_spec(self, project):
        if self._cancel_requested:
            return

        requested_mode = self._sync_build_selection()
        existing_spec = self.spec_file.get().strip()

        if requested_mode in ("Onefile", "Onedir"):
            matching_spec = self._find_spec_for_type(project, requested_mode)
            if matching_spec and os.path.isfile(matching_spec):
                if os.path.abspath(existing_spec) != os.path.abspath(matching_spec):
                    self.log(f"ℹ️ Spec aangepast naar gevraagde buildmodus {requested_mode}: {matching_spec}")
                    self.spec_file.set(matching_spec)
                existing_spec = matching_spec

        if existing_spec and os.path.isfile(existing_spec):
            inferred_mode = self._infer_build_mode_from_spec(existing_spec)
            if requested_mode in ("Onefile", "Onedir") and inferred_mode and inferred_mode != requested_mode:
                self.log(f"ℹ️ Bestaand spec is {inferred_mode}; nieuw {requested_mode} spec wordt gegenereerd.")
            else:
                self.log(f"ℹ️ Stap 2/3: Bestaand spec bestand gebruikt: {existing_spec}")
                self.after(0, lambda: self._auto_chain_build_start(existing_spec))
                return

        self._set_status("Stap 2/3: Spec genereren...")
        self.log("▶ Stap 2/3: .spec bestand genereren...")
        entry = self.entry_script.get().strip()
        app_name = self.app_name.get().strip() or os.path.splitext(os.path.basename(entry))[0]
        self.app_name.set(app_name)
        Thread(target=self._spec_worker, args=(project, entry, app_name, self._auto_chain_build_start), daemon=True).start()

    def _auto_chain_build_start(self, spec_path):
        if self._cancel_requested:
            return
        self._set_status("Stap 3/3: Bouwen met PyInstaller...")
        self.log("▶ Stap 3/3: PyInstaller bouwen...")
        project = self.project_dir.get().strip()
        output = self.output_dir.get().strip()
        Thread(target=self._build_worker, args=(project, spec_path, output), daemon=True).start()

    # ──────────────────────────────────────────────────────────────
    # Build worker
    # ──────────────────────────────────────────────────────────────
    def _apply_spec_overrides(self, spec_path):
        with open(spec_path, "r", encoding="utf-8") as f:
            original = f.read()
        updated = original

        requested_upx = self.upx_compression.get()
        upx_path = shutil.which("upx")
        effective_upx = requested_upx and bool(upx_path)
        if requested_upx and not upx_path:
            self.log("⚠️ UPX requested but not found in PATH. Skipping UPX.")
        elif effective_upx:
            self.log(f"✅ UPX found: {upx_path}")

        updated, n_upx = re.subn(
            r"(?m)^\s*upx\s*=\s*(True|False)\s*,\s*$",
            f"    upx={effective_upx},",
            updated, count=1,
        )
        updated, n_console = re.subn(
            r"(?m)^\s*console\s*=\s*(True|False)\s*,\s*$",
            f"    console={self.console_mode.get()},",
            updated, count=1,
        )
        icon_file = self.icon_path.get().strip()
        has_icon = bool(icon_file and os.path.isfile(icon_file))
        icon_re = r"(?m)^\s*icon\s*=.*?,\s*$"
        if has_icon:
            icon_line = f"    icon={repr(icon_file)},"
            if re.search(icon_re, updated):
                updated = re.sub(icon_re, icon_line, updated, count=1)
            else:
                updated = re.sub(
                    r"(?m)^(\s*name\s*=\s*'.*?'\s*,\s*)$",
                    r"\1\n" + icon_line,
                    updated, count=1,
                )
        else:
            updated = re.sub(icon_re + r"\n?", "", updated, count=1)

        self._effective_upx = effective_upx
        self._effective_console = self.console_mode.get()
        self._effective_icon = icon_file if has_icon else "None"

        if n_upx == 0:
            self.log("ℹ️ No 'upx=' field found in spec; UPX override skipped.")
        if n_console == 0:
            self.log("ℹ️ No 'console=' field found in spec; console override skipped.")

        # ── inject collect_submodules for all local packages in the project ──
        try:
            project_dir = os.path.dirname(spec_path)
            entry_dirs = [project_dir]
            for sub in ("src",):
                cand = os.path.join(project_dir, sub)
                if os.path.isdir(cand):
                    entry_dirs.append(cand)
            for m in re.findall(r"pathex\s*=\s*\[([^\]]*)\]", updated):
                for p in re.findall(r"['\"]([^'\"]+)['\"]", m):
                    if os.path.isdir(p) and p not in entry_dirs:
                        entry_dirs.append(p)

            # ── inject pathex for the entry script directory ──
            try:
                entry_match = re.search(r"Analysis\s*\(\s*\[\s*['\"]([^'\"]+)['\"]", updated)
                if entry_match:
                    entry_script_dir = os.path.dirname(entry_match.group(1)).replace("\\", "/")
                    if entry_script_dir and os.path.isdir(entry_script_dir):
                        if entry_script_dir not in entry_dirs:
                            entry_dirs.append(entry_script_dir)
                        # patch pathex=[] in spec if the entry dir is not already listed
                        current_pathex = re.search(r"pathex\s*=\s*\[([^\]]*)\]", updated)
                        current_paths = re.findall(r"['\"]([^'\"]+)['\"]", current_pathex.group(1)) if current_pathex else []
                        if entry_script_dir not in current_paths:
                            new_entry = repr(entry_script_dir)
                            if current_paths:
                                updated = re.sub(
                                    r"(pathex\s*=\s*\[)",
                                    rf"\g<1>{new_entry}, ",
                                    updated, count=1,
                                )
                            else:
                                updated = re.sub(
                                    r"pathex\s*=\s*\[\s*\]",
                                    f"pathex=[{new_entry}]",
                                    updated, count=1,
                                )
                            self.log(f"[INFO] pathex uitgebreid met entry script map: {entry_script_dir}")
            except Exception as e:
                self.log(f"⚠️ pathex injectie mislukt: {e}")

            local_packages = []
            local_module_folders = []
            for entry_dir in entry_dirs:
                for item in sorted(os.listdir(entry_dir)):
                    item_path = os.path.join(entry_dir, item)
                    if not os.path.isdir(item_path):
                        continue
                    # Keep only valid Python module/package names.
                    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", item):
                        continue

                    # Classic package (with __init__.py)
                    if os.path.isfile(os.path.join(item_path, "__init__.py")):
                        if item not in local_packages:
                            local_packages.append(item)
                        continue

                    # Namespace/module-like folder: include when it contains Python files.
                    try:
                        has_py_files = any(name.endswith(".py") for name in os.listdir(item_path))
                    except Exception:
                        has_py_files = False
                    if has_py_files and item not in local_module_folders:
                        local_module_folders.append(item)

            all_collect_targets = []
            for name in local_packages + local_module_folders:
                if name not in all_collect_targets:
                    all_collect_targets.append(name)

            explicit_hiddenimports = []
            seen_explicit = set()

            def _add_explicit(module_name: str):
                if not module_name:
                    return
                if module_name in seen_explicit:
                    return
                seen_explicit.add(module_name)
                explicit_hiddenimports.append(module_name)

            # Discover explicit module paths from filesystem to avoid runtime misses
            # when collect_submodules does not fully resolve namespace-like layouts.
            for entry_dir in entry_dirs:
                for root, dirs, files in os.walk(entry_dir):
                    dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
                    for fname in files:
                        if not fname.endswith(".py"):
                            continue
                        if fname.startswith("."):
                            continue
                        full = os.path.join(root, fname)
                        rel = os.path.relpath(full, entry_dir)
                        rel_no_ext = rel[:-3] if rel.lower().endswith(".py") else rel
                        parts = [p for p in rel_no_ext.replace("\\", "/").split("/") if p]
                        if not parts:
                            continue
                        if parts[-1] == "__init__":
                            parts = parts[:-1]
                            if not parts:
                                continue
                        if not all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", p) for p in parts):
                            continue
                        _add_explicit(".".join(parts))

            # Build-safe forced modules: if these files exist in the project,
            # always include them so common context-menu actions are preserved.
            forced_candidates = [
                ("actions.lbox.common", os.path.join("actions", "lbox", "common.py")),
                ("actions.tbox.common", os.path.join("actions", "tbox", "common.py")),
                ("actions.help.help", os.path.join("actions", "help", "help.py")),
            ]
            for module_name, rel_path in forced_candidates:
                for entry_dir in entry_dirs:
                    if os.path.isfile(os.path.join(entry_dir, rel_path)):
                        _add_explicit(module_name)
                        break

            if all_collect_targets or explicit_hiddenimports:
                if "collect_submodules" not in updated:
                    if re.search(r"from PyInstaller\.utils\.hooks import", updated):
                        updated = re.sub(
                            r"(from PyInstaller\.utils\.hooks import\s+[^\n]+)",
                            lambda mo: mo.group(1).rstrip() + ", collect_submodules",
                            updated, count=1,
                        )
                    else:
                        updated = "from PyInstaller.utils.hooks import collect_submodules\n" + updated
                collect_expr = " + ".join(f"collect_submodules('{p}')" for p in all_collect_targets) if all_collect_targets else ""
                explicit_expr = "[" + ", ".join(repr(m) for m in explicit_hiddenimports) + "]"
                new_hidden = explicit_expr
                if collect_expr:
                    new_hidden = f"{explicit_expr} + {collect_expr}"

                if re.search(r"hiddenimports\s*=", updated):
                    updated = re.sub(
                        r"hiddenimports\s*=\s*.*?,\s*(?=hookspath\s*=)",
                        f"hiddenimports={new_hidden},\n    ",
                        updated,
                        count=1,
                        flags=re.DOTALL,
                    )
                else:
                    updated = re.sub(
                        r"(hookspath\s*=)",
                        f"hiddenimports={new_hidden},\n    \\1",
                        updated,
                        count=1,
                    )

                self.log(f"[INFO] collect_submodules injected voor: {all_collect_targets}")
                self.log(f"[INFO] explicit hiddenimports toegevoegd: {len(explicit_hiddenimports)} modules")
        except Exception as e:
            self.log(f"⚠️ collect_submodules injectie mislukt: {e}")

        # ── inject customtkinter data files (themes/assets) if used ──
        # Direct __path__ copy is used instead of collect_data_files because
        # collect_data_files misses JSON theme assets in some ctk/PyInstaller versions.
        try:
            CTK_DIRECT = "_ctk.__path__[0], 'customtkinter'"
            CTK_COLLECT = "collect_data_files('customtkinter')"
            if CTK_DIRECT not in updated:
                python_exe = self._get_python()
                chk = subprocess.run(
                    [python_exe, "-c", "import customtkinter"],
                    capture_output=True, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if chk.returncode == 0:
                    # ensure 'import customtkinter as _ctk' is at the top of the spec
                    if "import customtkinter as _ctk" not in updated:
                        updated = "import customtkinter as _ctk\n" + updated
                    if CTK_COLLECT in updated:
                        # Replace the old collect_data_files approach with direct path
                        updated = updated.replace(CTK_COLLECT, "[(_ctk.__path__[0], 'customtkinter')]")
                        # Remove collect_data_files from the import line if no longer called
                        if not re.search(r"collect_data_files\s*\(", updated):
                            updated = re.sub(r",\s*collect_data_files\b", "", updated, count=1)
                            updated = re.sub(r"\bcollect_data_files\s*,\s*", "", updated, count=1)
                            updated = re.sub(
                                r"^from PyInstaller\.utils\.hooks import collect_data_files\n",
                                "", updated, flags=re.MULTILINE, count=1,
                            )
                        self.log("[INFO] customtkinter: directe padkopie gebruikt (themes/assets gegarandeerd)")
                    else:
                        # No existing ctk data entry – append to datas=[...]
                        updated = re.sub(
                            r"(datas\s*=\s*\[.*?\])",
                            r"\1 + [(_ctk.__path__[0], 'customtkinter')]",
                            updated, count=1, flags=re.DOTALL,
                        )
                        self.log("[INFO] customtkinter data files (directe padkopie) toegevoegd aan datas")
        except Exception as e:
            self.log(f"⚠️ customtkinter data injectie mislukt: {e}")

        if updated != original:
            with open(spec_path, "w", encoding="utf-8") as f:
                f.write(updated)
        return original

    def _restore_spec(self, spec_path, original):
        if original is None:
            return
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(original)

    def _sanitize_folder_name(self, value: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*]+', '_', value or '').strip(' .')
        return cleaned or "App"

    def _resolve_app_name(self, spec_path: str) -> str:
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec_text = f.read()
            matches = re.findall(r"(?m)^\s*name\s*=\s*['\"]([^'\"]+)['\"]\s*,", spec_text)
            if matches:
                return self._sanitize_folder_name(matches[-1])
        except Exception:
            pass
        return self._sanitize_folder_name(os.path.splitext(os.path.basename(spec_path))[0])

    def _attach_log_file(self, build_dir):
        self._build_log_file_path = os.path.join(build_dir, "build_log.txt")
        with open(self._build_log_file_path, "w", encoding="utf-8") as f:
            f.writelines(self._session_log_lines)

    def _build_worker(self, project, spec, out_root):
        original_spec = None
        try:
            original_spec = self._apply_spec_overrides(spec)
            self.log(f"⚙️ Options: console={self._effective_console} | upx={self._effective_upx} | icon={self._effective_icon}")

            cmd = [self._get_python(), "-m", "PyInstaller", "--noconfirm"]
            if self.clean_build.get():
                cmd.append("--clean")
            cmd.append(spec)
            self.log(f"Running: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd, cwd=project,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, universal_newlines=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._current_process = process
            for line in process.stdout:
                line = line.strip()
                if line:
                    self.log(line)
            process.wait()
            self._current_process = None
            if process.returncode != 0:
                raise Exception(f"PyInstaller failed (code {process.returncode})")

            self.log("✅ PyInstaller build done")

            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            app_name = self._resolve_app_name(spec)
            actual_mode = self._infer_build_mode_from_spec(spec) or self._sync_build_selection()
            mode_tag = ".OF" if actual_mode == "Onefile" else ".OD"
            self.log(f"ℹ️ Gedetecteerde buildmodus: {actual_mode}")
            package_dir = os.path.join(out_root, f"{app_name}{mode_tag}_Build_{timestamp}")
            os.makedirs(package_dir, exist_ok=True)
            self._attach_log_file(package_dir)
            self.log(f"📁 Package folder: {package_dir}")

            for folder in ("build", "dist"):
                src = os.path.join(project, folder)
                if os.path.exists(src):
                    dst = os.path.join(package_dir, folder)
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.move(src, dst)
                    self.log(f"✅ Moved: {folder}")

            readme = os.path.join(package_dir, "README.txt")
            with open(readme, "w", encoding="utf-8") as f:
                f.write("Build Tool Output\n=================\n\n")
                f.write(f"Project:  {project}\n")
                f.write(f"Spec:     {spec}\n")
                f.write(f"Built on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Console:  {self._effective_console}\n")
                f.write(f"UPX:      {self._effective_upx}\n")
                f.write(f"Icon:     {self._effective_icon}\n")
            self.log("✅ README.txt written")
            self.log("🎉 All done!")
            self.after(0, lambda: self._build_complete(package_dir))

        except Exception as exc:
            _msg = str(exc)
            self.after(0, lambda m=_msg: self._build_error(m))
        finally:
            try:
                self._restore_spec(spec, original_spec)
            except Exception as err:
                self.log(f"⚠️ Could not restore spec: {err}")

    def _build_complete(self, package_dir):
        self._stop_animation()
        self._set_busy(False)
        self._set_status(f"✅ Done: {package_dir}")
        # ── write changelog
        app_name = self.app_name.get().strip() or os.path.basename(package_dir).split("_Build_")[0]
        changelog_path = os.path.join(self.output_dir.get(), f"{app_name}_changelog.md")
        self._write_changelog_entry(
            changelog_path,
            action="Build",
            artifact=os.path.basename(package_dir),
        )
        self.save_settings()
        self.build_btn.configure(
            text="✅ Klaar!",
            fg_color="#059669",
            hover_color="#059669",
        )
        self.after(
            2000,
            lambda: self.build_btn.configure(
                text="🚀 Bouwen",
                fg_color="#1f6aa5",
                hover_color="#144870",
            ),
        )
        self._last_package_dir = package_dir
        if self.backup_after_build.get():
            self.after(500, lambda: self._start_backup_auto(package_dir))
        else:
            if messagebox.askyesno("Klaar", f"Build klaar!\n\nBuild folder openen?\n{package_dir}"):
                os.startfile(package_dir)

    def _build_error(self, msg):
        self._stop_animation()
        self._set_busy(False)
        self._set_status(f"❌ {msg}")
        messagebox.showerror("Build Failed", msg)

    def _job_error(self, message):
        self._stop_animation()
        self._set_busy(False)
        self._set_status("Error")
        self.log(f"❌ {message}")
        messagebox.showerror("Error", message)

    def _start_backup_auto(self, package_dir=None):
        """Auto backup triggered after a successful build."""
        project = self.project_dir.get().strip()
        backup_out = self.backup_dir.get().strip()
        if not backup_out or not project or not os.path.isdir(project):
            self.log("⚠️ Auto backup overgeslagen: geen geldig project of backup folder.")
            if package_dir and messagebox.askyesno("Klaar", f"Build klaar!\n\nBuild folder openen?\n{package_dir}"):
                os.startfile(package_dir)
            return
        os.makedirs(backup_out, exist_ok=True)
        self.is_backing_up = True
        self._highlight_active_btn(self.backup_btn)
        self._set_status("Auto backup na build...")
        self.log("▶ Auto backup na build starten...")
        Thread(target=self._backup_worker, args=(project, backup_out), daemon=True).start()

    def _request_cancel(self):
        """Stop button: cancel the current build/chain."""
        if not self.is_working:
            return
        self._cancel_requested = True
        proc = self._current_process
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        self._stop_animation()
        self._set_busy(False)
        self._set_status("⚠️ Gestopt door gebruiker.")
        self.log("⚠️ Build gestopt door gebruiker.")

    # ──────────────────────────────────────────────────────────────
    # Changelog helpers
    # ──────────────────────────────────────────────────────────────
    def _write_changelog_entry(self, log_path: str, action: str, artifact: str):
        """Append one entry to the per-app changelog file."""
        description = self.change_description.get().strip() or "(geen beschrijving opgegeven)"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"## {timestamp}\n"
            f"Actie     : {action}\n"
            f"Artifact  : {artifact}\n"
            f"Wijziging : {description}\n"
            f"{'─' * 60}\n\n"
        )
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            is_new = not os.path.exists(log_path) or os.path.getsize(log_path) == 0
            with open(log_path, "a", encoding="utf-8") as f:
                if is_new:
                    app_label = os.path.splitext(os.path.basename(log_path))[0].replace("_changelog", "")
                    f.write(f"# Changelog — {app_label}\n\n")
                f.write(entry)
            self.log(f"📓 Changelog bijgewerkt: {log_path}")
        except Exception as exc:
            self.log(f"⚠️ Changelog schrijven mislukt: {exc}")

    # ──────────────────────────────────────────────────────────────
    # Backup
    # ──────────────────────────────────────────────────────────────
    def start_backup(self):
        if self.is_working or self.is_backing_up:
            messagebox.showwarning("Busy", "A task is already running.")
            return
        project = self._validate_project()
        if not project:
            return
        backup_out = self.backup_dir.get().strip()
        if not backup_out:
            messagebox.showerror("Error", "Backup folder is required.")
            return
        self.save_settings()
        os.makedirs(backup_out, exist_ok=True)
        self.is_backing_up = True
        # Don't disable – yellow highlight already signals "busy"; is_backing_up guard prevents re-entry
        self._highlight_active_btn(self.backup_btn)
        self._set_status("Creating backup...")
        self.log(f"▶ Starting backup of: {project}")
        Thread(target=self._backup_worker, args=(project, backup_out), daemon=True).start()

    def _backup_worker(self, project, backup_out):
        try:
            project_name = os.path.basename(os.path.normpath(project))
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            zip_name = f"{project_name}_Backup_{timestamp}.zip"
            zip_path = os.path.join(backup_out, zip_name)

            excl_venv    = not self.include_venv.get()
            excl_cache   = not self.include_pycache.get()
            excl_builds  = not self.include_builds.get()

            BUILD_DIRS = {"build", "dist", "builds", "__pyinstaller"}

            def _is_excluded(rel: str) -> bool:
                parts = rel.replace("\\", "/").split("/")
                for p in parts:
                    pl = p.lower()
                    if excl_venv   and pl in (".venv", "venv"):
                        return True
                    if excl_cache  and pl in ("__pycache__", ".mypy_cache", ".pytest_cache"):
                        return True
                    if excl_builds and pl in BUILD_DIRS:
                        return True
                    if p.startswith(".") and p not in (".", ".."):
                        return True
                return False

            count = 0
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(project):
                    rel_root = os.path.relpath(root, project)
                    if _is_excluded(rel_root):
                        dirs.clear()
                        continue
                    dirs[:] = [d for d in dirs
                               if not _is_excluded(os.path.join(rel_root, d))]
                    for fname in files:
                        frel = os.path.join(rel_root, fname)
                        if not _is_excluded(frel):
                            zf.write(os.path.join(root, fname),
                                     os.path.join(project_name, frel))
                            count += 1

            size_mb = os.path.getsize(zip_path) / 1024 / 1024
            self.after(0, lambda: self._backup_complete(zip_path, count, size_mb))
        except Exception as exc:
            _msg = str(exc)
            self.after(0, lambda m=_msg: self._backup_error(m))

    def _backup_complete(self, zip_path, count, size_mb):
        self.is_backing_up = False
        self.backup_btn.configure(state="normal")
        self._restore_active_btn()
        self._set_status(f"✅ Backup done: {os.path.basename(zip_path)}")
        self.log(f"✅ Backup created: {zip_path} ({count} files, {size_mb:.1f} MB)")
        # ── write changelog
        project_name = os.path.basename(os.path.normpath(self.project_dir.get()))
        changelog_path = os.path.join(self.backup_dir.get(), f"{project_name}_changelog.md")
        self._write_changelog_entry(
            changelog_path,
            action="Backup",
            artifact=os.path.basename(zip_path),
        )
        self.save_settings()
        self.log(f"✅ Backup klaar: {zip_path}")
        build_dir = getattr(self, '_last_package_dir', None)
        if build_dir and os.path.isdir(build_dir):
            if messagebox.askyesno("Klaar",
                                   f"Backup klaar ({count} bestanden, {size_mb:.1f} MB)\n\nBuild folder openen?\n{build_dir}"):
                os.startfile(build_dir)
        else:
            if messagebox.askyesno("Backup Done",
                                   f"{count} files zipped ({size_mb:.1f} MB)\n\n{zip_path}\n\nOpen backup folder?"):
                os.startfile(os.path.dirname(zip_path))

    def _backup_error(self, msg):
        self.is_backing_up = False
        self.backup_btn.configure(state="normal")
        self._restore_active_btn()
        self._set_status(f"❌ Backup failed")
        self.log(f"❌ Backup failed: {msg}")
        messagebox.showerror("Backup Failed", msg)

    # ──────────────────────────────────────────────────────────────
    # Output folder
    # ──────────────────────────────────────────────────────────────
    def open_output(self):
        out = self.output_dir.get().strip()
        if os.path.exists(out):
            os.startfile(out)
        else:
            messagebox.showwarning("Missing", "Output folder does not exist yet.")

    def _on_close(self):
        self.save_settings()
        self.destroy()


def main():
    try:
        app = BuildCombinedGUI()
        app.mainloop()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        crash_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_combined_crash.log")
        tb = traceback.format_exc()
        try:
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 72 + "\n")
                f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                f.write(tb)
        except Exception:
            pass
        try:
            messagebox.showerror(
                "Startup Error",
                f"De app is onverwacht gestopt tijdens opstarten.\n\n"
                f"Fout: {exc}\n\n"
                f"Zie crashlog: {crash_log}"
            )
        except Exception:
            print(tb)


if __name__ == "__main__":
    main()
