#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chess Studio — app dung video cho kenh "Like a Chess Game"
==========================================================
Quy trinh 1 tap (xem claude_project/README.md):
  KÊNH       cai dat 1 lan: thu muc, token HuggingFace, SFX, font, toc do go chu...
  THOẠI      clip mp4 -> WhisperX + pyannote -> bang duyet NGUOI NOI -> copy TRANSCRIPT cho /analyze
  NƯỚC ĐI    dan khoi MOVES tu /qcmoves -> kiem tra anchor -> xem truoc the
  DRAFT      sinh draft CapCut + CHAPTERS (cho /metadata) + khung hinh (cho /thumbnail)
  THUMBNAIL  dan spec tu /thumbnail -> anh 1280x720

Cac file can nam CANH app: lacg_formats.py, lacg_render.py, lacg_transcribe.py, lacg_build.py
Du lieu app: ~/.chess_studio/  (settings.json, voices/, done.json)
Chay: py chess_studio.pyw
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lacg_formats as F  # noqa: E402

APP_DIR = Path.home() / ".chess_studio"
SETTINGS_FILE = APP_DIR / "settings.json"
VOICES_DIR = APP_DIR / "voices"
DONE_FILE = APP_DIR / "done.json"
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0


def console_python() -> str:
    """pythonw.exe khong co stdout -> subprocess dung python.exe cung thu muc."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.is_file():
            return str(cand)
    return str(exe)


PY = console_python()

CLR_BG = "#F7F7F5"
CLR_ACCENT = "#81B64C"
CLR_RUN = "#98FB98"
ROW_TAGS = {"overlap": "#FFE7C2", "unknown": "#FFD6D6", "separated": "#E3F0FF",
            "error": "#FFD6D6", "warn": "#FFF4C2", "ok": "#E6F5DA"}

DEFAULT_SETTINGS = {
    "episodes_root": str(Path.home() / "LACG_episodes"),
    "sfx_dir": str(HERE / "sfx"),
    "draft_root": str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CapCut" / "User Data"
                      / "Projects" / "com.lveditor.draft"),
    "hf_token": "",
    "whisper_python": "",
    "whisper_model": "large-v3",
    "language": "en",
    "device": "cuda",
    "batch": 16,
    "font_regular": "", "font_bold": "",
    "cps": 30.0, "pause": 2.5, "review_hold": 15.0, "sub_size": 7.0,
    "width": 1920, "height": 1080, "fps": 30,
    "last_episode": "",
}


def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def open_path(p) -> None:
    p = str(p)
    try:
        if IS_WIN:
            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as ex:
        messagebox.showerror("Không mở được", str(ex))


# ════════════════════════════════════════════════════════════════════════════
class ChessStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Chess Studio — Like a Chess Game")
        self.geometry("1280x860")
        self.minsize(1050, 700)
        self.configure(bg=CLR_BG)
        APP_DIR.mkdir(parents=True, exist_ok=True)
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        self.s = dict(DEFAULT_SETTINGS)
        self.s.update(load_json(SETTINGS_FILE, {}))
        self.ep_dir: Path | None = None
        self.ep: dict = {}
        self.tdata: dict | None = None          # transcript.json dang mo
        self.game: F.Game | None = None
        self.proc: subprocess.Popen | None = None
        self.player: subprocess.Popen | None = None
        self.q: "queue.Queue" = queue.Queue()
        self._editor = None
        self._style()
        self._build()
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        last = self.s.get("last_episode")
        if last and (Path(last) / "episode.json").is_file():
            self._open_episode(Path(last))
        self._refresh_episode_list()

    # ───────────────────────────────────────────────────────────── giao dien
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=CLR_BG)
        st.configure("TNotebook.Tab", padding=(14, 6), font=("Segoe UI", 10, "bold"))
        st.configure("Run.TButton", background=CLR_RUN, font=("Segoe UI", 10, "bold"))
        st.configure("Hint.TLabel", foreground="#666666")
        st.configure("H.TLabel", font=("Segoe UI", 11, "bold"))
        st.configure("Treeview", rowheight=24)

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(top, text="Tập:", style="H.TLabel").pack(side="left")
        self.v_ep = tk.StringVar()
        self.cb_ep = ttk.Combobox(top, textvariable=self.v_ep, width=48, state="readonly")
        self.cb_ep.pack(side="left", padx=6)
        self.cb_ep.bind("<<ComboboxSelected>>", lambda e: self._on_ep_selected())
        ttk.Button(top, text="＋ Tạo tập mới từ clip…", command=self._new_episode).pack(side="left", padx=4)
        ttk.Button(top, text="📁 Mở thư mục tập", command=lambda: self.ep_dir and open_path(self.ep_dir)).pack(side="left")
        self.lbl_ep = ttk.Label(top, text="", style="Hint.TLabel")
        self.lbl_ep.pack(side="left", padx=10)

        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True, padx=10, pady=4)
        self.nb = ttk.Notebook(pw)
        pw.add(self.nb, weight=5)
        logf = ttk.Frame(pw)
        pw.add(logf, weight=1)
        self.log = tk.Text(logf, height=8, wrap="word", bg="#1E1E1E", fg="#E6E6E6", insertbackground="white",
                           font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for name, builder in (("KÊNH", self._tab_settings), ("THOẠI", self._tab_dialogue),
                              ("NƯỚC ĐI", self._tab_moves), ("DRAFT", self._tab_draft),
                              ("THUMBNAIL", self._tab_thumb)):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text=name)
            builder(f)
        self.nb.select(1)

    def _path_row(self, parent, row, label, var, kind="dir", filetypes=None, width=70):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky="ew", padx=6)

        def pick():
            if kind == "dir":
                p = filedialog.askdirectory(initialdir=var.get() or None)
            else:
                p = filedialog.askopenfilename(filetypes=filetypes or [("Tất cả", "*.*")])
            if p:
                var.set(p)
        ttk.Button(parent, text="Chọn…", command=pick).grid(row=row, column=2, sticky="w")

    # ════════════════════════════════════════════════════════ TAB KÊNH
    def _tab_settings(self, f):
        f.columnconfigure(1, weight=1)
        self.sv = {}
        rows = [("episodes_root", "Thư mục chứa các tập", "dir"),
                ("sfx_dir", "Thư mục SFX", "dir"),
                ("draft_root", "Thư mục draft CapCut", "dir"),
                ("whisper_python", "Python của WhisperX (trống = Python đang chạy app)", "file"),
                ("font_regular", "Font thường (.ttf, trống = Arial)", "file"),
                ("font_bold", "Font đậm (.ttf, trống = Arial Bold)", "file")]
        r = 0
        for key, label, kind in rows:
            self.sv[key] = tk.StringVar(value=str(self.s.get(key, "")))
            self._path_row(f, r, label, self.sv[key], kind,
                           [("Python", "python*.exe python*"), ("Tất cả", "*.*")] if key == "whisper_python"
                           else [("Font", "*.ttf *.otf"), ("Tất cả", "*.*")] if key.startswith("font") else None)
            r += 1
        ttk.Label(f, text="HuggingFace token (pyannote)").grid(row=r, column=0, sticky="w", pady=3)
        self.sv["hf_token"] = tk.StringVar(value=self.s.get("hf_token", ""))
        ttk.Entry(f, textvariable=self.sv["hf_token"], width=70, show="•").grid(row=r, column=1, sticky="ew", padx=6)
        r += 1
        grid = ttk.Frame(f)
        grid.grid(row=r, column=0, columnspan=3, sticky="w", pady=(10, 4))
        nums = [("whisper_model", "Model Whisper", 12), ("language", "Ngôn ngữ", 6), ("device", "Thiết bị", 8),
                ("batch", "Batch", 5), ("cps", "Tốc độ gõ (ký tự/giây)", 6), ("pause", "Dừng đọc (giây)", 6),
                ("review_hold", "Giữ Game Review (giây)", 6), ("sub_size", "Cỡ phụ đề CapCut", 6),
                ("width", "Rộng", 6), ("height", "Cao", 6), ("fps", "FPS", 5)]
        for i, (key, label, w) in enumerate(nums):
            self.sv[key] = tk.StringVar(value=str(self.s.get(key, "")))
            ttk.Label(grid, text=label).grid(row=i // 4, column=(i % 4) * 2, sticky="w", padx=(0, 4), pady=3)
            ttk.Entry(grid, textvariable=self.sv[key], width=w).grid(row=i // 4, column=(i % 4) * 2 + 1,
                                                                    sticky="w", padx=(0, 18))
        r += 1
        bx = ttk.Frame(f)
        bx.grid(row=r, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Button(bx, text="LƯU CÀI ĐẶT", style="Run.TButton", command=self._save_settings).pack(side="left")
        ttk.Button(bx, text="KIỂM TRA CÀI ĐẶT", command=self._run_check).pack(side="left", padx=8)
        r += 1
        ttk.Label(f, text="KHO GIỌNG NHÂN VẬT (vân tay giọng đã lưu — tập sau app tự nhận ra)",
                  style="H.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", pady=(14, 4))
        r += 1
        vf = ttk.Frame(f)
        vf.grid(row=r, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(r, weight=1)
        self.lb_voices = tk.Listbox(vf, height=8, width=40)
        self.lb_voices.pack(side="left", fill="y")
        bf = ttk.Frame(vf)
        bf.pack(side="left", padx=8, anchor="n")
        ttk.Button(bf, text="Xoá giọng đã chọn", command=self._delete_voice).pack(fill="x")
        ttk.Button(bf, text="Copy danh sách đã làm (cho /pick)", command=self._copy_done).pack(fill="x", pady=4)
        ttk.Label(bf, style="Hint.TLabel", wraplength=520, justify="left",
                  text="• SFX: đặt file trong thư mục SFX đúng tên: brilliant, great, best, excellent, good, book,\n"
                       "  inaccuracy, mistake, miss, blunder, move, typing, checkmate (.mp3/.wav).\n"
                       "• Token HuggingFace (Read): tạo ở huggingface.co/settings/tokens, rồi bấm 'Agree' ở trang các model\n"
                       "  pyannote/speaker-diarization-community-1 và pyannote/wespeaker-voxceleb-resnet34-LM\n"
                       "  (thêm pyannote/speech-separation-ami-1.0 nếu dùng tách giọng nói chồng).").pack(anchor="w", pady=8)
        self._refresh_voices()

    def _save_settings(self, quiet=False):
        for k, v in self.sv.items():
            val = v.get().strip()
            if k in ("batch", "width", "height", "fps"):
                try:
                    val = int(float(val))
                except ValueError:
                    val = DEFAULT_SETTINGS[k]
            elif k in ("cps", "pause", "review_hold", "sub_size"):
                try:
                    val = float(val)
                except ValueError:
                    val = DEFAULT_SETTINGS[k]
            self.s[k] = val
        save_json(SETTINGS_FILE, self.s)
        self._refresh_episode_list()
        if not quiet:
            self._log("💾 Đã lưu cài đặt.")

    def _run_check(self):
        if not self._guard_busy():
            return
        self._save_settings(quiet=True)
        py = self.s.get("whisper_python") or PY
        cmd = [py, "-u", str(HERE / "lacg_check.py"), "--sfx-dir", self.s.get("sfx_dir", "")]
        if self.s.get("hf_token"):
            cmd += ["--hf-token", self.s["hf_token"]]
        self._spawn(cmd, "Kiểm tra cài đặt", hide=self.s.get("hf_token", ""))

    def _refresh_voices(self):
        self.lb_voices.delete(0, "end")
        for p in sorted(VOICES_DIR.glob("*.json")):
            d = load_json(p, {})
            self.lb_voices.insert("end", "%s  (%d mẫu)" % (d.get("label", p.stem), len(d.get("embeddings", []))))

    def _delete_voice(self):
        sel = self.lb_voices.curselection()
        if not sel:
            return
        label = self.lb_voices.get(sel[0]).split()[0]
        if messagebox.askyesno("Xoá giọng", "Xoá vân tay giọng %s?" % label):
            p = VOICES_DIR / ("%s.json" % label)
            if p.is_file():
                p.unlink()
            self._refresh_voices()

    def _copy_done(self):
        done = load_json(DONE_FILE, [])
        text = "\n".join("%s — %s — %s" % (d.get("series", ""), d.get("episode", ""), d.get("scene", "")) for d in done)
        self._clip(text or "(chưa có tập nào)")
        self._log("📋 Đã copy %d cảnh đã làm — dán sau 'done:' của /pick." % len(done))

    # ════════════════════════════════════════════════════════ TẬP
    def _episodes_root(self) -> Path:
        return Path(self.s.get("episodes_root") or DEFAULT_SETTINGS["episodes_root"])

    def _refresh_episode_list(self):
        root = self._episodes_root()
        items = []
        if root.is_dir():
            items = sorted([p.name for p in root.iterdir() if (p / "episode.json").is_file()],
                           key=lambda n: -(root / n / "episode.json").stat().st_mtime)
        self.cb_ep["values"] = items
        if self.ep_dir is not None:
            self.v_ep.set(self.ep_dir.name)

    def _on_ep_selected(self):
        name = self.v_ep.get()
        if name:
            self._open_episode(self._episodes_root() / name)

    def _new_episode(self):
        clip = filedialog.askopenfilename(title="Chọn clip cảnh (mp4)",
                                          filetypes=[("Video", "*.mp4 *.mkv *.mov *.m4v"), ("Tất cả", "*.*")])
        if not clip:
            return
        name = simpledialog.askstring("Tên tập", "Tên thư mục tập:", parent=self,
                                      initialvalue=re.sub(r"[^\w\-]+", "_", Path(clip).stem)[:60])
        if not name:
            return
        name = re.sub(r'[\\/:*?"<>|]+', "_", name.strip())
        d = self._episodes_root() / name
        if (d / "episode.json").is_file() and not messagebox.askyesno("Đã có", "Tập '%s' đã có. Mở tập đó?" % name):
            return
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "episode.json").is_file():
            save_json(d / "episode.json", {"clip": clip, "draft_name": name, "badge_pos": [], "avatars": {}})
        self._open_episode(d)
        self._refresh_episode_list()

    def _open_episode(self, d: Path):
        self.ep_dir = d
        self.ep = load_json(d / "episode.json", {})
        self.s["last_episode"] = str(d)
        save_json(SETTINGS_FILE, self.s)
        self.lbl_ep.configure(text=self.ep.get("clip", ""))
        self.v_srt.set(self.ep.get("official_srt", ""))
        self.v_draft_name.set(self.ep.get("draft_name", d.name))
        self.v_av_w.set((self.ep.get("avatars") or {}).get("W", ""))
        self.v_av_b.set((self.ep.get("avatars") or {}).get("B", ""))
        tp = d / "transcript.json"
        self.tdata = F.load_transcript(str(tp)) if tp.is_file() else None
        self._refresh_speakers()
        self._refresh_lines()
        mp = d / "moves.txt"
        self.txt_moves.delete("1.0", "end")
        if mp.is_file():
            self.txt_moves.insert("1.0", mp.read_text(encoding="utf-8"))
        self.game = None
        self._refresh_moves_tree()
        cp = d / "chapters.txt"
        self.txt_chap.delete("1.0", "end")
        if cp.is_file():
            self.txt_chap.insert("1.0", cp.read_text(encoding="utf-8"))
        self.v_ep.set(d.name)
        self._log("📂 Mở tập: %s" % d)

    def _save_episode(self):
        if self.ep_dir is None:
            return
        self.ep["official_srt"] = self.v_srt.get().strip()
        self.ep["draft_name"] = self.v_draft_name.get().strip() or self.ep_dir.name
        self.ep["avatars"] = {"W": self.v_av_w.get().strip(), "B": self.v_av_b.get().strip()}
        save_json(self.ep_dir / "episode.json", self.ep)

    def _need_ep(self) -> bool:
        if self.ep_dir is None:
            messagebox.showinfo("Chưa có tập", "Bấm '＋ Tạo tập mới từ clip…' hoặc chọn một tập trước.")
            return False
        return True

    # ════════════════════════════════════════════════════════ TAB THOẠI
    def _tab_dialogue(self, f):
        f.columnconfigure(0, weight=1)
        opt = ttk.Frame(f)
        opt.grid(row=0, column=0, sticky="ew")
        opt.columnconfigure(1, weight=1)
        self.v_srt = tk.StringVar()
        self._path_row(opt, 0, "Phụ đề chuẩn .srt (tuỳ chọn — cảnh cãi nhau nên dùng)", self.v_srt, "file",
                       [("SRT", "*.srt"), ("Tất cả", "*.*")], width=60)
        o2 = ttk.Frame(opt)
        o2.grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        self.v_demucs = tk.BooleanVar(value=True)
        self.v_sep = tk.BooleanVar(value=False)
        self.v_minsp = tk.StringVar(value="")
        self.v_maxsp = tk.StringVar(value="")
        ttk.Checkbutton(o2, text="Demucs tách giọng khỏi nhạc nền", variable=self.v_demucs).pack(side="left")
        ttk.Checkbutton(o2, text="Tách giọng đoạn nói chồng (thử nghiệm)", variable=self.v_sep).pack(side="left", padx=12)
        ttk.Label(o2, text="Số người nói tối thiểu/tối đa:").pack(side="left", padx=(12, 4))
        ttk.Entry(o2, textvariable=self.v_minsp, width=4).pack(side="left")
        ttk.Entry(o2, textvariable=self.v_maxsp, width=4).pack(side="left", padx=4)
        ttk.Button(o2, text="NHẬN THOẠI", style="Run.TButton", command=self._run_transcribe).pack(side="left", padx=14)
        ttk.Button(o2, text="Dừng", command=self._stop).pack(side="left")

        spf = ttk.LabelFrame(f, text=" NGƯỜI NÓI — đặt tên nhân vật (IN HOA, ví dụ GUS, HANK) ", padding=6)
        spf.grid(row=1, column=0, sticky="ew", pady=6)
        self.sp_frame = ttk.Frame(spf)
        self.sp_frame.pack(fill="x")
        self.sp_vars = {}

        lf = ttk.Frame(f)
        lf.grid(row=2, column=0, sticky="nsew")
        f.rowconfigure(2, weight=1)
        cols = ("i", "start", "end", "who", "text", "flag")
        self.tv_lines = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        for c, w, t in (("i", 40, "#"), ("start", 70, "Bắt đầu"), ("end", 70, "Kết thúc"), ("who", 110, "Người nói"),
                        ("text", 760, "Lời thoại"), ("flag", 90, "")):
            self.tv_lines.heading(c, text=t)
            self.tv_lines.column(c, width=w, stretch=(c == "text"), anchor="w")
        for tag, color in ROW_TAGS.items():
            self.tv_lines.tag_configure(tag, background=color)
        sb = ttk.Scrollbar(lf, command=self.tv_lines.yview)
        self.tv_lines.configure(yscrollcommand=sb.set)
        self.tv_lines.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv_lines.bind("<Double-1>", self._on_line_double)

        bf = ttk.Frame(f)
        bf.grid(row=3, column=0, sticky="ew", pady=6)
        for text, cmd in (("▶ Nghe dòng", self._play_line), ("■ Dừng nghe", self._stop_play),
                          ("Thêm dòng", self._add_line), ("Tách dòng", self._split_line),
                          ("Gộp với dòng sau", self._merge_line), ("Xoá dòng", self._delete_lines)):
            ttk.Button(bf, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(bf, text="💾 Lưu", command=self._save_transcript).pack(side="left", padx=(18, 6))
        ttk.Button(bf, text="📋 Copy TRANSCRIPT (cho /analyze)", style="Run.TButton",
                   command=self._copy_transcript).pack(side="left", padx=6)
        ttk.Button(bf, text="Xuất dialogue.srt", command=self._export_srt).pack(side="left", padx=6)
        ttk.Label(f, style="Hint.TLabel",
                  text="Double-click ô Người nói / Lời thoại / giờ để sửa. Cam = đoạn nói chồng, đỏ = chưa rõ ai nói, "
                       "xanh = dòng lấy từ giọng đã tách. Mỗi dòng chỉ được thuộc 1 người.").grid(row=4, column=0, sticky="w")

    def _run_transcribe(self):
        if not self._need_ep() or not self._guard_busy():
            return
        self._save_settings(quiet=True)
        self._save_episode()
        clip = self.ep.get("clip", "")
        if not Path(clip).is_file():
            return messagebox.showerror("Thiếu clip", "Không thấy file clip:\n%s" % clip)
        if not self.s.get("hf_token"):
            if not messagebox.askyesno("Chưa có token", "Chưa có HuggingFace token → không phân biệt được người nói.\n"
                                                        "Vẫn chạy?"):
                return
        py = self.s.get("whisper_python") or PY
        cmd = [py, "-u", str(HERE / "lacg_transcribe.py"), "--clip", clip,
               "--out", str(self.ep_dir / "transcript.json"),
               "--model", self.s.get("whisper_model", "large-v3"), "--language", self.s.get("language", "en"),
               "--device", self.s.get("device", "cuda"), "--batch", str(self.s.get("batch", 16)),
               "--voices-dir", str(VOICES_DIR)]
        if self.s.get("hf_token"):
            cmd += ["--hf-token", self.s["hf_token"]]
        if self.v_srt.get().strip():
            cmd += ["--srt", self.v_srt.get().strip()]
        if not self.v_demucs.get():
            cmd.append("--no-demucs")
        if self.v_sep.get():
            cmd.append("--separate-overlaps")
        for flag, v in (("--min-speakers", self.v_minsp), ("--max-speakers", self.v_maxsp)):
            if v.get().strip().isdigit():
                cmd += [flag, v.get().strip()]

        def done(ok):
            if ok:
                self.tdata = F.load_transcript(str(self.ep_dir / "transcript.json"))
                self._refresh_speakers()
                self._refresh_lines()
                self._log("👉 Đặt tên người nói, nghe kiểm tra các dòng tô màu, rồi 'Copy TRANSCRIPT'.")
        self._spawn(cmd, "Nhận thoại", done, hide=self.s.get("hf_token", ""))

    # ── nguoi noi
    def _refresh_speakers(self):
        for w in self.sp_frame.winfo_children():
            w.destroy()
        self.sp_vars = {}
        if not self.tdata:
            ttk.Label(self.sp_frame, text="(chưa có transcript — bấm NHẬN THOẠI)", style="Hint.TLabel").pack(anchor="w")
            return
        counts = {}
        for ln in self.tdata.get("lines", []):
            counts[ln["speaker"]] = counts.get(ln["speaker"], 0) + 1
        for i, (sp, info) in enumerate(sorted(self.tdata.get("speakers", {}).items())):
            r, c = divmod(i, 3)
            box = ttk.Frame(self.sp_frame)
            box.grid(row=r, column=c, sticky="w", padx=(0, 22), pady=2)
            ttk.Button(box, text="▶", width=3, command=lambda s=sp: self._play_sample(s)).pack(side="left")
            ttk.Label(box, text="%s (%d dòng)" % (sp, counts.get(sp, 0))).pack(side="left", padx=4)
            v = tk.StringVar(value=info.get("label", ""))
            self.sp_vars[sp] = v
            ttk.Entry(box, textvariable=v, width=14).pack(side="left")
            if info.get("suggest"):
                ttk.Label(box, text="gợi ý: %s (%.2f)" % (info["suggest"], info.get("score", 0)),
                          style="Hint.TLabel").pack(side="left", padx=4)
        ttk.Button(self.sp_frame, text="Áp dụng tên + lưu giọng", command=self._apply_names).grid(
            row=99, column=0, sticky="w", pady=(6, 0))

    def _apply_names(self):
        if not self.tdata:
            return
        saved = 0
        for sp, v in self.sp_vars.items():
            lab = re.sub(r"[^A-Z0-9 '\-]", "", v.get().strip().upper())
            info = self.tdata["speakers"].setdefault(sp, {})
            info["label"] = lab
            emb = info.get("embedding")
            if lab and emb:
                p = VOICES_DIR / ("%s.json" % lab.replace(" ", "_"))
                d = load_json(p, {"label": lab, "embeddings": []})
                d["label"] = lab
                d["embeddings"] = (d.get("embeddings", []) + [emb])[-20:]
                save_json(p, d)
                saved += 1
        self._save_transcript()
        self._refresh_lines()
        self._refresh_voices()
        self._log("✅ Đã áp dụng tên. Lưu %d vân tay giọng vào kho." % saved)

    def _play_sample(self, sp):
        info = (self.tdata or {}).get("speakers", {}).get(sp, {})
        s, e = (info.get("sample") or [0, 4])[:2]
        self._play(s, e)

    # ── bang dong thoai
    def _labels(self):
        labs = set()
        if self.tdata:
            for info in self.tdata.get("speakers", {}).values():
                if info.get("label"):
                    labs.add(info["label"])
            for ln in self.tdata.get("lines", []):
                if ln.get("label"):
                    labs.add(ln["label"])
        return sorted(labs)

    def _refresh_lines(self, keep_sel=None):
        tv = self.tv_lines
        tv.delete(*tv.get_children())
        if not self.tdata:
            return
        self.tdata["lines"] = F.sorted_lines(self.tdata)
        flags = F.overlap_flags(self.tdata)
        for i, (ln, ov) in enumerate(zip(self.tdata["lines"], flags)):
            who = F.label_of(self.tdata, ln)
            tags = []
            fl = ""
            if ln.get("separated"):
                tags.append("separated")
                fl = "giọng tách"
            elif ov or ln.get("overlap"):
                tags.append("overlap")
                fl = "nói chồng"
            if who == "?":
                tags = ["unknown"]
            tv.insert("", "end", iid=str(i), values=(i + 1, F.fmt_ts(ln["start"]), F.fmt_ts(ln["end"]), who,
                                                     ln.get("text", ""), fl), tags=tags)
        if keep_sel is not None and tv.exists(str(keep_sel)):
            tv.selection_set(str(keep_sel))
            tv.see(str(keep_sel))

    def _sel_index(self):
        sel = self.tv_lines.selection()
        return int(sel[0]) if sel else None

    def _on_line_double(self, event):
        iid = self.tv_lines.identify_row(event.y)
        col = self.tv_lines.identify_column(event.x)
        if not iid or not self.tdata:
            return
        i = int(iid)
        ln = self.tdata["lines"][i]
        colname = ("i", "start", "end", "who", "text", "flag")[int(col[1:]) - 1]
        if colname == "who":
            vals = self._labels() + ["?"]

            def commit(v):
                ln["label"] = "" if v == "?" else v.strip().upper()
                self._after_edit(i)
            self._cell_editor(self.tv_lines, iid, col, F.label_of(self.tdata, ln), commit, values=vals)
        elif colname == "text":
            def commit(v):
                ln["text"] = v.strip()
                self._after_edit(i)
            self._cell_editor(self.tv_lines, iid, col, ln.get("text", ""), commit)
        elif colname in ("start", "end"):
            def commit(v):
                t = F.parse_time(v)
                if t is not None:
                    ln[colname] = t
                    if ln["end"] < ln["start"]:
                        ln["end"] = ln["start"] + 0.3
                    ln["words"] = []           # moc gio tu cu khong con dung -> chia deu
                    self._after_edit(None)
            self._cell_editor(self.tv_lines, iid, col, F.fmt_ts(ln[colname]), commit)
        elif colname == "i":
            self._play_line()

    def _after_edit(self, keep):
        self._save_transcript(quiet=True)
        self._refresh_lines(keep)

    def _cell_editor(self, tv, iid, col, value, commit, values=None):
        if self._editor is not None:
            self._editor.destroy()
        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        var = tk.StringVar(value=value)
        if values is not None:
            ed = ttk.Combobox(tv, textvariable=var, values=values)
        else:
            ed = ttk.Entry(tv, textvariable=var)
        ed.place(x=x, y=y, width=max(w, 120), height=h)
        ed.focus_set()
        if values is None:
            ed.select_range(0, "end")
        self._editor = ed

        def ok(_e=None):
            v = var.get()
            close()
            commit(v)

        def close(_e=None):
            if self._editor is not None:
                self._editor.destroy()
                self._editor = None
        ed.bind("<Return>", ok)
        ed.bind("<Escape>", close)
        ed.bind("<FocusOut>", ok)
        if values is not None:
            ed.bind("<<ComboboxSelected>>", ok)

    def _play_line(self):
        i = self._sel_index()
        if i is None or not self.tdata:
            return
        ln = self.tdata["lines"][i]
        self._play(ln["start"] - 0.2, ln["end"] + 0.2)

    def _play(self, s, e):
        self._stop_play()
        clip = self.ep.get("clip", "")
        if not shutil.which("ffplay"):
            return self._log("⚠️  Không thấy ffplay (đi kèm ffmpeg) để nghe thử.")
        try:
            self.player = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                                            "-ss", "%.2f" % max(0.0, s), "-t", "%.2f" % max(0.3, e - s), clip],
                                           creationflags=NO_WINDOW)
        except Exception as ex:
            self._log("⚠️  Không phát được: %s" % ex)

    def _stop_play(self):
        if self.player and self.player.poll() is None:
            self.player.terminate()
        self.player = None

    def _add_line(self):
        if not self.tdata:
            return
        i = self._sel_index()
        base = self.tdata["lines"][i] if i is not None else None
        s = base["end"] if base else 0.0
        self.tdata["lines"].append({"start": s, "end": s + 1.5, "speaker": "", "label": "?", "text": "(dòng mới)",
                                    "words": []})
        self._after_edit(None)

    def _split_line(self):
        i = self._sel_index()
        if i is None or not self.tdata:
            return
        ln = self.tdata["lines"][i]
        words = ln.get("text", "").split()
        if len(words) < 2:
            return
        k = simpledialog.askinteger("Tách dòng", "Tách SAU từ thứ mấy? (1..%d)\n%s" % (len(words) - 1, ln["text"]),
                                    minvalue=1, maxvalue=len(words) - 1, parent=self)
        if not k:
            return
        timed = F.line_words(ln)
        ntok = len(F.norm_tokens(" ".join(words[:k])))
        cut = timed[ntok - 1]["e"] if timed and ntok - 1 < len(timed) else (ln["start"] + ln["end"]) / 2
        w_old = ln.get("words") or []
        a = dict(ln, end=cut, text=" ".join(words[:k]), words=w_old[:k] if len(w_old) == len(words) else [])
        b = dict(ln, start=cut, text=" ".join(words[k:]), words=w_old[k:] if len(w_old) == len(words) else [])
        self.tdata["lines"][i:i + 1] = [a, b]
        self._after_edit(i)

    def _merge_line(self):
        i = self._sel_index()
        if i is None or not self.tdata or i + 1 >= len(self.tdata["lines"]):
            return
        a, b = self.tdata["lines"][i], self.tdata["lines"][i + 1]
        if F.label_of(self.tdata, a) != F.label_of(self.tdata, b):
            if not messagebox.askyesno("Khác người nói", "Hai dòng của 2 người khác nhau. Vẫn gộp (theo người dòng trên)?"):
                return
        m = dict(a, end=max(a["end"], b["end"]), text=(a.get("text", "") + " " + b.get("text", "")).strip(),
                 words=(a.get("words") or []) + (b.get("words") or []))
        self.tdata["lines"][i:i + 2] = [m]
        self._after_edit(i)

    def _delete_lines(self):
        if not self.tdata:
            return
        idx = sorted((int(x) for x in self.tv_lines.selection()), reverse=True)
        if not idx or not messagebox.askyesno("Xoá", "Xoá %d dòng?" % len(idx)):
            return
        for i in idx:
            del self.tdata["lines"][i]
        self._after_edit(None)

    def _save_transcript(self, quiet=False):
        if self.tdata and self.ep_dir:
            F.save_transcript(self.tdata, str(self.ep_dir / "transcript.json"))
            if not quiet:
                self._log("💾 Đã lưu transcript.")

    def _copy_transcript(self):
        if not self.tdata:
            return messagebox.showinfo("Chưa có", "Chưa có transcript.")
        unk = sum(1 for ln in self.tdata["lines"] if F.label_of(self.tdata, ln) == "?")
        if unk and not messagebox.askyesno("Còn dòng chưa rõ người nói",
                                           "%d dòng chưa có tên người nói ('?'). Vẫn copy?" % unk):
            return
        self._save_transcript(quiet=True)
        self._clip(F.transcript_text(self.tdata))
        self._log("📋 Đã copy TRANSCRIPT → dán vào chat mới: /analyze + (context: … notes: …)")

    def _export_srt(self):
        if not self.tdata or not self.ep_dir:
            return
        p = self.ep_dir / "dialogue.srt"
        F.write_srt(F.build_cues(self.tdata), str(p))
        self._log("💾 %s" % p)

    # ════════════════════════════════════════════════════════ TAB NƯỚC ĐI
    def _tab_moves(self, f):
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=2)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text="Khối MOVES (dán từ /qcmoves)", style="H.TLabel").grid(row=0, column=0, sticky="w")
        self.txt_moves = tk.Text(f, wrap="word", font=("Consolas", 10), undo=True)
        self.txt_moves.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(f)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)
        bf = ttk.Frame(right)
        bf.grid(row=0, column=0, sticky="w")
        ttk.Button(bf, text="Dán từ clipboard", command=self._paste_moves).pack(side="left")
        ttk.Button(bf, text="KIỂM TRA", style="Run.TButton", command=self._check_moves).pack(side="left", padx=6)
        ttk.Button(bf, text="👁 Xem trước thẻ đã chọn", command=self._preview_move).pack(side="left")
        self.lbl_sides = ttk.Label(right, text="", style="Hint.TLabel", wraplength=700, justify="left")
        self.lbl_sides.grid(row=1, column=0, sticky="w", pady=4)
        cols = ("k", "move", "cls", "eval", "anchor", "time", "status", "badge")
        self.tv_moves = ttk.Treeview(right, columns=cols, show="headings")
        for c, w, t in (("k", 34, "#"), ("move", 210, "Nước"), ("cls", 80, "Loại"), ("eval", 50, "Eval"),
                        ("anchor", 220, "Anchor"), ("time", 70, "Dừng lúc"), ("status", 200, "Trạng thái"),
                        ("badge", 50, "Badge")):
            self.tv_moves.heading(c, text=t)
            self.tv_moves.column(c, width=w, stretch=c in ("move", "anchor", "status"), anchor="w")
        for tag, color in ROW_TAGS.items():
            self.tv_moves.tag_configure(tag, background=color)
        self.tv_moves.grid(row=2, column=0, sticky="nsew")
        self.tv_moves.bind("<Double-1>", self._on_move_double)
        self.txt_issues = tk.Text(right, height=6, wrap="word", font=("Segoe UI", 9), bg="#FFFDF2")
        self.txt_issues.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        av = ttk.LabelFrame(right, text=" Avatar (trống = tự cắt mặt từ khung hình) ", padding=6)
        av.grid(row=4, column=0, sticky="ew", pady=6)
        av.columnconfigure(1, weight=1)
        self.v_av_w = tk.StringVar()
        self.v_av_b = tk.StringVar()
        self._path_row(av, 0, "Trắng (White)", self.v_av_w, "file", [("Ảnh", "*.png *.jpg *.jpeg *.webp")], width=40)
        self._path_row(av, 1, "Đen (Black)", self.v_av_b, "file", [("Ảnh", "*.png *.jpg *.jpeg *.webp")], width=40)
        ttk.Label(right, style="Hint.TLabel",
                  text="Double-click cột Badge để đổi vị trí: auto (cạnh mặt lớn nhất) → L → C → R.").grid(
            row=5, column=0, sticky="w")

    def _paste_moves(self):
        try:
            t = self.clipboard_get()
        except tk.TclError:
            return
        self.txt_moves.delete("1.0", "end")
        self.txt_moves.insert("1.0", t)
        self._check_moves()

    def _save_moves(self):
        if self.ep_dir:
            (self.ep_dir / "moves.txt").write_text(self.txt_moves.get("1.0", "end").strip() + "\n", encoding="utf-8")

    def _check_moves(self):
        if not self._need_ep():
            return False
        self._save_moves()
        text = self.txt_moves.get("1.0", "end")
        g, errs = F.parse_moves(text)
        issues = list(errs)
        if self.tdata:
            F.resolve_anchors(g, self.tdata)
        else:
            issues.append("Chưa có transcript → chưa khớp được anchor (tab THOẠI).")
        pos = self.ep.get("badge_pos") or []
        for i, mv in enumerate(g.moves):
            mv.badge_pos = pos[i] if i < len(pos) and pos[i] else "auto"
        self.ep["badge_pos"] = [m.badge_pos for m in g.moves]
        for k, msg in F.validate_moves(g):
            issues.append(("Nước %d: " % k if k else "") + msg)
        self.game = g
        self._refresh_moves_tree()
        self.lbl_sides.configure(text="%s · %s · %s\nWHITE: %s = %s     BLACK: %s = %s     START %s     RESULT %s" % (
            g.series, g.episode, g.scene, g.white_name, ", ".join(g.white_members), g.black_name,
            ", ".join(g.black_members), g.start_eval.raw, g.result))
        errors = [m for m in g.moves if m.status.startswith("error")]
        self.txt_issues.delete("1.0", "end")
        self.txt_issues.insert("1.0", "\n".join(issues) if issues else "Không có lỗi cú pháp/luật.")
        self._save_episode()
        ok = not errs and not errors and bool(self.tdata) and bool(g.moves)
        self._log("%s Kiểm tra MOVES: %d nước, %d lỗi cú pháp, %d lỗi anchor, %d ghi chú." % (
            "✅" if ok else "⚠️", len(g.moves), len(errs), len(errors), len(issues)))
        return ok

    def _refresh_moves_tree(self):
        tv = self.tv_moves
        tv.delete(*tv.get_children())
        if not self.game:
            return
        for i, mv in enumerate(self.game.moves):
            tag = "error" if mv.status.startswith("error") else "warn" if mv.status.startswith("warn") else "ok"
            tv.insert("", "end", iid=str(i), tags=[tag], values=(
                i + 1, "%s %s · %s" % (F.move_number(i), mv.mover_display, mv.name), mv.cls, mv.eval.raw,
                str(mv.anchor), F.fmt_ts(mv.time) if mv.time is not None else "—", mv.status, mv.badge_pos))

    def _on_move_double(self, event):
        iid = self.tv_moves.identify_row(event.y)
        col = self.tv_moves.identify_column(event.x)
        if not iid or not self.game:
            return
        i = int(iid)
        if col == "#8":
            order = ["auto", "L", "C", "R"]
            mv = self.game.moves[i]
            mv.badge_pos = order[(order.index(mv.badge_pos) + 1) % 4] if mv.badge_pos in order else "auto"
            self.ep["badge_pos"] = [m.badge_pos for m in self.game.moves]
            self._save_episode()
            self._refresh_moves_tree()
        elif self.tdata and self.game.moves[i].time is not None:
            t = self.game.moves[i].time
            self._play(t - 4.0, t)

    def _episode_cfg(self) -> dict:
        """Gom cai dat kenh + tap -> episode.json cho lacg_build.py."""
        self._save_settings(quiet=True)
        self._save_episode()
        cfg = dict(self.ep)
        cfg.update({"transcript": str(self.ep_dir / "transcript.json"), "moves": str(self.ep_dir / "moves.txt"),
                    "sfx_dir": self.s.get("sfx_dir", ""), "draft_root": self.s.get("draft_root", ""),
                    "font_regular": self.s.get("font_regular", ""), "font_bold": self.s.get("font_bold", "")})
        for k in ("cps", "pause", "review_hold", "sub_size", "width", "height", "fps"):
            cfg[k] = self.s.get(k, DEFAULT_SETTINGS[k])
        save_json(self.ep_dir / "episode.json", cfg)
        self.ep = cfg
        return cfg

    def _preview_move(self):
        sel = self.tv_moves.selection()
        if not sel or not self.game:
            return messagebox.showinfo("Chọn nước", "Bấm KIỂM TRA rồi chọn 1 nước trong bảng.")
        i = int(sel[0])
        if self.game.moves[i].time is None:
            return messagebox.showerror("Chưa có thời điểm", "Nước này chưa khớp được anchor.")
        self._episode_cfg()
        out = self.ep_dir / "render" / ("preview_M%02d.png" % (i + 1))

        def work():
            try:
                import lacg_build as B
                ep = B.Episode(str(self.ep_dir / "episode.json"))
                B.preview_move(ep, self.game, i, str(out))
                self.q.put(("log", "👁 Xem trước: %s" % out))
                self.q.put(("open", str(out)))
            except Exception as ex:
                self.q.put(("log", "❌ Xem trước lỗi: %s" % ex))
        threading.Thread(target=work, daemon=True).start()

    # ════════════════════════════════════════════════════════ TAB DRAFT
    def _tab_draft(self, f):
        f.columnconfigure(1, weight=1)
        self.v_draft_name = tk.StringVar()
        ttk.Label(f, text="Tên draft CapCut").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_draft_name, width=40).grid(row=0, column=1, sticky="w", padx=6)
        bf = ttk.Frame(f)
        bf.grid(row=1, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(bf, text="SINH DRAFT CAPCUT", style="Run.TButton", command=self._run_build).pack(side="left")
        ttk.Button(bf, text="Dừng", command=self._stop).pack(side="left", padx=6)
        ttk.Button(bf, text="📁 Khung hình (frames)", command=lambda: self.ep_dir and open_path(self.ep_dir / "frames")).pack(side="left", padx=6)
        ttk.Label(f, style="Hint.TLabel", justify="left",
                  text="App render từng đoạn dừng hình (khung hình đứng + badge + thẻ gõ chữ + thanh eval) thành mp4, rồi ghép\n"
                       "với clip gốc, ô player, thanh eval, phụ đề và SFX thành draft CapCut. Mở CapCut để xem, chỉnh, export.\n"
                       "Đóng CapCut trước khi sinh lại một draft cùng tên.").grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="CHAPTERS (copy sang chat, chạy /metadata chapters: …)", style="H.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(14, 4))
        self.txt_chap = tk.Text(f, height=14, font=("Consolas", 10))
        self.txt_chap.grid(row=4, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(4, weight=1)
        ttk.Button(f, text="📋 Copy CHAPTERS", command=lambda: self._clip(self.txt_chap.get("1.0", "end").strip())).grid(
            row=5, column=0, sticky="w", pady=6)

    def _run_build(self):
        if not self._need_ep() or not self._guard_busy():
            return
        if not self._check_moves():
            self.nb.select(2)
            return messagebox.showerror("Còn lỗi", "Khối MOVES còn lỗi cú pháp/anchor (hoặc chưa có transcript) — "
                                                   "xem tab NƯỚC ĐI.")
        cfg = self._episode_cfg()
        name = cfg.get("draft_name") or self.ep_dir.name
        target = Path(cfg.get("draft_root", "")) / name
        if target.exists() and not messagebox.askyesno("Ghi đè draft?", "Draft '%s' đã có trong CapCut. Ghi đè?\n"
                                                                         "(Mọi chỉnh tay trong draft đó sẽ mất.)" % name):
            return
        cmd = [PY, "-u", str(HERE / "lacg_build.py"), "--episode", str(self.ep_dir / "episode.json")]

        def done(ok):
            if not ok:
                return
            cp = self.ep_dir / "chapters.txt"
            self.txt_chap.delete("1.0", "end")
            if cp.is_file():
                self.txt_chap.insert("1.0", cp.read_text(encoding="utf-8"))
            if self.game:
                done_list = load_json(DONE_FILE, [])
                key = (self.game.series, self.game.episode, self.game.scene)
                if key not in [(d.get("series"), d.get("episode"), d.get("scene")) for d in done_list]:
                    done_list.append({"series": key[0], "episode": key[1], "scene": key[2], "folder": str(self.ep_dir)})
                    save_json(DONE_FILE, done_list)
            self._log("👉 Mở CapCut → draft '%s'. Sau đó copy CHAPTERS và chạy /metadata." % name)
        self._spawn(cmd, "Sinh draft CapCut", done)

    # ════════════════════════════════════════════════════════ TAB THUMBNAIL
    def _tab_thumb(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text="Dán kết quả /thumbnail (các dòng A1…C3 + khối tsv)", style="H.TLabel").grid(row=0, column=0, sticky="w")
        self.txt_thumb = tk.Text(f, wrap="none", font=("Consolas", 10))
        self.txt_thumb.grid(row=1, column=0, sticky="nsew")
        bf = ttk.Frame(f)
        bf.grid(row=2, column=0, sticky="w", pady=6)
        ttk.Button(bf, text="Dán từ clipboard", command=lambda: (self.txt_thumb.delete("1.0", "end"),
                                                                 self.txt_thumb.insert("1.0", self.clipboard_get()))).pack(side="left")
        ttk.Button(bf, text="TẠO THUMBNAIL", style="Run.TButton", command=self._make_thumbs).pack(side="left", padx=6)
        ttk.Button(bf, text="📁 Mở thư mục thumbs", command=lambda: self.ep_dir and open_path(self.ep_dir / "thumbs")).pack(side="left")
        ttk.Label(f, style="Hint.TLabel",
                  text="Khung hình lấy từ frames/M01.png… (sinh khi SINH DRAFT). Mặt sai thì sửa số M<k> trong spec rồi tạo lại.").grid(
            row=3, column=0, sticky="w")

    def _make_thumbs(self):
        if not self._need_ep():
            return
        specs, errs = F.parse_thumb_spec(self.txt_thumb.get("1.0", "end"))
        for e in errs:
            self._log("⚠️  %s" % e)
        if not specs:
            return
        out_dir = self.ep_dir / "thumbs"
        out_dir.mkdir(exist_ok=True)
        font_bold = self.s.get("font_bold", "")

        def work():
            try:
                from PIL import Image
                import lacg_render as R
                for sp in specs:
                    frames = {}
                    for k in sp.frames:
                        p = self.ep_dir / "frames" / ("M%02d.png" % k)
                        if not p.is_file():
                            raise FileNotFoundError("Thiếu %s — chạy SINH DRAFT trước." % p.name)
                        frames[k] = Image.open(p)
                    img = R.compose_thumbnail(sp, frames, font_bold)
                    path = out_dir / ("%s.png" % sp.id)
                    img.save(path)
                    self.q.put(("log", "🖼  %s  ← %s" % (path.name, sp.title)))
                self.q.put(("open", str(out_dir)))
            except Exception as ex:
                self.q.put(("log", "❌ Thumbnail lỗi: %s" % ex))
        threading.Thread(target=work, daemon=True).start()

    # ════════════════════════════════════════════════════════ CHAY LENH NEN
    def _guard_busy(self) -> bool:
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Đang chạy", "Đang có tác vụ chạy — chờ xong hoặc bấm Dừng.")
            return False
        return True

    def _spawn(self, cmd, label, on_done=None, hide=""):
        shown = " ".join(('"%s"' % c if " " in c else c) for c in cmd)
        if hide:
            shown = shown.replace(hide, "•••")
        self._log("▶ %s\n   %s" % (label, shown))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            self.proc = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                                         creationflags=NO_WINDOW)
        except Exception as ex:
            self._log("❌ Không chạy được: %s" % ex)
            return

        def reader(p):
            for line in p.stdout:
                self.q.put(("log", line.rstrip("\n")))
            rc = p.wait()
            self.q.put(("done", (label, rc, on_done)))
        threading.Thread(target=reader, args=(self.proc,), daemon=True).start()

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self._log("■ Đã dừng.")

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self._log(val)
                elif kind == "open":
                    open_path(val)
                elif kind == "done":
                    label, rc, cb = val
                    self._log("✅ %s xong." % label if rc == 0 else "❌ %s lỗi (mã %d)." % (label, rc))
                    if cb:
                        cb(rc == 0)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _log(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _clip(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _on_close(self):
        self._stop_play()
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Thoát", "Đang có tác vụ chạy. Dừng và thoát?"):
                return
            self.proc.terminate()
        try:
            self._save_settings(quiet=True)
            self._save_episode()
            if self.tdata:
                self._save_transcript(quiet=True)
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    ChessStudio().mainloop()
