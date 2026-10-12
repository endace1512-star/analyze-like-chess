# -*- coding: utf-8 -*-
"""
lacg_check.py — KIEM TRA CAI DAT cho Chess Studio
=================================================
python lacg_check.py [--hf-token hf_xxx] [--sfx-dir sfx] [--deep]
  --deep : tai thu model pyannote (lau hon, can mang)
In ra tung dong ✅ / ⚠️ / ❌ de biet thieu gi.
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys

OK, WARN, BAD = "✅", "⚠️ ", "❌"
problems = 0


def say(mark: str, msg: str) -> None:
    global problems
    if mark == BAD:
        problems += 1
    print("%s %s" % (mark, msg), flush=True)


def try_import(name: str, hint: str, required: bool = True):
    try:
        m = importlib.import_module(name)
        say(OK, "%s %s" % (name, getattr(m, "__version__", "")))
        return m
    except Exception as ex:
        say(BAD if required else WARN, "%s: chưa cài / lỗi (%s) → %s" % (name, str(ex)[:120], hint))
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hf-token", default="")
    ap.add_argument("--sfx-dir", default="")
    ap.add_argument("--deep", action="store_true")
    a = ap.parse_args()

    v = sys.version_info
    say(OK if (3, 10) <= v[:2] <= (3, 13) else BAD, "Python %d.%d.%d (%s) — cần 3.10–3.13" % (v[0], v[1], v[2], sys.executable))
    for exe in ("ffmpeg", "ffprobe", "ffplay"):
        p = shutil.which(exe)
        say(OK if p else (BAD if exe != "ffplay" else WARN), "%s: %s" % (exe, p or "không thấy trên PATH (cài ffmpeg bản full)"))

    print("── App + dựng draft", flush=True)
    try:
        import tkinter  # noqa: F401
        say(OK, "tkinter")
    except Exception:
        say(WARN, "tkinter: không có (chỉ cần cho giao diện — dùng Python từ python.org)")
    try_import("PIL", "pip install pillow")
    try_import("numpy", "pip install numpy")
    try_import("cv2", "pip install opencv-python (dò mặt để đặt badge / cắt avatar)", required=False)
    if try_import("pycapcut", "pip install pycapcut"):
        try:
            import pymediainfo
            say(OK if pymediainfo.MediaInfo.can_parse() else BAD, "pymediainfo đọc được media")
        except Exception as ex:
            say(BAD, "pymediainfo: %s" % ex)

    print("── Nhận thoại (GPU)", flush=True)
    torch = try_import("torch", "xem INSTALL_LACG.md bước 3 (torch bản CUDA)")
    if torch:
        if torch.cuda.is_available():
            say(OK, "CUDA: %s (torch CUDA %s)" % (torch.cuda.get_device_name(0), torch.version.cuda))
        else:
            say(BAD, "torch KHÔNG thấy GPU (bản CPU?) → cài lại torch bản cu128, xem INSTALL_LACG.md")
    try_import("whisperx", "pip install whisperx")
    try_import("demucs", "pip install demucs", required=False)
    pa = try_import("pyannote.audio", "đi kèm whisperx")
    try_import("torchcodec", "cần FFmpeg bản SHARED (có file .dll) trên PATH — xem INSTALL_LACG.md bước 2",
               required=False)

    print("── HuggingFace", flush=True)
    models = ["pyannote/speaker-diarization-community-1", "pyannote/wespeaker-voxceleb-resnet34-LM",
              "pyannote/speech-separation-ami-1.0"]
    if not a.hf_token:
        say(WARN, "Chưa nhập HuggingFace token → không phân biệt được người nói")
    else:
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=a.hf_token)
            who = api.whoami()
            say(OK, "Token hợp lệ (%s)" % who.get("name", "?"))
            for m in models:
                optional = "separation" in m
                try:
                    from huggingface_hub import hf_hub_download
                    hf_hub_download(m, "config.yaml", token=a.hf_token)
                    say(OK, "Truy cập được %s" % m)
                except Exception as ex:
                    say(WARN if optional else BAD,
                        "%s: chưa truy cập được → mở huggingface.co/%s bấm 'Agree' (%s)" % (m, m, type(ex).__name__))
        except Exception as ex:
            say(BAD, "Token lỗi: %s" % ex)
        if a.deep and pa and torch:
            try:
                from pyannote.audio import Model
                try:
                    Model.from_pretrained(models[1], token=a.hf_token)
                except TypeError:
                    Model.from_pretrained(models[1], use_auth_token=a.hf_token)
                say(OK, "Tải được model vân tay giọng")
            except Exception as ex:
                say(BAD, "Không tải được model vân tay giọng: %s" % ex)

    print("── Font + SFX", flush=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import lacg_render as R
        say(OK if R.find_font(False) else WARN, "Font thường: %s" % (R.find_font(False) or "không thấy (chọn ở tab KÊNH)"))
        say(OK if R.find_font(True) else WARN, "Font đậm: %s" % (R.find_font(True) or "không thấy"))
    except Exception as ex:
        say(BAD, "lacg_render: %s" % ex)
    names = ["brilliant", "great", "best", "excellent", "good", "book", "inaccuracy", "mistake", "miss", "blunder",
             "move", "typing", "checkmate"]
    if a.sfx_dir and os.path.isdir(a.sfx_dir):
        have = {os.path.splitext(f)[0].lower() for f in os.listdir(a.sfx_dir)}
        miss = [n for n in names if n not in have]
        say(OK if not miss else WARN, "SFX: %d/%d file%s" % (len(names) - len(miss), len(names),
                                                            (" — thiếu: " + ", ".join(miss)) if miss else ""))
    else:
        say(WARN, "Chưa có thư mục SFX (%s)" % (a.sfx_dir or "chưa chọn"))
    print("── %s" % ("Đủ để chạy." if not problems else "Còn %d lỗi ❌ cần sửa." % problems), flush=True)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
