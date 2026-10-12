# -*- coding: utf-8 -*-
"""
lacg_render.py — do hoa Like a Chess Game (Pillow) + video dung hinh (ffmpeg)
============================================================================
THEME SANG: o player + the binh luan + bang Game Review nen TRANG, chu DEN; badge va mau loai
nuoc theo quy uoc quen thuoc cua app co vua.

Moi doan DUNG HINH (1 nuoc di) duoc render thanh 1 file mp4 du khung canvas:
    khung hinh dung + lam toi -> thanh eval truot -> badge bat len -> the hien ra, chu go dan
    (thanh mau ben trai dai theo so dong) -> "Better was" + tag -> dung doc 2-3s
Vi nen la khung hinh DUNG nen bake ca hieu ung vao mp4 la khop tung pixel, khong phu thuoc cach
CapCut ve chu. O player + thanh eval khi clip dang chay la anh PNG trong suot full canvas.

Phu thuoc: Pillow, numpy (tuy chon), opencv-python (tuy chon — do mat de dat badge / cat avatar),
ffmpeg tren PATH.
"""
from __future__ import annotations

import math
import os
import subprocess
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import lacg_formats as F

# ════════════════════════════════════════════════════════════════════════════
#  MAU
# ════════════════════════════════════════════════════════════════════════════
BADGE_COLOR = {   # nen badge + thanh mau ben trai the
    "brilliant": "#1BAAA6", "great": "#5C8BB0", "best": "#81B64C", "excellent": "#96BC4B",
    "good": "#96AF8B", "book": "#A88865", "inaccuracy": "#F7C045", "mistake": "#E58F2A",
    "miss": "#EE6B55", "blunder": "#CA3431",
}
TEXT_COLOR = {    # mau tieu de the tren nen TRANG (dam hon de doc duoc)
    "brilliant": "#13908C", "great": "#3F6E96", "best": "#5E8E2E", "excellent": "#6B9230",
    "good": "#6A8660", "book": "#86643F", "inaccuracy": "#B98608", "mistake": "#C2680E",
    "miss": "#D2452F", "blunder": "#B8261F",
}
INK = "#1E1E1E"
MUTED = "#8A8A8A"
CARD_BG = (255, 255, 255, 255)
CHIP_BG = "#ECECEC"
CHIP_INK = "#4A4A4A"
BAR_DARK = "#403D39"
BAR_LIGHT = "#FFFFFF"
DIM_ALPHA = 0.32           # muc lam toi khung hinh dung

# ════════════════════════════════════════════════════════════════════════════
#  FONT
# ════════════════════════════════════════════════════════════════════════════
_FONT_CANDIDATES = {
    False: ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf"],
    True: ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
}


def find_font(bold: bool, override: str = "") -> str:
    if override and os.path.isfile(override):
        return override
    for p in _FONT_CANDIDATES[bold]:
        if os.path.isfile(p):
            return p
    if bold:
        return find_font(False, override)
    return ""


@lru_cache(maxsize=64)
def _font(path: str, size: int):
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


class Style:
    """Kich thuoc thiet ke o 1080p, tu nhan theo do cao canvas."""

    def __init__(self, width: int = 1920, height: int = 1080, font_regular: str = "", font_bold: str = ""):
        self.W, self.H = width, height
        self.k = height / 1080.0
        self.reg = find_font(False, font_regular)
        self.bold = find_font(True, font_bold) or self.reg

    def px(self, v: float) -> int:
        return max(1, int(round(v * self.k)))

    def f(self, size: float, bold: bool = False):
        return _font(self.bold if bold else self.reg, self.px(size))

    # bo cuc chung (1080p)
    @property
    def bar_rect(self) -> Tuple[int, int, int, int]:
        """(x0, y0, x1, y1) cua thanh eval."""
        return (self.px(16), self.px(116), self.px(16) + self.px(30), self.H - self.px(116))

    @property
    def safe_left(self) -> int:
        return self.px(96)

    @property
    def sub_zone_top(self) -> int:
        """The / badge khong duoc lan xuong vung phu de."""
        return self.H - self.px(175)


def hex_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def text_w(font, s: str) -> float:
    return font.getlength(s) if s else 0.0


def wrap_text(text: str, font, max_w: float) -> List[str]:
    lines, cur = [], ""
    for word in (text or "").split():
        cand = (cur + " " + word).strip()
        if cur and text_w(font, cand) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


# ════════════════════════════════════════════════════════════════════════════
#  BADGE
# ════════════════════════════════════════════════════════════════════════════
def _star(cx, cy, r_out, r_in, rot=-90):
    pts = []
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(rot + i * 36)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _draw_icon(d: ImageDraw.ImageDraw, cls: str, S: int, font_path: str):
    """Ve ky hieu trang len badge kich thuoc S (S x S)."""
    c = S / 2
    white = (255, 255, 255, 255)
    if cls == "best":
        d.polygon(_star(c, c * 1.03, S * 0.33, S * 0.135), fill=white)
    elif cls == "good":
        w = int(S * 0.09)
        d.line([(S * 0.29, S * 0.52), (S * 0.44, S * 0.67), (S * 0.72, S * 0.36)], fill=white, width=w, joint="curve")
        for p in [(S * 0.29, S * 0.52), (S * 0.72, S * 0.36)]:
            d.ellipse([p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2], fill=white)
    elif cls == "miss":
        w = int(S * 0.09)
        a, b = S * 0.33, S * 0.67
        d.line([(a, a), (b, b)], fill=white, width=w)
        d.line([(a, b), (b, a)], fill=white, width=w)
    elif cls == "book":
        # sach mo: 2 trang nghieng, khe giua
        g = S * 0.025
        d.polygon([(c - g, S * 0.34), (S * 0.25, S * 0.29), (S * 0.25, S * 0.66), (c - g, S * 0.71)], fill=white)
        d.polygon([(c + g, S * 0.34), (S * 0.75, S * 0.29), (S * 0.75, S * 0.66), (c + g, S * 0.71)], fill=white)
    elif cls == "excellent":
        # ngon cai gio len (don gian hoa)
        d.rounded_rectangle([S * 0.25, S * 0.45, S * 0.34, S * 0.72], radius=S * 0.02, fill=white)    # co tay
        d.rounded_rectangle([S * 0.38, S * 0.44, S * 0.72, S * 0.72], radius=S * 0.07, fill=white)    # nam tay
        d.rounded_rectangle([S * 0.43, S * 0.25, S * 0.55, S * 0.52], radius=S * 0.06, fill=white)    # ngon cai
    else:
        sym = {"brilliant": "!!", "great": "!", "inaccuracy": "?!", "mistake": "?", "blunder": "??"}.get(cls, "?")
        size = int(S * (0.56 if len(sym) == 1 else 0.48))
        f = _font(font_path, size)
        bbox = d.textbbox((0, 0), sym, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((c - tw / 2 - bbox[0], c - th / 2 - bbox[1]), sym, font=f, fill=white)


@lru_cache(maxsize=64)
def badge_image(cls: str, diameter: int, font_path: str = "", shadow: bool = True) -> Image.Image:
    """Badge tron RGBA (co bong do nhe). Kich thuoc anh = diameter * 1.2 (cho bong)."""
    SS = 4                                   # ve to 4x roi thu nho -> mep min
    S = diameter * SS
    pad = int(diameter * 0.1) * SS
    img = Image.new("RGBA", (S + 2 * pad, S + 2 * pad), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse([pad, pad + S * 0.04, pad + S, pad + S * 1.04], fill=(0, 0, 0, 110))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(S * 0.03)))
    disc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    dd.ellipse([0, 0, S - 1, S - 1], fill=hex_rgb(BADGE_COLOR.get(cls, "#888888")) + (255,))
    _draw_icon(dd, cls, S, font_path or find_font(True))
    img.alpha_composite(disc, (pad, pad))
    out_size = (img.width // SS, img.height // SS)
    return img.resize(out_size, Image.LANCZOS)


# ════════════════════════════════════════════════════════════════════════════
#  KHUNG HINH + MAT
# ════════════════════════════════════════════════════════════════════════════
def fit_canvas(img: Image.Image, W: int, H: int) -> Image.Image:
    """Giong CapCut dat video vao canvas: giu ti le, vien den (contain)."""
    img = img.convert("RGB")
    s = min(W / img.width, H / img.height)
    nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
    out = Image.new("RGB", (W, H), (0, 0, 0))
    out.paste(img.resize((nw, nh), Image.LANCZOS), ((W - nw) // 2, (H - nh) // 2))
    return out


def detect_faces(img: Image.Image) -> List[Tuple[int, int, int, int]]:
    """[(x, y, w, h)] lon -> nho. Can opencv; khong co thi tra []."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return []
    gray = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    gray = cv2.equalizeHist(gray)
    H, W = gray.shape
    min_side = max(24, int(min(W, H) * 0.08))
    found = []
    for name, flip in (("haarcascade_frontalface_default.xml", False),
                       ("haarcascade_profileface.xml", False), ("haarcascade_profileface.xml", True)):
        path = os.path.join(cv2.data.haarcascades, name)
        if not os.path.isfile(path):
            continue
        casc = cv2.CascadeClassifier(path)
        src = cv2.flip(gray, 1) if flip else gray
        for (x, y, w, h) in casc.detectMultiScale(src, 1.1, 6, minSize=(min_side, min_side)):
            if flip:
                x = W - x - w
            found.append((int(x), int(y), int(w), int(h)))
    # gop cac khung trung nhau
    found.sort(key=lambda r: -r[2] * r[3])
    keep = []
    for r in found:
        cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
        if all(not (k[0] <= cx <= k[0] + k[2] and k[1] <= cy <= k[1] + k[3]) for k in keep):
            keep.append(r)
    return keep


def crop_square_face(img: Image.Image, size: int = 256) -> Image.Image:
    """Cat vuong quanh mat lon nhat (avatar tu dong); khong thay mat -> cat giua."""
    faces = detect_faces(img)
    W, H = img.size
    if faces:
        x, y, w, h = faces[0]
        side = int(max(w, h) * 1.7)
        cx, cy = x + w / 2, y + h / 2 + h * 0.08
    else:
        side = int(min(W, H) * 0.6)
        cx, cy = W / 2, H / 2
    side = min(side, W, H)
    x0 = int(min(max(0, cx - side / 2), W - side))
    y0 = int(min(max(0, cy - side / 2), H - side))
    return img.crop((x0, y0, x0 + side, y0 + side)).resize((size, size), Image.LANCZOS)


def initials_avatar(name: str, size: int = 256) -> Image.Image:
    img = Image.new("RGB", (size, size), hex_rgb("#5A5A5A"))
    d = ImageDraw.Draw(img)
    ini = "".join(w[0] for w in (name or "?").split()[:2]).upper() or "?"
    f = _font(find_font(True), int(size * 0.42))
    bb = d.textbbox((0, 0), ini, font=f)
    d.text(((size - bb[2] - bb[0]) / 2, (size - bb[3] - bb[1]) / 2), ini, font=f, fill="white")
    return img


# ════════════════════════════════════════════════════════════════════════════
#  THANH EVAL + O PLAYER
# ════════════════════════════════════════════════════════════════════════════
def eval_fraction(score: float, mate: int = 0) -> float:
    """Ti le phan TRANG (duoi) cua thanh. Phi tuyen nhu app co: lech nho thay ro, lech lon bao hoa."""
    if mate:
        return 1.0 if mate > 0 else 0.0
    return min(0.97, max(0.03, 0.5 + 0.5 * math.tanh(score / 4.0)))


def result_fraction(result: str) -> float:
    return {"1-0": 1.0, "0-1": 0.0}.get(result, 0.5)


def draw_bar(img: Image.Image, st: Style, frac: float, label: str, white_leads: bool):
    """Ve thanh eval len img (RGBA hoac RGB) tai st.bar_rect."""
    x0, y0, x1, y1 = st.bar_rect
    d = ImageDraw.Draw(img)
    r = st.px(4)
    d.rounded_rectangle([x0 - st.px(2), y0 - st.px(2), x1 + st.px(2), y1 + st.px(2)], radius=r + st.px(2),
                        fill=(255, 255, 255, 255))                                         # vien trang (theme sang)
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=hex_rgb(BAR_DARK) + (255,))
    split = y1 - (y1 - y0) * frac
    if frac > 0:
        d.rounded_rectangle([x0, split, x1, y1], radius=r, fill=hex_rgb(BAR_LIGHT) + (255,))
        if split - y0 > r:
            d.rectangle([x0, split, x1, min(y1, split + r)], fill=hex_rgb(BAR_LIGHT) + (255,))
    f = st.f(14, True)
    tw = text_w(f, label)
    tx = (x0 + x1) / 2 - tw / 2
    if white_leads:
        d.text((tx, y1 - st.px(22)), label, font=f, fill=hex_rgb(BAR_DARK) + (255,))
    else:
        d.text((tx, y0 + st.px(6)), label, font=f, fill=(255, 255, 255, 255))


def bar_state(ev: F.Eval) -> Tuple[float, str, bool]:
    return eval_fraction(ev.score, ev.mate), ev.label, ev.score >= 0


def bar_overlay(st: Style, ev: Optional[F.Eval] = None, result: Optional[str] = None) -> Image.Image:
    img = Image.new("RGBA", (st.W, st.H), (0, 0, 0, 0))
    if result is not None:
        lab = result.replace("1/2-1/2", "½-½") if result != "*" else (ev.label if ev else "0.0")
        frac = result_fraction(result) if result != "*" else bar_state(ev)[0]
        draw_bar(img, st, frac, lab, frac >= 0.5)
    else:
        draw_bar(img, st, *bar_state(ev or F.Eval()))
    return img


def _player_chip(st: Style, avatar: Image.Image, name: str, side_txt: str) -> Image.Image:
    av = st.px(64)
    fn, fs = st.f(24, True), st.f(15)
    lines = wrap_text(name, fn, st.px(230)) or [name]
    tw = max([text_w(fn, ln) for ln in lines] + [text_w(fs, side_txt)])
    pad = st.px(10)
    lh = st.px(27)
    text_h = lh * len(lines) + st.px(19)
    h = max(av, text_h) + 2 * pad
    w = int(pad + av + st.px(12) + tw + pad + st.px(4))
    chip = Image.new("RGBA", (w + st.px(12), h + st.px(12)), (0, 0, 0, 0))
    sh = Image.new("RGBA", chip.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([st.px(4), st.px(6), w + st.px(4), h + st.px(6)], radius=st.px(12),
                                         fill=(0, 0, 0, 90))
    chip = Image.alpha_composite(chip, sh.filter(ImageFilter.GaussianBlur(st.px(4))))
    d = ImageDraw.Draw(chip)
    d.rounded_rectangle([0, 0, w, h], radius=st.px(12), fill=(255, 255, 255, 255))
    a = avatar.convert("RGB").resize((av, av), Image.LANCZOS)
    m = Image.new("L", (av, av), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, av - 1, av - 1], radius=st.px(8), fill=255)
    chip.paste(a, (pad, (h - av) // 2), m)
    tx = pad + av + st.px(12)
    ty = (h - text_h) // 2
    for i, ln in enumerate(lines):
        d.text((tx, ty + i * lh), ln, font=fn, fill=hex_rgb(INK))
    d.text((tx, ty + lh * len(lines) - st.px(2)), side_txt, font=fs, fill=hex_rgb(MUTED))
    return chip


def players_overlay(st: Style, black_name: str, black_av: Image.Image,
                    white_name: str, white_av: Image.Image) -> Image.Image:
    """O player tren (Den) + duoi (Trang), goc trai — full canvas RGBA."""
    img = Image.new("RGBA", (st.W, st.H), (0, 0, 0, 0))
    top = _player_chip(st, black_av, black_name, "Black")
    bot = _player_chip(st, white_av, white_name, "White")
    m = st.px(10)
    img.alpha_composite(top, (m, m))
    img.alpha_composite(bot, (m, st.H - bot.height - m + st.px(10)))
    return img


# ════════════════════════════════════════════════════════════════════════════
#  THE BINH LUAN
# ════════════════════════════════════════════════════════════════════════════
class Card:
    """Bo cuc 1 the; render(n_chars) ve trang thai dang go chu."""

    def __init__(self, st: Style, index: int, mv: F.Move, chips: List[str]):
        self.st, self.mv = st, mv
        self.cls = mv.cls
        self.W = st.px(560)
        self.bar_w = st.px(8)
        self.pl, self.pr, self.pt, self.pb = st.px(26), st.px(22), st.px(18), st.px(18)
        self.inner = self.W - self.bar_w - self.pl - self.pr
        self.f_title, self.f_move = st.f(30, True), st.f(19, True)
        self.f_body, self.f_better, self.f_chip = st.f(26), st.f(22, True), st.f(18, True)
        self.lh_title, self.lh_move, self.lh_body = st.px(36), st.px(27), st.px(35)
        self.title_lines = wrap_text(F.card_title(mv), self.f_title, self.inner)
        ml = F.move_line(index, mv)
        while text_w(self.f_move, ml) > self.inner and len(ml) > 8:
            ml = ml[:-2].rstrip() + "…"
        self.move_line = ml
        self.body_lines = wrap_text(mv.body, self.f_body, self.inner)
        self.n_chars = sum(len(x) for x in self.body_lines)
        self.better = ("Better was: " + mv.better) if mv.better else ""
        self.better_lines = wrap_text(self.better, self.f_better, self.inner - st.px(34)) if self.better else []
        self.chips = [c for c in chips if c]
        self.chip_h = st.px(34)

    # chieu cao cac phan
    def _head_h(self):
        return self.pt + self.lh_title * len(self.title_lines) + self.lh_move + st_gap(self.st, 10)

    def _extra_h(self):
        h = 0
        if self.better_lines:
            h += st_gap(self.st, 12) + st_gap(self.st, 30) * len(self.better_lines)
        if self.chips:
            h += st_gap(self.st, 14) + self.chip_h
        return h

    def height(self, n_body_lines: int, extra: bool) -> int:
        return self._head_h() + self.lh_body * max(1, n_body_lines) + (self._extra_h() if extra else 0) + self.pb

    @property
    def full_height(self) -> int:
        return self.height(len(self.body_lines), True)

    def visible_lines(self, n: int) -> int:
        """So dong da co it nhat 1 ky tu hien."""
        acc = 0
        for i, ln in enumerate(self.body_lines):
            if n <= acc:
                return max(1, i)
            acc += len(ln)
        return len(self.body_lines)

    def render(self, n: int, extra_alpha: float = 0.0, fade_chars: int = 5) -> Image.Image:
        """Anh RGBA cua the (kem le bong). n = so ky tu than the da go."""
        st = self.st
        n_lines = self.visible_lines(n) if n < self.n_chars else len(self.body_lines)
        show_extra = extra_alpha > 0
        h = self.height(n_lines, show_extra)
        sp = st.px(14)                                  # le cho bong
        img = Image.new("RGBA", (self.W + 2 * sp, h + 2 * sp), (0, 0, 0, 0))
        img.alpha_composite(_card_shadow(self.W, h, sp, st.px(14)), (0, 0))
        d = ImageDraw.Draw(img)
        r = st.px(14)
        d.rounded_rectangle([sp, sp, sp + self.W, sp + h], radius=r, fill=CARD_BG)
        # thanh mau ben trai (bo goc trai)
        d.rounded_rectangle([sp, sp, sp + self.bar_w + r, sp + h], radius=r,
                            fill=hex_rgb(BADGE_COLOR[self.cls]) + (255,))
        d.rectangle([sp + self.bar_w, sp, sp + self.bar_w + r, sp + h], fill=CARD_BG)
        x = sp + self.bar_w + self.pl
        y = sp + self.pt
        for ln in self.title_lines:
            d.text((x, y), ln, font=self.f_title, fill=hex_rgb(TEXT_COLOR[self.cls]))
            y += self.lh_title
        d.text((x, y + st.px(2)), self.move_line, font=self.f_move, fill=hex_rgb(MUTED))
        y += self.lh_move + st_gap(st, 10)
        # than the: n ky tu den, vai ky tu tiep theo nhat dan (hieu ung chu dang hien)
        acc = 0
        ink = hex_rgb(INK)
        for li, ln in enumerate(self.body_lines):
            if acc >= n + fade_chars or li >= n_lines:
                break
            shown = max(0, min(len(ln), n - acc))
            if shown:
                d.text((x, y), ln[:shown], font=self.f_body, fill=ink)
            cx = x + text_w(self.f_body, ln[:shown])
            for j in range(shown, min(len(ln), shown + fade_chars)):
                if acc + j >= n + fade_chars:
                    break
                k = (acc + j - n + 1) / (fade_chars + 1)          # 0 = sap den, 1 = rat nhat
                col = tuple(int(c + (238 - c) * k) for c in ink)
                d.text((cx, y), ln[j], font=self.f_body, fill=col)
                cx += text_w(self.f_body, ln[j])
            acc += len(ln)
            y += self.lh_body
        if show_extra:
            y = sp + self._head_h() + self.lh_body * len(self.body_lines)
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            dl = ImageDraw.Draw(layer)
            if self.better_lines:
                y += st_gap(st, 12)
                ic = st.px(24)
                ib = badge_image("best", ic, st.bold, shadow=False)
                layer.alpha_composite(ib, (x - (ib.width - ic) // 2, int(y + st.px(3) - (ib.height - ic) // 2)))
                for ln in self.better_lines:
                    dl.text((x + st.px(34), y), ln, font=self.f_better, fill=hex_rgb(TEXT_COLOR["best"]))
                    y += st_gap(st, 30)
            if self.chips:
                y += st_gap(st, 14)
                cx = x
                for chip in self.chips:
                    tw = text_w(self.f_chip, chip)
                    cw = int(tw + st.px(22))
                    if cx > x and cx + cw > x + self.inner:
                        break
                    dl.rounded_rectangle([cx, y, cx + cw, y + self.chip_h], radius=self.chip_h // 2, fill=CHIP_BG)
                    dl.text((cx + st.px(11), y + st.px(6)), chip, font=self.f_chip, fill=CHIP_INK)
                    cx += cw + st.px(8)
            if extra_alpha < 1.0:
                a = layer.getchannel("A").point(lambda v: int(v * extra_alpha))
                layer.putalpha(a)
            img.alpha_composite(layer)
        return img


def st_gap(st: Style, v: float) -> int:
    return st.px(v)


@lru_cache(maxsize=256)
def _card_shadow(w: int, h: int, sp: int, r: int) -> Image.Image:
    sh = Image.new("RGBA", (w + 2 * sp, h + 2 * sp), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([sp, sp + sp // 3, sp + w, sp + h + sp // 3], radius=r, fill=(0, 0, 0, 120))
    return sh.filter(ImageFilter.GaussianBlur(max(1, sp // 2)))


# ════════════════════════════════════════════════════════════════════════════
#  VI TRI BADGE + THE
# ════════════════════════════════════════════════════════════════════════════
def place(st: Style, frame: Image.Image, card_w: int, card_h: int, badge_d: int,
          pos: str = "auto") -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """-> (tam badge, goc tren-trai the). pos: auto (theo mat lon nhat) | L | C | R."""
    W, H = st.W, st.H
    top_lim, bot_lim = st.px(40), st.sub_zone_top
    face = None
    if pos == "auto":
        faces = detect_faces(frame)
        face = faces[0] if faces else None
    if face:
        x, y, w, h = face
        r = badge_d / 2
        right_room = W - (x + w) - st.px(30)
        if right_room >= badge_d * 0.8:
            bc = (int(min(x + w + r * 0.55, W - r - st.px(30))), int(y + r * 0.35))
        else:
            bc = (int(max(x - r * 0.55, st.safe_left + r)), int(y + r * 0.35))
        bc = (bc[0], int(min(max(bc[1], top_lim + r), bot_lim - r)))
        # the: ben co nhieu cho trong hon (khong de len mat)
        left_free = x - st.px(30) - st.safe_left
        right_free = W - st.px(40) - (x + w + st.px(30))
        badge_right = bc[0] > x + w / 2
        if left_free >= card_w and (not badge_right or right_free < card_w or left_free >= right_free):
            cx0 = max(st.safe_left, x - st.px(30) - card_w)
        elif right_free >= card_w:
            cx0 = x + w + st.px(30)
            if badge_right:                       # badge dang o ben phai mat -> day the xuong duoi badge
                cx0 = min(cx0, W - st.px(40) - card_w)
        else:
            cx0 = st.safe_left if bc[0] > W / 2 else W - st.px(40) - card_w
        cy0 = bc[1] - int(badge_d * 0.15)
    else:
        xs = {"L": 0.27, "C": 0.5, "R": 0.72}.get(pos if pos in ("L", "C", "R") else "R", 0.72)
        bc = (int(W * xs), int(H * 0.24))
        if xs > 0.5:
            cx0 = bc[0] - badge_d // 2 - st.px(40) - card_w
        elif xs < 0.5:
            cx0 = bc[0] + badge_d // 2 + st.px(40)
        else:
            cx0 = st.safe_left + st.px(40)
        cy0 = bc[1] + badge_d // 2 - st.px(20) if xs == 0.5 else bc[1] - int(badge_d * 0.15)
    cx0 = int(min(max(cx0, st.safe_left), W - st.px(40) - card_w))
    cy0 = int(min(max(cy0, top_lim), bot_lim - card_h))
    return bc, (cx0, cy0)


# ════════════════════════════════════════════════════════════════════════════
#  GHI VIDEO (ffmpeg pipe)
# ════════════════════════════════════════════════════════════════════════════
class VideoWriter:
    def __init__(self, path: str, W: int, H: int, fps: int = 30, ffmpeg: str = "ffmpeg"):
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "%dx%d" % (W, H), "-r", str(fps), "-i", "-",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", path]
        flags = 0x08000000 if os.name == "nt" else 0                 # CREATE_NO_WINDOW
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=flags)
        self.W, self.H, self.n = W, H, 0

    def write(self, img: Image.Image):
        self.p.stdin.write(img.convert("RGB").tobytes())
        self.n += 1

    def close(self):
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("ffmpeg lỗi khi ghi video")


def _dimmed(frame: Image.Image, a: float) -> Image.Image:
    if a <= 0:
        return frame.copy()
    return Image.blend(frame, Image.new("RGB", frame.size, (0, 0, 0)), a)


# thoi gian (giay) trong 1 doan dung hinh
T_BAR = 0.5          # thanh eval truot
T_BADGE0, T_BADGE1 = 0.05, 0.33
T_CARD = 0.35        # the hien ra
T_TYPE0 = 0.45       # bat dau go chu
T_EXTRA_FADE = 0.15


def freeze_timing(card: Card, cps: float, pause: float) -> dict:
    t_type = card.n_chars / max(5.0, cps)
    t_end = T_TYPE0 + t_type
    extra = bool(card.better_lines or card.chips)
    t_extra = t_end + 0.12 if extra else None
    dur = (t_extra + T_EXTRA_FADE if extra else t_end) + pause
    return {"duration": dur, "type_start": T_TYPE0, "type_end": t_end, "extra": t_extra, "badge": T_BADGE0}


def render_freeze(out_path: str, frame: Image.Image, st: Style, card: Card, badge_center: Tuple[int, int],
                  card_xy: Tuple[int, int], ev_from: F.Eval, ev_to: F.Eval,
                  cps: float = 30.0, pause: float = 2.5, fps: int = 30, ffmpeg: str = "ffmpeg") -> dict:
    """Render 1 doan dung hinh -> mp4. Tra ve moc thoi gian (cho SFX)."""
    tm = freeze_timing(card, cps, pause)
    n_frames = max(1, int(round(tm["duration"] * fps)))
    tm["duration"] = n_frames / fps
    badge_d = st.px(170)
    base_full = _dimmed(frame, DIM_ALPHA)
    f0, l0, w0 = bar_state(ev_from)
    f1, l1, w1 = bar_state(ev_to)
    vw = VideoWriter(out_path, st.W, st.H, fps, ffmpeg)
    last_key, last_img = None, None
    try:
        for i in range(n_frames):
            t = i / fps
            # khung giong het khung truoc (dang dung doc) -> ghi lai, khoi ve
            if t > max(T_BAR, T_BADGE1, T_CARD + 0.12):
                n_k = min(card.n_chars, int((t - T_TYPE0) * cps))
                ea_k = round(min(1.0, (t - tm["extra"]) / T_EXTRA_FADE), 2) if (
                    tm["extra"] is not None and t >= tm["extra"]) else 0.0
                key = (n_k, ea_k)
                if key == last_key:
                    vw.write(last_img)
                    continue
                last_key = key
            img = _dimmed(frame, DIM_ALPHA * min(1.0, t / 0.2)) if t < 0.2 else base_full.copy()
            k = ease_in_out(t / T_BAR)
            frac = f0 + (f1 - f0) * k
            lab, wl = (l1, w1) if k >= 0.5 else (l0, w0)
            draw_bar(img, st, frac, lab, wl)
            if t >= T_BADGE0:
                u = (t - T_BADGE0) / (T_BADGE1 - T_BADGE0)
                sc = (1.12 * ease_out(u / 0.65)) if u < 0.65 else (1.12 - 0.12 * ease_out((u - 0.65) / 0.35))
                if sc > 0.02:
                    b = badge_image(card.cls, max(2, int(badge_d * sc)), st.bold)
                    img.paste(b, (badge_center[0] - b.width // 2, badge_center[1] - b.height // 2), b)
            if t >= T_CARD:
                n = 0 if t < T_TYPE0 else int((t - T_TYPE0) * cps)
                n = min(n, card.n_chars)
                ea = 0.0
                if tm["extra"] is not None and t >= tm["extra"]:
                    ea = min(1.0, (t - tm["extra"]) / T_EXTRA_FADE)
                c = card.render(n, ea, fade_chars=0 if n >= card.n_chars else 5)
                a_in = min(1.0, (t - T_CARD) / 0.12)
                dy = int((1 - ease_out(a_in)) * st.px(14))
                if a_in < 1.0:
                    c.putalpha(c.getchannel("A").point(lambda v: int(v * a_in)))
                sp = st.px(14)
                img.paste(c, (card_xy[0] - sp, card_xy[1] - sp + dy), c)
            last_img = img
            vw.write(img)
    finally:
        vw.close()
    return tm


# ════════════════════════════════════════════════════════════════════════════
#  GAME REVIEW
# ════════════════════════════════════════════════════════════════════════════
REVIEW_ROWS = [("Brilliant", "brilliant"), ("Great", "great"), ("Best", "best"), ("Excellent", "excellent"),
               ("Good", "good"), ("Book", "book"), ("Inaccuracy", "inaccuracy"), ("Mistake", "mistake"),
               ("Miss", "miss"), ("Blunder", "blunder")]


def review_panel(st: Style, g: F.Game, stats: dict, av_w: Image.Image, av_b: Image.Image) -> Image.Image:
    """Bang Game Review (RGBA, nen trang chu den)."""
    W = st.px(660)
    pad = st.px(30)
    f_title, f_name, f_row = st.f(36, True), st.f(18, True), st.f(24, True)
    f_cnt, f_foot, f_acc = st.f(26, True), st.f(20, True), st.f(24, True)
    row_h = st.px(38)
    col_lab = pad
    col_w, col_ic, col_b = int(W * 0.55), int(W * 0.70), int(W * 0.85)
    tp = stats.get("turning_point")
    foot = []
    if g.opening:
        foot.append("Opening: " + g.opening)
    if tp is not None:
        foot.append("Turning Point: " + F.short_ref(tp, g.moves[tp]))
    foot.append(F.result_text(g.result))
    H = (pad + st.px(50) + st.px(118) + st.px(58) + row_h * len(REVIEW_ROWS) + st.px(16)
         + st.px(32) * len(foot) + pad)
    sp = st.px(18)
    img = Image.new("RGBA", (W + 2 * sp, H + 2 * sp), (0, 0, 0, 0))
    img.alpha_composite(_card_shadow(W, H, sp, st.px(18)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([sp, sp, sp + W, sp + H], radius=st.px(18), fill=(255, 255, 255, 255))
    x0, y = sp, sp + pad
    t = "Game Review"
    d.text((x0 + (W - text_w(f_title, t)) / 2, y), t, font=f_title, fill=hex_rgb(INK))
    y += st.px(58)
    av = st.px(60)
    for col, im, name in ((col_w, av_w, g.white_name), (col_b, av_b, g.black_name)):
        a = im.convert("RGB").resize((av, av), Image.LANCZOS)
        img.paste(a, (x0 + col - av // 2, y))
        nm = name
        while text_w(f_name, nm) > st.px(170) and len(nm) > 4:
            nm = nm[:-2].rstrip() + "…"
        d.text((x0 + col - text_w(f_name, nm) / 2, y + av + st.px(8)), nm, font=f_name, fill=hex_rgb(INK))
    y += st.px(118)
    d.text((x0 + col_lab, y + st.px(8)), "Accuracy", font=f_row, fill=hex_rgb(INK))
    for col, side, dark in ((col_w, "W", False), (col_b, "B", True)):
        v = "%.1f" % stats["accuracy"][side]
        bw, bh = st.px(84), st.px(42)
        bx, by = x0 + col - bw // 2, y
        if dark:
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=st.px(6), fill=hex_rgb(BAR_DARK))
        else:
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=st.px(6), fill=(255, 255, 255),
                                outline=hex_rgb("#CFCFCF"), width=st.px(2))
        d.text((bx + (bw - text_w(f_acc, v)) / 2, by + st.px(8)), v, font=f_acc,
               fill=(255, 255, 255) if dark else hex_rgb(INK))
    y += st.px(58)
    ic = st.px(30)
    for label, cls in REVIEW_ROWS:
        d.text((x0 + col_lab, y + st.px(5)), label, font=f_row, fill=hex_rgb(INK))
        col = hex_rgb(TEXT_COLOR[cls])
        for c, side in ((col_w, "W"), (col_b, "B")):
            v = str(stats["counts"][side][cls])
            d.text((x0 + c - text_w(f_cnt, v) / 2, y + st.px(4)), v, font=f_cnt, fill=col)
        b = badge_image(cls, ic, st.bold, shadow=False)
        img.alpha_composite(b, (x0 + col_ic - b.width // 2, y + row_h // 2 - b.height // 2))
        y += row_h
    y += st.px(16)
    for ln in foot:
        while text_w(f_foot, ln) > W - 2 * pad and len(ln) > 8:
            ln = ln[:-2].rstrip() + "…"
        d.text((x0 + (W - text_w(f_foot, ln)) / 2, y), ln, font=f_foot, fill=hex_rgb("#555555"))
        y += st.px(32)
    return img


def render_review(out_path: str, frame: Image.Image, st: Style, g: F.Game, stats: dict,
                  av_w: Image.Image, av_b: Image.Image, ev_from: F.Eval, hold: float = 15.0,
                  fps: int = 30, ffmpeg: str = "ffmpeg") -> float:
    """Doan ket: khung cuoi lam toi, thanh eval chay ve ket qua, bang Game Review hien ra."""
    panel = review_panel(st, g, stats, av_w, av_b)
    px = (st.W - panel.width) // 2
    py = max(st.px(10), (st.H - panel.height) // 2 - st.px(10))
    f0, l0, w0 = bar_state(ev_from)
    if g.result == "*":
        f1, l1, w1 = f0, l0, w0
    else:
        f1 = result_fraction(g.result)
        l1, w1 = g.result.replace("1/2-1/2", "½-½"), f1 >= 0.5
    n_frames = max(1, int(round(hold * fps)))
    base = _dimmed(frame, DIM_ALPHA + 0.1)
    vw = VideoWriter(out_path, st.W, st.H, fps, ffmpeg)
    still = None
    try:
        for i in range(n_frames):
            t = i / fps
            if still is not None:
                vw.write(still)
                continue
            img = _dimmed(frame, (DIM_ALPHA + 0.1) * min(1.0, t / 0.25)) if t < 0.25 else base.copy()
            k = ease_in_out(t / 0.6)
            draw_bar(img, st, f0 + (f1 - f0) * k, l1 if k >= 0.5 else l0, w1 if k >= 0.5 else w0)
            a = ease_out((t - 0.3) / 0.45) if t >= 0.3 else 0.0
            if a > 0:
                p = panel if a >= 1 else panel.copy()
                if a < 1:
                    p.putalpha(p.getchannel("A").point(lambda v: int(v * a)))
                img.paste(p, (px, py + int((1 - a) * st.px(24))), p)
            if t > 0.8:
                still = img
            vw.write(img)
    finally:
        vw.close()
    return n_frames / fps


# ════════════════════════════════════════════════════════════════════════════
#  THUMBNAIL 1280x720
# ════════════════════════════════════════════════════════════════════════════
def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Cat phu kin w x h, canh theo mat lon nhat (neu co)."""
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    big = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    faces = detect_faces(big)
    if faces:
        x, y, fw, fh = faces[0]
        cx, cy = x + fw / 2, y + fh / 2
    else:
        cx, cy = big.width / 2, big.height / 2
    x0 = int(min(max(0, cx - w / 2), big.width - w))
    y0 = int(min(max(0, cy - h * 0.42), big.height - h))
    return big.crop((x0, y0, x0 + w, y0 + h))


def compose_thumbnail(spec: F.ThumbSpec, frames: Dict[int, Image.Image], font_bold: str = "") -> Image.Image:
    W, H = 1280, 720
    fb = find_font(True, font_bold)
    out = Image.new("RGB", (W, H), (255, 255, 255))
    bd = 230
    if spec.layout == "split" and len(spec.frames) >= 2:
        half = (W - 10) // 2
        out.paste(_cover_crop(frames[spec.frames[0]], half, H), (0, 0))
        out.paste(_cover_crop(frames[spec.frames[1]], half, H), (W - half, 0))
        badges = (spec.badges + spec.badges[-1:])[:2] if spec.badges else []
        spots = [(int(bd * 0.62), int(bd * 0.62)), (W - int(bd * 0.62), int(bd * 0.62))]
    else:
        out.paste(_cover_crop(frames[spec.frames[0]], W, H), (0, 0))
        badges = spec.badges[:2]
        faces = detect_faces(out)
        if faces:
            x, y, fw, fh = faces[0]
            left = (int(max(bd * 0.6, x - bd * 0.55)), int(max(bd * 0.6, y + fh * 0.15)))
            right = (int(min(W - bd * 0.6, x + fw + bd * 0.55)), int(max(bd * 0.6, y + fh * 0.15)))
        else:
            left, right = (int(W * 0.2), int(H * 0.32)), (int(W * 0.8), int(H * 0.32))
        if len(badges) == 1:
            spots = [right if right[0] < W - bd * 0.6 or left[0] <= bd * 0.6 else left]
        else:
            spots = [left, right]
    for cls, (cx, cy) in zip(badges, spots):
        b = badge_image(cls, bd, fb)
        out.paste(b, (cx - b.width // 2, cy - b.height // 2), b)
    if spec.text:
        f = _font(fb, 96)
        d = ImageDraw.Draw(out)
        tw = text_w(f, spec.text)
        bx0 = int((W - tw) / 2 - 28)
        d.rounded_rectangle([bx0, H - 170, bx0 + int(tw) + 56, H - 40], radius=18, fill=(255, 255, 255))
        d.text(((W - tw) / 2, H - 160), spec.text, font=f, fill=hex_rgb(INK))
    return out
