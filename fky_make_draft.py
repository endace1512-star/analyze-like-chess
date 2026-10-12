# -*- coding: utf-8 -*-
"""
fky_make_draft.py — SINH DRAFT CAPCUT từ dữ liệu tập (chạy bởi nút "SINH DRAFT CAPCUT" trong app)
=================================================================================================
Luồng:
  1) ALIGN: dùng lại đúng thuật toán align của make_video.py (khớp lời VO từng scene với sub.srt)
     -> span (start, end) mỗi scene. Ghi luôn scene-aligned.srt cạnh sub.srt (phụ đề CapCut).
  2) Mốc đầu neo về 0s, mốc cuối neo về độ dài VO.mp3 (giống nút ALIGN + BUILD cũ).
  3) Dựng danh sách Beat (scene / concept) rồi gọi fky_draft_builder.build_draft():
       - dòng đáy: nền trắng chạy hết video
       - dòng base: ảnh 001.jpg, 002.jpg… (beat SCENE scale cố định 104%, không keyframe — đổi 24/09/2026)
       - dòng element (chỉ khi có ảnh con 042a.png, 042b.png… — ĐỢT 2)
       - dòng text: card sign-title (đọc từ tag [SIGN-TITLE: …] trong cột action)
       - audio: VO.mp3 đầy đủ
  4) Draft ghi vào thư mục draft CapCut, tên theo --draft-name.

Cách gọi (app tự gọi, người dùng không cần gõ):
  py fky_make_draft.py --scenes-json scenes.json --srt sub.srt --audio VO.mp3 --images <folder ảnh>
                       --draft-name "ep01" [--draft-root <thư mục draft>] [--font C:\\...\\Montserrat-Black.ttf]
                       [--align-script <đường dẫn make_video.py>]

scenes.json: danh sách [{"id": "001", "vo": "...", "bg": "fred-white", "action": "...", "kind": "concept"}, ...]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8")   # log tiếng Việt trên console Windows
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DEFAULT_DRAFT_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "CapCut", "User Data", "Projects", "com.lveditor.draft")
IMG_EXTS = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]
LIST_CELL_ORDER = [1, 3, 4, 6, 7, 9]          # LIST BEAT: thứ tự ưu tiên ô
# 25/09/2026: thêm "metaphor" = cảnh ẩn dụ toàn khung (Fred đóng nhiều vai), 1 ảnh trọn vẹn
VALID_FN = ("present", "demonstrate", "undergo", "list", "solo", "metaphor")
# Beat CÂU NỐI (08/09/2026): cờ [FN: pose:<tên>] trên dòng fred-white -> KHÔNG gen ảnh mới,
# app lấy sẵn file <tên>.jpg trong thư mục THƯ VIỆN TƯ THẾ. Ở đây chỉ cần nhận diện để
# fn = "pose" và cắt cờ khỏi phần tả Fred; dòng loại này không có {cell N} nên không có mảnh.
POSE_FN_RE = re.compile(r"^\s*\[FN:\s*pose\s*:\s*([A-Za-z0-9][A-Za-z0-9_\-]*)\s*\]\s*",
                        re.IGNORECASE)
# Ô của mảnh: "{cell 3}", từ 25/09/2026 thêm được mốc chữ: "{cell 3 @amygdala}" = mảnh này
# hiện ra đúng lúc VO đọc tới chữ "amygdala".
# 02/10/2026: thêm "{pov @sorry}" = ẢNH POV (Fred nhìn từ sau lưng, từ vai trở lên, cầm điện
# thoại, nền trắng) làm ảnh phụ của cảnh nhắn tin — tách nền, sát mép dưới, trượt từ dưới lên.
# Nhóm 1 = số ô, nhóm 2 = "pov" (ô POV), nhóm 3 = chữ mốc (có thể trống).
CELL_RE = re.compile(r"\{\s*(?:cell\s*(\d?)|(pov))\s*(?:@\s*([^}]*?))?\s*\}", re.IGNORECASE)
POV_CELL = 10       # "ô" giả của ảnh POV (ngoài lưới 1..9). PHẢI khớp POV_CELL ở app + builder
# Tag biên tập, bỏ khỏi phần tả mảnh: [SIGN-TITLE: …], (25/09/2026) [HOOK-TITLE]
# và (01/10/2026) [FINDING: …] = dòng chữ kết quả nghiên cứu
TITLE_TAG_RE = re.compile(r"\s*\[(?:(?:SIGN|HOOK)-TITLE|FINDING)(?::[^\]]*)?\]\s*", re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════════
#  2 HÀM LÕI (theo luật đã chốt trong BRIEF)
# ══════════════════════════════════════════════════════════════════════
# Cách viết CŨ của /scenes: mỗi mảnh kết thúc bằng "(left)", "(centre)", "(right)", "(above)"…
# -> quy về ô grid 3x3 khi action CHƯA có "{cell N}" (fallback, để tập cũ vẫn dùng được).
_ZONE_CELL = {
    "top-left": 1, "upper-left": 1, "top left": 1, "upper left": 1,
    "top": 2, "above": 2, "overhead": 2, "top-centre": 2, "top-center": 2,
    "top centre": 2, "top center": 2, "above him": 2, "over his head": 2,
    "top-right": 3, "upper-right": 3, "top right": 3, "upper right": 3,
    "left": 4, "middle-left": 4, "centre-left": 4, "center-left": 4, "far left": 4,
    "left side": 4, "to his left": 4, "on his left": 4, "his left": 4,
    "centre": 5, "center": 5, "middle": 5, "in the middle": 5,
    "right": 6, "middle-right": 6, "centre-right": 6, "center-right": 6, "far right": 6,
    "right side": 6, "to his right": 6, "on his right": 6, "his right": 6,
    "bottom-left": 7, "lower-left": 7, "bottom left": 7, "lower left": 7,
    "bottom": 8, "below": 8, "beneath": 8, "underneath": 8, "bottom-centre": 8,
    "bottom-center": 8, "below him": 8, "at his feet": 8,
    "bottom-right": 9, "lower-right": 9, "bottom right": 9, "lower right": 9,
}
_ZONE_RE = re.compile(r"\(\s*([A-Za-z][A-Za-z \-']{0,24}?)\s*\)")
# Mảnh đầu tiên thường dính pose của Fred. Khuôn CHUẨN (bắt buộc từ v7.5) là
#   "[FN: x] [fred] <dáng>, <biểu cảm>, <mảnh 1> {cell N}"
# -> cắt tại DẤU PHẨY CUỐI trước {cell} là ra đúng cụm danh từ của vật.
# Khuôn CŨ không có dấu phẩy ("[fred] to one side gesturing toward a bubble (left)")
# -> mới cần dò từ nối. KHÔNG được dò từ nối trên chuỗi có dấu phẩy: những chữ như
# "showing / presenting / toward" hoàn toàn có thể nằm GIỮA mô tả vật
# ("a mouth showing behind its edge") và sẽ cắt cụt mô tả.
_LEAD_CUT_RE = re.compile(
    r"\b(?:towards?|gesturing (?:at|to)|pointing (?:at|to)|presenting|showing|"
    r"beside him|next to him|around him|in front of him|floating near him|near him)\s+",
    flags=re.IGNORECASE)
# Từ nối còn sót ở ĐẦU mảnh sau khi đã cắt theo dấu phẩy (vd ", gesturing toward a bubble").
_LEAD_PREFIX_RE = re.compile(
    r"^(?:(?:and|then|while)\s+)?"
    r"(?:gesturing|pointing|presenting|showing|indicating|holding out|reaching)?\s*"
    r"(?:towards?|at|to)\b\s+", flags=re.IGNORECASE)


def _zone_to_cell(txt):
    t = re.sub(r"\s+", " ", (txt or "").strip().lower())
    t = re.sub(r"^(?:to the|on the|at the|in the|the|his)\s+", "", t)
    t = re.sub(r"\s+(?:of (?:the )?frame|side of (?:the )?frame|of him)$", "", t)
    return _ZONE_CELL.get(t, 0)


def _clean_piece_desc(desc, first):
    """Dọn mô tả mảnh: bỏ token [tên], bỏ 'and'/',' đầu, và với mảnh ĐẦU (dính pose Fred)
    cắt sau từ nối cuối cùng (toward / gesturing at / presenting …)."""
    d = (desc or "").strip()
    had_token = bool(re.match(r"^\s*\[[^\]]+\]", d))
    d = re.sub(r"\[[^\]]+\]", " ", d)                     # bỏ [fred]…
    if first and (had_token or _LEAD_CUT_RE.search(d)):
        k = d.rfind(",")
        if k > 0 and len(d) - k > 3:
            d = d[k + 1:]                                 # khuôn chuẩn: cắt sau dấu phẩy CUỐI
        else:                                             # khuôn cũ (không phẩy): dò từ nối
            last = None
            for mm in _LEAD_CUT_RE.finditer(d):
                last = mm
            if last is not None:
                d = d[last.end():]
        d = _LEAD_PREFIX_RE.sub("", d.strip(), count=1)   # bỏ từ nối còn sót ở ĐẦU mảnh
    d = re.sub(r"^(?:,|and|then|plus|\s)+", "", d, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", d).strip(" ,;")


def parse_concept_action(action: str):
    """Đọc cột action của beat CONCEPT.

    Trả về (fn, fred_pose, [(mô_tả_mảnh, cell), ...])
      - fn: cờ đầu [FN: present|demonstrate|undergo|list|solo]; không có -> "present"
      - fred_pose: phần mô tả Fred (đoạn trước mảnh đầu tiên), đã bỏ tag [SIGN-TITLE] và [FN]
      - mảnh: mỗi đoạn kết thúc bằng "{cell N}" (N = 1..9). LIST beat thiếu ô -> gán theo 1>3>4>6>7>9.
    Action KHÔNG có {cell}: thử cách viết cũ "(left)/(right)/(above)…" -> quy về ô grid.
    Không có cả hai -> danh sách mảnh rỗng (draft chỉ đặt ảnh nền).
    """
    s = action or ""
    s = TITLE_TAG_RE.sub(" ", s)
    fn = "present"
    mp = POSE_FN_RE.match(s)
    if mp:                                   # [FN: pose:thinking] -> beat câu nối
        return "pose", s[mp.end():].strip(" ,"), []
    # 28/09/2026: "[FN: present pose:explaining]" = beat có mảnh nhưng ẢNH NỀN lấy từ thư viện
    # tư thế (nút CHÉP ẢNH POSE chép vào đúng số scene) -> ở đây chỉ cần bỏ qua phần "pose:…".
    m = re.match(r"^\s*\[FN:\s*([A-Za-z]+)(?:\s+pose\s*:\s*[A-Za-z0-9][A-Za-z0-9_\-]*)?\s*\]\s*",
                 s, flags=re.IGNORECASE)
    if m:
        cand = m.group(1).lower()
        if cand in VALID_FN:
            fn = cand
        s = s[m.end():]
    # markers = [(start, end, cell)] — ưu tiên {cell N}; không có thì dùng (zone) cũ
    markers = [(mm.start(), mm.end(),
                POV_CELL if mm.group(2) else (int(mm.group(1)) if mm.group(1) else 0))
               for mm in CELL_RE.finditer(s)]
    if not markers:
        for mm in _ZONE_RE.finditer(s):
            c = _zone_to_cell(mm.group(1))
            if c:
                markers.append((mm.start(), mm.end(), c))
    pieces = []
    pos = 0
    first_piece_start = None
    for (st, en, cell) in markers:
        seg = s[pos:st]
        if first_piece_start is None:
            raw = seg.strip()
            k = raw.rfind(",")
            first_piece_start = raw[:k].strip() if (k > 0 and len(raw) - k > 3) else ""
        desc = _clean_piece_desc(seg, first=(len(pieces) == 0))
        pieces.append([desc, cell if (1 <= cell <= 9 or cell == POV_CELL) else 0])
        pos = en
    if first_piece_start is None:
        fred_pose = s.strip(" ,")
    else:
        fred_pose = first_piece_start
    if fn == "list":
        used = {c for _, c in pieces if c}
        free = [c for c in LIST_CELL_ORDER if c not in used]
        for p in pieces:
            if not p[1]:
                p[1] = free.pop(0) if free else 5
    else:
        for p in pieces:
            if not p[1]:
                p[1] = 5
    return fn, fred_pose, [(d, c) for d, c in pieces]


def split_sentences(vo: str):
    """Tách câu theo . ! ? (cột vo đã TTS-safe)."""
    parts = re.split(r"(?<=[.!?])\s+", (vo or "").strip())
    return [p for p in parts if p.strip()]


# 23/09/2026: cắt cảnh theo VẾ (mỗi dấu phẩy / chấm) — mỗi vế một mảnh.
# PHẢI khớp luật CLAUSE trong -FKY_Claude_command_instructions.md (/scenes).
CLAUSE_MIN_WORDS = 4


def _n_words(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", s))


def split_clauses(vo: str):
    """Tách vế theo . ! ? và dấu phẩy (bỏ qua dấu phẩy trong số như 1,000).
    Vế dưới CLAUSE_MIN_WORDS từ gộp vào vế sau; vế cuối quá ngắn gộp vào vế trước."""
    parts = re.split(r"(?<=[.!?])\s+|(?<!\d),\s*|,\s+", (vo or "").strip())
    parts = [p.strip() for p in parts if p and p.strip()]
    out, carry = [], ""
    for p in parts:
        cur = f"{carry} {p}".strip() if carry else p
        if _n_words(cur) < CLAUSE_MIN_WORDS:
            carry = cur
            continue
        out.append(cur)
        carry = ""
    if carry:
        if out:
            out[-1] = f"{out[-1]} {carry}"
        else:
            out.append(carry)
    return out


def piece_units(vo: str, n_pieces: int):
    """Đơn vị để ghép với mảnh: vế nếu số vế khớp số mảnh; nếu không mà số câu khớp
    (tập cũ viết theo câu) thì dùng câu; còn lại dùng vế (mảnh dư dồn về vế cuối)."""
    clauses = split_clauses(vo)
    if len(clauses) == n_pieces:
        return clauses
    sents = split_sentences(vo)
    if len(sents) == n_pieces:
        return sents
    return clauses


def per_sentence_starts(span_start: float, span_end: float, sentences):
    """Chia thời điểm BẮT ĐẦU từng câu trong span của beat, tỉ lệ theo số từ mỗi câu.
    Trả về list giây (tuyệt đối), cùng độ dài với sentences."""
    n = len(sentences)
    if n == 0:
        return []
    counts = [max(1, len(re.findall(r"[A-Za-z0-9']+", s))) for s in sentences]
    total = float(sum(counts))
    span = max(0.0, float(span_end) - float(span_start))
    starts, acc = [], 0
    for c in counts:
        starts.append(span_start + span * (acc / total))
        acc += c
    return starts


# ══════════════════════════════════════════════════════════════════════
#  MẢNH HIỆN ĐÚNG CHỮ (25/09/2026)
# ══════════════════════════════════════════════════════════════════════
# Mỗi mảnh đại diện cho 1 chữ / cụm chữ trong lời VO của dòng. /scenes ghi nó ở ô:
# "{cell 3 @amygdala}". App tìm chữ đó trong VO, lấy đúng thời điểm TTS đọc tới nó
# (nội suy trong từng cue SRT) rồi cho mảnh trượt vào ngay trước chữ đó.
# Dòng cũ chưa có @: thử nhãn trong ngoặc kép của mảnh ("AMYGDALA"), không thấy thì lùi về
# đầu vế như trước (per_sentence_starts).
def piece_anchors(action: str):
    """Danh sách chữ mốc theo thứ tự mảnh ('' nếu mảnh không ghi @)."""
    s = TITLE_TAG_RE.sub(" ", action or "")
    return [(mm.group(3) or "").strip().strip("\"'") for mm in CELL_RE.finditer(s)]


def _norm(text: str):
    return re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'"))


def word_times(rows, cues):
    """Thời điểm (giây, VO gốc) của TỪNG TỪ trong cột vo của mọi dòng.
    Trục từ của SRT: nội suy đều trong [đầu, cuối] mỗi cue (không kéo qua khoảng nghỉ như
    align, để mốc chữ không bị trễ). Khớp từ dòng <-> từ SRT bằng SequenceMatcher; từ không
    khớp được nội suy giữa 2 từ khớp gần nhất. Trả về list (mỗi dòng 1 list giây)."""
    from difflib import SequenceMatcher
    import bisect
    tw, tt = [], []
    for st, en, text in cues:
        ws = _norm(text)
        for j, w in enumerate(ws):
            tw.append(w)
            tt.append(st + (en - st) * j / max(1, len(ws)))
    sw, owner = [], []
    for ri, r in enumerate(rows):
        ws = _norm(r.get("vo", "")) or [""]
        sw.extend(ws)
        owner.extend([ri] * len(ws))
    s2t = {}
    for i, j, n in SequenceMatcher(None, sw, tw, autojunk=False).get_matching_blocks():
        for k in range(n):
            s2t[i + k] = tt[j + k]
    keys = sorted(s2t)
    out = [[] for _ in rows]
    for idx in range(len(sw)):
        if idx in s2t:
            t = s2t[idx]
        elif keys:
            p = bisect.bisect_left(keys, idx)
            lo = keys[p - 1] if p > 0 else keys[0]
            hi = keys[p] if p < len(keys) else keys[-1]
            t = s2t[lo] if lo == hi else s2t[lo] + (s2t[hi] - s2t[lo]) * (idx - lo) / (hi - lo)
        else:
            t = 0.0
        out[owner[idx]].append(t)
    return out


def find_anchor(vo_words, anchor: str, start_at: int = 0):
    """Vị trí từ đầu tiên của cụm chữ mốc trong vo_words (tìm từ start_at trở đi, rồi mới
    tìm từ đầu). Khớp chính xác trước, sau đó khớp theo gốc từ (anxious ~ anxiety)."""
    aw = _norm(anchor)
    if not aw:
        return None
    n = len(aw)
    for lo in (start_at, 0):
        for i in range(lo, len(vo_words) - n + 1):
            if vo_words[i:i + n] == aw:
                return i
    stem = lambda w: w[:max(4, len(w) - 3)]
    for lo in (start_at, 0):
        for i in range(lo, len(vo_words) - n + 1):
            if all(stem(a) == stem(b) for a, b in zip(vo_words[i:i + n], aw)):
                return i
    return None


def auto_anchor(desc: str):
    """Mảnh chưa ghi @: lấy nhãn trong ngoặc kép (vd tag reading "AMYGDALA")."""
    m = re.search(r"\"([^\"]{2,40})\"", desc or "")
    return m.group(1) if m else ""


# ══════════════════════════════════════════════════════════════════════
#  TIỆN ÍCH
# ══════════════════════════════════════════════════════════════════════
def load_align_module(path):
    """Nạp make_video.py (đã bung từ app) để dùng lại parse_srt() + align()."""
    spec = importlib.util.spec_from_file_location("fky_align", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_white_png(path, w=1920, h=1080):
    """Tạo ảnh PNG trắng đục w×h bằng thư viện chuẩn (không cần PIL/ffmpeg)."""
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = b"\x00" + b"\xff" * (w * 3)
    raw = row * h

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return path


def find_image(folder, stem):
    """stem = '042' hoặc '042a' -> đường dẫn ảnh (thử png/jpg/…), None nếu không có."""
    for ext in IMG_EXTS:
        p = os.path.join(folder, stem + ext)
        if os.path.isfile(p):
            return p
    return None


def find_base_image(folder, num):
    for pat in (f"{num:03d}", f"{num:02d}", f"{num}"):
        p = find_image(folder, pat)
        if p:
            return p
    return None


def find_element_image(folder, stem):
    """Ảnh con 042a: tìm trong folder ảnh, rồi trong folder con 'elements' / 'element'."""
    for d in (folder, os.path.join(folder, "elements"), os.path.join(folder, "element")):
        if os.path.isdir(d):
            p = find_image(d, stem)
            if p:
                return p
    return None


def _already_cut(im):
    """PNG đã tách nền rồi = có kênh alpha và cả 4 góc trong suốt."""
    if im.mode != "RGBA":
        return False
    w, h = im.size
    return all(im.getpixel(p)[3] == 0 for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))


def element_cutout(src, thr=235, seed_bottom=True):
    """Ảnh con gen ra có NỀN TRẮNG ĐỤC -> đặt lên lớp trên sẽ che mất Fred. Hàm này tách
    nền trắng thành TRONG SUỐT (chỉ vùng trắng NỐI với mép ảnh; vùng trắng bên trong vật —
    vd lòng bong bóng chat — giữ nguyên). Cần Pillow + numpy; nếu thiếu thì trả về ảnh gốc
    (CapCut vẫn mở được, nhưng lớp sẽ che nền).
    24/09/2026 theo yêu cầu người dùng: ghi đè NGAY TẠI CHỖ — 042a.jpg -> 042a.png trong
    cùng folder rồi XOÁ file gốc, không giữ folder cutout riêng. PNG đã tách thì bỏ qua.
    02/10/2026: ảnh POV có vai Fred bị mép DƯỚI cắt ngang — áo sơ mi trắng chạm mép dưới, lan
    từ mép đó sẽ ăn mất áo -> seed_bottom=False: chỉ lan từ mép trên / trái / phải (nền trắng
    ở hai góc dưới vẫn được tách vì nó nối với mép trái / phải)."""
    try:
        from PIL import Image
        import numpy as np
    except Exception:
        return src, False
    dst = os.path.splitext(src)[0] + ".png"
    try:
        im = Image.open(src)
        im.load()
        if src.lower().endswith(".png") and _already_cut(im.convert("RGBA") if im.mode != "RGBA" else im):
            return src, True
        im = im.convert("RGBA")
        a = np.array(im)
        rgb = a[:, :, :3]
        white = (rgb.min(axis=2) >= thr)
        H, W = white.shape
        reach = np.zeros_like(white)
        reach[0, :] = white[0, :]
        if seed_bottom:
            reach[-1, :] = white[-1, :]
        reach[:, 0] = white[:, 0]; reach[:, -1] = white[:, -1]
        # lan từ mép vào theo 4 hướng quét, lặp tới khi không đổi (nhanh: mỗi bước là 1 dòng/cột)
        for _ in range(60):
            before = int(reach.sum())
            for x in range(1, W):
                reach[:, x] |= reach[:, x - 1] & white[:, x]
            for x in range(W - 2, -1, -1):
                reach[:, x] |= reach[:, x + 1] & white[:, x]
            for y in range(1, H):
                reach[y, :] |= reach[y - 1, :] & white[y, :]
            for y in range(H - 2, -1, -1):
                reach[y, :] |= reach[y + 1, :] & white[y, :]
            if int(reach.sum()) == before:
                break
        alpha = a[:, :, 3].copy()
        alpha[reach] = 0
        # viền mềm: pixel sát vùng trong suốt mà hơi sáng (khử răng cưa) -> alpha theo độ tối
        ring = np.zeros_like(reach)
        ring[1:, :] |= reach[:-1, :]; ring[:-1, :] |= reach[1:, :]
        ring[:, 1:] |= reach[:, :-1]; ring[:, :-1] |= reach[:, 1:]
        ring &= ~reach
        mn = rgb.min(axis=2).astype(np.int32)
        soft = ring & (mn >= 150)
        alpha[soft] = np.clip((255 - mn[soft]) * 255 // (255 - 150), 0, 255).astype(np.uint8)
        a[:, :, 3] = alpha
        tmp = dst + ".tmp.png"
        Image.fromarray(a, "RGBA").save(tmp)
        os.replace(tmp, dst)                 # ghi xong mới thay, lỗi giữa chừng không mất ảnh
        if os.path.normcase(src) != os.path.normcase(dst):
            os.remove(src)                   # bỏ bản nền trắng gốc
        return dst, True
    except Exception as e:
        print(f"⚠️  Không tách nền được {os.path.basename(src)}: {e} -> dùng ảnh gốc.")
        return src, False


def audio_duration(path):
    """Độ dài mp3 (giây): pymediainfo (đi kèm pycapcut) -> ffprobe -> None."""
    try:
        import pymediainfo
        info = pymediainfo.MediaInfo.parse(path)
        if info.audio_tracks and info.audio_tracks[0].duration:
            return float(info.audio_tracks[0].duration) / 1000.0
    except Exception:
        pass
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", path],
                           capture_output=True, text=True, timeout=30,
                           creationflags=(0x08000000 if os.name == "nt" else 0))
        v = float((r.stdout or "").strip())
        return v if v > 0 else None
    except Exception:
        return None


# ── HIỆU ỨNG ÂM THANH (24/09/2026, chỉnh 25/09/2026 theo draft người dùng sửa tay) ──
#   pop    — bỏ 30/09/2026, BẬT LẠI 01/10/2026: beat liệt kê, mỗi ảnh phụ 1 pop, 50%,
#            ảnh phụ + pop của nó group lại (Ctrl+G) trong CapCut
#   whoosh — lúc VÀO NỀN TRẮNG (beat concept sau beat không-concept) và lúc vào THẺ SIGN, 100%
#   air    — "soft transition air": lúc TỪ NỀN TRẮNG QUAY VỀ cảnh đời thường, 43%, sớm 0.3s
#   typing — đúng lúc hiệu ứng gõ chữ của thẻ (Sign / tên video) chạy, dài bằng hiệu ứng,
#            bỏ 0.35s im lặng đầu file, 100%
# File tìm trong thư mục sfx_library cạnh script, tên đúng như dưới (đuôi mp3/wav…).
SFX_DIR_DEFAULT = os.path.join(HERE, "sfx_library")
SFX_POP, SFX_WHOOSH, SFX_TYPING, SFX_AIR = "pop", "whoosh", "typing", "soft transition air"
SFX_VOLUME = {SFX_POP: 0.5, SFX_WHOOSH: 1.0, SFX_TYPING: 1.0, SFX_AIR: 0.43}   # so với VO = 1.0
SFX_LEAD = {SFX_WHOOSH: 0.0, SFX_AIR: 0.3}   # bắt đầu sớm bao nhiêu giây so với lúc cắt cảnh
TYPING_SRC_START = 0.35      # typing.mp3 có ~0.35s im lặng ở đầu
SFX_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg")
# Khoảng nghỉ chèn vào VO ngay trước mỗi thẻ Sign (25/09/2026). Ảnh trước giữ nguyên qua khoảng nghỉ.
SIGN_PAUSE = 0.5
# Ảnh nền trượt vào khi vào nền trắng / thẻ Sign: xoay vòng 3 hướng người dùng hay chọn.
BASE_INTRO_CYCLE = ("up", "right", "left")
# 26/09/2026: nền trắng -> cảnh đời thường (chỗ có tiếng soft transition air) luôn Slide Left.
AIR_INTRO = "left"


def find_sfx(folder, name):
    for ext in SFX_EXTS:
        p = os.path.join(folder, name + ext)
        if os.path.isfile(p):
            return p
    return None


def sign_title_of(action):
    m = re.search(r"\[SIGN-TITLE:\s*([^\]]+)\]", action or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def hook_title_of(action, vo):
    """Dòng cuối phần hook có tag [HOOK-TITLE] -> thẻ chữ gõ NGUYÊN CÂU VO của dòng đó
    (25/09/2026; người dùng tự cắt gọn trong CapCut)."""
    if re.search(r"\[HOOK-TITLE\]", action or "", flags=re.IGNORECASE):
        return (vo or "").strip() or None
    return None


def finding_of(action):
    """01/10/2026: tag [FINDING: …] -> thẻ chữ kết quả nghiên cứu (in hoa, font Sign, gõ chữ
    + tiếng typing). Chữ do /scenes viết sẵn: chỉ phần nghiên cứu tìm ra."""
    m = re.search(r"\[FINDING:\s*([^\]]+)\]", action or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Sinh draft CapCut cho FKY.")
    ap.add_argument("--scenes-json", required=True)
    ap.add_argument("--srt", required=True)
    ap.add_argument("--audio", required=True, help="VO.mp3 hoàn chỉnh")
    ap.add_argument("--images", required=True, help="Folder ảnh 001.jpg, 002.jpg…")
    ap.add_argument("--draft-name", required=True)
    ap.add_argument("--draft-root", default=DEFAULT_DRAFT_ROOT)
    ap.add_argument("--font", default="", help="Đường dẫn font .ttf/.otf (trống = System)")
    ap.add_argument("--align-script", default="", help="make_video.py (đã bung) để dùng lại align")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--sfx-dir", default=SFX_DIR_DEFAULT, help="Thư mục hiệu ứng âm thanh")
    args = ap.parse_args()

    # ── kiểm tra đầu vào ──
    for label, p in (("scenes.json", args.scenes_json), ("SRT", args.srt), ("Audio VO", args.audio)):
        if not os.path.isfile(p):
            print(f"❌ Không thấy file {label}: {p}"); sys.exit(1)
    if not os.path.isdir(args.images):
        print(f"❌ Không thấy folder ảnh: {args.images}"); sys.exit(1)
    if not os.path.isdir(args.draft_root):
        print(f"❌ Không thấy thư mục draft CapCut: {args.draft_root}\n"
              "   (Mở CapCut ít nhất 1 lần để nó tạo thư mục, hoặc chỉ định --draft-root)"); sys.exit(1)
    align_script = args.align_script or os.path.join(HERE, "make_video.py")
    if not os.path.isfile(align_script):
        print(f"❌ Không thấy script align (make_video.py): {align_script}"); sys.exit(1)

    try:
        from fky_draft_builder import (build_draft, Beat, Element, DraftSpec, Sfx, shift_time,
                                       title_anim_len, card_start, card_text, element_in_time)
    except Exception as e:
        print(f"❌ Không nạp được fky_draft_builder / pycapcut: {e}\n"
              "   Thử: py -m pip install pycapcut"); sys.exit(1)

    rows = json.load(open(args.scenes_json, encoding="utf-8"))
    rows = [r for r in rows if (r.get("vo") or "").strip()]
    if not rows:
        print("❌ Không có scene nào có VO."); sys.exit(1)

    # ── 1) ALIGN (dùng lại make_video.py) ──
    mv = load_align_module(align_script)
    scenes = [(r["id"], r["vo"]) for r in rows]
    cues = mv.parse_srt(args.srt)
    if not cues:
        print("❌ SRT không có cue nào."); sys.exit(1)
    print(f"Scene: {len(scenes)}   |   SRT cue (TTS): {len(cues)}")
    spans = mv.align(scenes, cues)
    aligned_out = os.path.join(os.path.dirname(os.path.abspath(args.srt)), "scene-aligned.srt")
    mv.write_aligned_srt(scenes, spans, aligned_out)
    mv.print_align_stats(scenes, spans)
    print(f"   -> {aligned_out}")

    # ── 2) mốc thời gian: đầu = 0, cuối = độ dài VO.mp3 ──
    boundaries = [sp[0] for sp in spans] + [spans[-1][1]]
    boundaries[0] = 0.0
    adur = audio_duration(args.audio)
    if adur and adur > boundaries[-2] + 1.0 / args.fps:
        boundaries[-1] = adur
    elif adur:
        print(f"⚠️  VO.mp3 ({adur:.2f}s) không dài hơn mốc cuối SRT — giữ tổng theo SRT.")
    else:
        print("⚠️  Không đọc được thời lượng VO.mp3 — khớp theo mốc cuối SRT.")
    for i in range(1, len(boundaries)):
        if boundaries[i] <= boundaries[i - 1]:
            boundaries[i] = boundaries[i - 1] + 1.0 / args.fps

    # ── 2b) khoảng nghỉ trước mỗi thẻ Sign (25/09/2026) ──
    # Mốc cắt VO = giữa lúc câu trước dứt tiếng và lúc câu Sign bắt đầu (cắt vào chỗ im lặng).
    # Mọi mốc thời gian từ đây đi qua S() để dời theo khoảng nghỉ; ảnh trước giữ qua khoảng nghỉ.
    pauses = []
    for i, r in enumerate(rows):
        if i > 0 and sign_title_of(r.get("action", "")):
            st_i = boundaries[i]
            prev_end = max((c[1] for c in cues if c[1] <= st_i + 1e-3), default=st_i)
            p = (prev_end + st_i) / 2.0 if prev_end < st_i else st_i
            if p > boundaries[i - 1]:
                pauses.append((p, SIGN_PAUSE))

    def S(t):
        return shift_time(t, pauses)
    total = S(boundaries[-1])
    if pauses:
        print(f"   Chèn {len(pauses)} khoảng nghỉ {SIGN_PAUSE:g}s trước thẻ Sign "
              f"(video dài thêm {sum(d for _, d in pauses):.1f}s)")
    wtimes = word_times(rows, cues)       # giờ từng từ trong vo (VO gốc) -> mảnh hiện đúng chữ

    # ── 3) dựng beat ──
    # 24/09/2026: không ghi gì thêm vào folder ảnh. Ảnh trắng do fky_draft_builder tự tạo
    # BÊN TRONG thư mục draft; beat thiếu ảnh để base_image="" -> builder đặt ảnh trắng.
    white = ""
    beats, missing, n_scene, n_concept, n_title, n_el = [], [], 0, 0, 0, 0
    el_missing, n_cut, n_anchor = [], 0, 0
    n_intro = n_finding = 0
    for i, r in enumerate(rows):
        src_st, src_en = boundaries[i], boundaries[i + 1]
        st, en = S(src_st), S(src_en)
        num = int(re.search(r"\d+", str(r["id"])).group()) if re.search(r"\d+", str(r["id"])) else i + 1
        base = find_base_image(args.images, num)
        if base is None:
            missing.append(num)
            base = white                      # giữ chỗ để timeline không bị hụt
        kind = "concept" if r.get("kind") == "concept" else "scene"
        title = sign_title_of(r.get("action", ""))
        b = Beat(kind=kind, start=st, end=en, base_image=base, sign_title=title,
                 video_title=hook_title_of(r.get("action", ""), r.get("vo", "")),
                 finding=finding_of(r.get("action", "")))
        n_finding += int(bool(b.finding))
        # 25/09/2026: ảnh nền trượt vào khi VÀO nền trắng hoặc vào thẻ Sign
        prev_concept = i > 0 and rows[i - 1].get("kind") == "concept"
        if i > 0 and ((kind == "concept" and not prev_concept) or title):
            b.intro = BASE_INTRO_CYCLE[n_intro % len(BASE_INTRO_CYCLE)]
            n_intro += 1
        elif prev_concept and kind != "concept":
            b.intro = AIR_INTRO   # 26/09/2026: beat có "soft transition air" -> Slide Left
        action = r.get("action", "")
        # 24/09/2026: cảnh đời thường có {cell N} (SCENE PIECES) cũng xếp lớp ảnh con như
        # concept present. Cảnh thường không có {cell} -> pieces rỗng, giữ nguyên như cũ.
        # (25/09/2026: dùng CELL_RE để nhận cả ô có chữ mốc "{cell 3 @chữ}")
        has_pieces = kind == "concept" or bool(CELL_RE.search(action))
        if kind == "concept":
            n_concept += 1
        else:
            n_scene += 1
        if has_pieces:
            fn, _pose, pieces = parse_concept_action(action)
            b.function = fn
            # 07/09/2026: bỏ "undergo" — beat undergo giữ đồ vật ngay trong ảnh nền,
            # không xếp lớp ảnh con nữa. PHẢI khớp ELEMENT_FN trong -youtube_studio.pyw.
            if pieces and fn in ("present", "list"):
                units = piece_units(r["vo"], len(pieces))
                s_starts = per_sentence_starts(src_st, src_en, units) or [src_st]
                anchors = piece_anchors(action)
                vo_words = _norm(r["vo"])
                pos = 0
                for k, (desc, cell) in enumerate(pieces):
                    stem = f"{num:03d}{chr(ord('a') + k)}"
                    img = find_element_image(args.images, stem)
                    if img is None:
                        el_missing.append(stem)
                        continue              # chưa có ảnh con -> bỏ qua mảnh này
                    img, cut = element_cutout(img, seed_bottom=(cell != POV_CELL))
                    n_cut += int(cut)
                    # 25/09/2026: mảnh hiện đúng lúc VO đọc tới chữ nó đại diện ({cell N @chữ});
                    # không có @ -> thử nhãn "…" của mảnh; vẫn không thấy -> đầu vế như cũ.
                    t = None
                    anc = (anchors[k] if k < len(anchors) else "") or auto_anchor(desc)
                    if anc:
                        wi = find_anchor(vo_words, anc, pos)
                        if wi is not None and wi < len(wtimes[i]):
                            t = min(max(wtimes[i][wi], src_st), src_en)
                            pos = wi + 1
                            n_anchor += 1
                    if t is None:
                        t = s_starts[min(k, len(s_starts) - 1)]
                    b.elements.append(Element(image=img, cell=cell, vo_time=S(t)))
                    n_el += 1
        if title:
            n_title += 1
        # cảnh LIỆT KÊ -> pop mỗi ảnh phụ: [FN: list], hoặc cảnh đời thường có ≥2 mảnh
        # (02/10/2026: ảnh POV không tính là mảnh liệt kê, và không có pop)
        n_list = len([p for p in pieces if p[1] != POV_CELL]) if has_pieces else 0
        b.is_list = has_pieces and (b.function == "list" or (kind == "scene" and n_list >= 2))
        beats.append(b)

    # ── 4) hiệu ứng âm thanh ──
    sfx, sfx_missing = [], set()

    def _sfx(name, start, dur, src_start=0.0, group=None):
        p = find_sfx(args.sfx_dir, name)
        if p is None:
            sfx_missing.add(name)
            return False
        sfx.append(Sfx(path=p, start=start, dur=dur, volume=SFX_VOLUME.get(name, 0.5),
                       src_start=src_start, group=group))
        return True
    for i, b in enumerate(beats):
        prev_concept = i > 0 and beats[i - 1].kind == "concept"
        if i > 0 and ((b.kind == "concept" and not prev_concept) or b.sign_title):
            _sfx(SFX_WHOOSH, b.start - SFX_LEAD[SFX_WHOOSH], 5.0)      # vào nền trắng / thẻ Sign
        elif prev_concept and b.kind != "concept":
            _sfx(SFX_AIR, b.start - SFX_LEAD[SFX_AIR], 5.0)            # nền trắng -> cảnh đời thường
        txt = card_text(b)
        if txt:                             # typing dài đúng bằng hiệu ứng gõ chữ trên thẻ
            t0 = card_start(b)
            _sfx(SFX_TYPING, t0, min(title_anim_len(txt), b.end - t0), src_start=TYPING_SRC_START)
        # 01/10/2026: BẬT LẠI pop ở beat LIỆT KÊ ([FN: list] hoặc cảnh đời thường ≥2 mảnh) — mỗi ảnh
        # phụ 1 tiếng pop đúng lúc nó trượt vào, và ảnh + pop đó thành 1 GROUP trong CapCut.
        if getattr(b, "is_list", False):
            for k, el in enumerate(b.elements):
                if el.cell == POV_CELL:
                    continue
                key = f"pop_{i}_{k}"
                if _sfx(SFX_POP, element_in_time(b, el), 5.0, group=key):
                    el.group = key
    if sfx_missing:
        print(f"ℹ️  Thiếu file âm thanh trong {args.sfx_dir}: {', '.join(sorted(sfx_missing))} (bỏ qua)")
    if sfx:
        cnt = {}
        for s in sfx:
            k = os.path.splitext(os.path.basename(s.path))[0]
            cnt[k] = cnt.get(k, 0) + 1
        print("   Âm thanh: " + " · ".join(f"{k} {v}" for k, v in sorted(cnt.items())))

    if missing:
        print(f"⚠️  Thiếu {len(missing)} ảnh scene (đặt tạm nền trắng): "
              f"{missing[:15]}{'…' if len(missing) > 15 else ''}")
    if el_missing:
        print(f"ℹ️  Chưa có {len(el_missing)} ảnh con element (bỏ qua, chỉ đặt ảnh nền): "
              f"{', '.join(el_missing[:12])}{'…' if len(el_missing) > 12 else ''}")
    if n_el:
        print(f"   Element: {n_el} ảnh con xếp lớp ({n_cut} đã tách nền, ghi đè tại chỗ thành .png) "
              f"· {n_anchor} ảnh hiện đúng chữ, {n_el - n_anchor} ảnh hiện theo đầu vế")

    if n_finding:
        print(f"   Thẻ chữ nghiên cứu [FINDING]: {n_finding}")

    font = args.font.strip()
    if font and not os.path.isfile(font):
        print(f"⚠️  Không thấy file font: {font} -> dùng System.")
        font = ""

    spec = DraftSpec(
        draft_root=args.draft_root, draft_name=args.draft_name,
        white_image=white, vo_path=os.path.abspath(args.audio),
        total_duration=total, beats=beats,
        width=args.width, height=args.height, fps=args.fps, font_path=font,
        caption_srt=os.path.abspath(args.srt),   # 24/09/2026: caption SRT, track ẩn sẵn
        sfx=sfx,
        pauses=pauses,                           # 25/09/2026: khoảng nghỉ trước thẻ Sign
    )
    print(f"\nDựng draft '{args.draft_name}': {len(beats)} beat "
          f"(scene {n_scene} · concept {n_concept} · sign-title {n_title} · element {n_el}) "
          f"| tổng {total:.2f}s ({total/60:.1f} phút)")
    out = build_draft(spec)
    print(f"\n✅ XONG -> {out}")
    print("CapCut: mở CapCut -> draft '%s' nằm trong danh sách dự án. Thêm nhạc rồi export." % args.draft_name)


if __name__ == "__main__":
    main()
