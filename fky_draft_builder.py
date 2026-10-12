# -*- coding: utf-8 -*-
"""
fky_draft_builder.py  —  LOI SINH DRAFT CAPCUT cho FKY (ban production, khong phai test)
=========================================================================================
Module nay nhan DU LIEU 1 video (danh sach beat + span thoi gian + duong dan anh) va
sinh ra 1 draft CapCut hoan chinh, dung TOAN BO luat da chot:

  - Dong MAIN (day): chi nen trang, chay het video.
  - Dong 2 (base): anh nen tung beat (scene = anh scene ; concept = Fred-tren-trang).
  - Dong 3+ : element (chi beat concept).
  - Audio dong 1: VO. Tu 25/09/2026 bi CAT truoc moi the Sign de chen khoang nghi (spec.pauses).
  - BEAT SCENE: scale co dinh 104% (doi 24/09/2026). Tu 25/09/2026 beat scene dai >= 5s, khong
        anh phu, zoom cham 104% -> 124% tren ca clip.
  - ANH NEN khi vao nen trang / the Sign: truot vao 0.5s (Slide Up/Left/Right) — 25/09/2026.
  - ELEMENT: 2 anh phu dau cua beat truot vao tu canh gan nhat, 0.5s; tu anh thu 3 -> fade in
    (01/10/2026). Beat UNDERGO -> fade in het.
        element ngan hon 0.5s -> chuyen dong = do dai element.
        element bat dau truot 0.4s TRUOC CHU no dai dien (25/09/2026), chay den het beat.
  - The chu (25/09/2026): Sign "Sign N:\\nTEN" can trai goc duoi phai; dong hook cuoi co tag
        [HOOK-TITLE] -> go nguyen cau hook in hoa, can giua (nguoi dung tu cat gon). Font lay tu font_path, trang, vien den, hieu ung Type 1 dai 1.1-2.0s, hien 0.7s
        sau dau beat va giu toi het beat.

Cach dung tu tool:
    from fky_draft_builder import build_draft, Beat, Element
    build_draft(spec)              # spec: xem dataclass DraftSpec ben duoi

Huong truot (DA XAC NHAN dung tren CapCut 9.1.0 qua test4):
    o cot trai  -> vao tu TRAI  (向右滑动)
    o cot phai  -> vao tu PHAI  (向左滑动)
    o hang tren -> vao tu TREN  (向下滑动)
    o hang duoi -> vao tu DUOI  (向上滑动)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

from pycapcut import (
    DraftFolder, IntroType, AudioSegment, AudioMaterial, ClipSettings,
    TextSegment, TextStyle, TextBorder, FontType, TextIntro, KeyframeProperty,
    TrackType, VideoSegment, trange,
)

SEC = 1_000_000
ANIM = 0.5          # do dai chuyen dong element (giay) — 25/09/2026: 0.8 -> 0.5 nhu nguoi dung chinh tay
EL_LEAD = 0.4       # element bat dau truot vao 0.4s truoc chu no dai dien -> hien ro dung luc doc chu
SCENE_SCALE = 1.04  # scale co dinh anh beat scene (24/09/2026: bo keyframe zoom 100->110%)
EL_SCALE = 0.80     # scale moi anh phu (01/10/2026), thu nho quanh tam cua manh — xem element_clip
# 25/09/2026: anh scene giu lau thi zoom cham +20% tren ca clip (dung nhu nguoi dung tu lam tay)
ZOOM_MIN_LEN = 5.0  # beat scene (khong co anh phu) dai tu 5s tro len moi zoom
ZOOM_GAIN = 0.20    # 1.04 -> 1.24
SLIDE_PIECES = 2    # 01/10/2026: so anh phu dau moi beat duoc truot vao; anh sau do fade in
# 02/10/2026: ANH POV (Fred tu sau lung, tu vai tro len, cam dien thoai) = anh phu cua canh nhan
# tin. "O" gia POV_CELL (khop fky_make_draft.py + app). Khong thu nho 80% nhu anh phu thuong:
# scale POV_SCALE, day vai SAT MEP DUOI khung, luon Slide Up (truot tu duoi len), khong tinh vao
# SLIDE_PIECES.
POV_CELL = 10
POV_SCALE = 0.85   # 03/10/2026: 1.0 -> 0.85 theo nguoi dung
BASE_INTRO = 0.5   # hieu ung truot vao cua anh nen khi vao nen trang / the sign (giay)
# The chu (25/09/2026, lay tu draft nguoi dung da sua tay):
TITLE_DELAY = 0.7   # the hien 0.7s sau dau beat (sau hieu ung truot + whoosh), chay toi het beat
SIGN_POS = (0.25, -0.58)     # the Sign: goc duoi ben phai, can trai, 2 dong "Sign N:" / TEN
SIGN_SCALE = 1.04
SIGN_SIZE = 10.0             # co chu the Sign (26/09/2026: 12 -> 10 theo nguoi dung)
HOOK_POS = (0.0, -0.51)      # the cau hook ([HOOK-TITLE]): giua duoi, can giua
HOOK_SCALE = 1.5
# The ket qua nghien cuu [FINDING: …] (01/10/2026): cung font / mau / vien / go chu nhu the Sign,
# in hoa, giua duoi nhu the hook, ngat ~30 ky tu, toi da 3 dong (dai hon thi tu thu nho cho vua khung)
FINDING_POS = (0.0, -0.51)
FINDING_SIZE = 5.0
FINDING_SCALE = 1.5
FINDING_WIDTH = 30
FINDING_MAX_LINES = 3
TITLE_LEN = 3.0    # (giu cho tuong thich script cu; khong con dung de cat the)


def title_anim_len(text: str) -> float:
    """Do dai hieu ung go chu (Type 1) = do dai tieng typing. Nguoi dung chinh tay ra 1.1-2.0s
    tuy do dai chu -> ~0.055s moi ky tu, kep trong [1.1, 2.0]."""
    n = len((text or "").replace("\n", ""))
    return max(1.1, min(2.0, 0.055 * n))


def sign_card_text(tag: str) -> str:
    """'Sign 1 — THE BLANK STARE' -> 'Sign 1:\\nTHE BLANK STARE' (kieu nguoi dung sua tay)."""
    import re
    m = re.match(r"^\s*(sign\s*\d+)\s*[—–:\-]+\s*(.+)$", tag or "", flags=re.IGNORECASE)
    if not m:
        return (tag or "").strip()
    return "%s:\n%s" % (m.group(1).strip().capitalize(), m.group(2).strip().upper())


def hook_card_text(sentence: str, width: int = 30) -> str:
    """NGUYEN CAU hook (loi VO cua dong [HOOK-TITLE]) in hoa, ngat dong ~30 ky tu.
    Nguoi dung tu cat gon trong CapCut (25/09/2026)."""
    lines, cur = [], ""
    for w in (sentence or "").strip().upper().split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)

# canh gan nhat -> hieu ung intro (da xac nhan chieu qua test4)
_SLIDE = {
    "left":   IntroType["向右滑动"],
    "right":  IntroType["向左滑动"],
    "top":    IntroType["向下滑动"],
    "bottom": IntroType["向上滑动"],
}
_FADE = IntroType["渐显"]
# anh nen truot vao (25/09/2026) — dung dung 3 hieu ung nguoi dung hay chon: Slide Up/Left/Right
_BASE_SLIDE = {
    "up":    IntroType["向上滑动"],
    "left":  IntroType["向左滑动"],
    "right": IntroType["向右滑动"],
}


def card_start(b) -> float:
    """Luc the chu (Sign / ten video) bat dau: TITLE_DELAY sau dau beat (beat ngan thi som hon).
    make_draft dung lai de dat tieng typing trung dung hieu ung go chu."""
    return b.start + min(TITLE_DELAY, max(0.0, (b.end - b.start) - 1.0))


def card_text(b) -> str:
    """Chu tren the cua beat ('' neu beat khong co the)."""
    if b.sign_title:
        return sign_card_text(b.sign_title)
    if b.video_title:
        return hook_card_text(b.video_title)
    if getattr(b, "finding", None):
        return finding_card(b.finding)[0]
    return ""


def finding_card(text: str):
    """01/10/2026: the ket qua nghien cuu -> (chu, scale). In hoa, bo dau cham cuoi, ngat dong
    FINDING_WIDTH ky tu. Dai qua FINDING_MAX_LINES dong thi ngat rong hon va thu nho scale theo
    dung ti le -> luon nam gon trong khung."""
    t = (text or "").strip().rstrip(".!?,;:").strip()
    width, scale = FINDING_WIDTH, FINDING_SCALE
    out = hook_card_text(t, width)
    while out.count("\n") + 1 > FINDING_MAX_LINES and width < 60:
        width += 6
        scale = FINDING_SCALE * FINDING_WIDTH / width
        out = hook_card_text(t, width)
    # chia deu cac dong (khong de 1 chu le loi o dong cuoi): thu hep be rong toi muc nho nhat
    # ma so dong khong tang
    n = out.count("\n") + 1
    w = width
    while w > 8 and hook_card_text(t, w - 1).count("\n") + 1 == n:
        w -= 1
    return hook_card_text(t, w), scale


def element_in_time(b, el) -> float:
    """Luc anh phu bat dau truot vao: EL_LEAD truoc chu no dai dien, khong truoc dau beat.
    make_draft dung lai ham nay de dat tieng pop dung luc."""
    return max(b.start, el.vo_time - EL_LEAD)


def element_clip(path: str) -> ClipSettings:
    """01/10/2026 theo yeu cau nguoi dung: moi anh phu scale EL_SCALE. Anh phu la anh TOAN KHUNG
    (manh nam san o o cua no, phan con lai trong suot) nen thu nho quanh TAM CUA MANH (tinh tu vung
    khong trong suot) — manh nho lai nhung van nam dung o, khong troi vao giua de len Fred.
    Khong doc duoc kenh alpha thi thu nho quanh tam khung."""
    tx = ty = 0.0
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.mode == "RGBA":
                box = im.getchannel("A").getbbox()
                if box:
                    W, H = im.size
                    cx = (box[0] + box[2]) / W - 1.0        # don vi: nua khung, -1 trai .. 1 phai
                    cy = 1.0 - (box[1] + box[3]) / H        # -1 duoi .. 1 tren
                    tx, ty = (1 - EL_SCALE) * cx, (1 - EL_SCALE) * cy
    except Exception:
        pass
    return ClipSettings(scale_x=EL_SCALE, scale_y=EL_SCALE, transform_x=tx, transform_y=ty)


def pov_clip(path: str) -> ClipSettings:
    """02/10/2026: anh POV scale POV_SCALE, giu nguyen vi tri ngang cua hinh, day MEP DUOI cua
    hinh (tinh tu vung khong trong suot) xuong sat mep duoi khung -> vai Fred moc tu day khung len.
    Khong doc duoc alpha thi chi scale quanh tam."""
    s = POV_SCALE
    tx = ty = 0.0
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.mode == "RGBA":
                box = im.getchannel("A").getbbox()
                if box:
                    W, H = im.size
                    cx = (box[0] + box[2]) / W - 1.0        # -1 trai .. 1 phai
                    yb = 1.0 - 2.0 * box[3] / H             # mep duoi cua hinh, -1 = day khung
                    tx = (1 - s) * cx
                    ty = -1.0 - s * yb
    except Exception:
        pass
    return ClipSettings(scale_x=s, scale_y=s, transform_x=tx, transform_y=ty)


def _nearest_edge(cell: int) -> str:
    c = (cell - 1) % 3
    if c == 0:
        return "left"
    if c == 2:
        return "right"
    # cot giua: khong co canh ngang -> dung hang de chon tren/duoi
    r = (cell - 1) // 3
    return "top" if r == 0 else "bottom"


def _white_png(path, w=1920, h=1080):
    """PNG trang duc w x h bang thu vien chuan (khong can PIL)."""
    import struct
    import zlib
    raw = (b"\x00" + b"\xff" * (w * 3)) * h

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return path


def _span(start: float, end: float):
    """trange tu giay dau/cuoi. Lam tron DAU va CUOI truoc roi moi tru ra do dai, de clip
    sau bat dau dung cho clip truoc ket thuc (lam tron rieng dau + do dai co the lech 1 us
    -> pycapcut bao SegmentOverlap)."""
    s, e = int(round(start * SEC)), int(round(end * SEC))
    return trange(s, e - s)


def _anim_dur(clip_dur: float) -> float:
    return ANIM if clip_dur >= ANIM else clip_dur   # ngan hon 0.8s -> = do dai (>=50%)


# ------------------------------------------------------------------ data model
@dataclass
class Element:
    image: str          # duong dan anh element (vd .../002a.png)
    cell: int           # o trong grid 3x3 (1..9)
    vo_time: float      # giay cau VO cua element vang len (tuyet doi trong video)
    group: Optional[str] = None   # 01/10/2026: cung khoa voi 1 Sfx -> anh + am thanh thanh 1 GROUP CapCut


@dataclass
class Beat:
    kind: str                       # "scene" hoac "concept"
    start: float                    # giay bat dau (tuyet doi)
    end: float                      # giay ket thuc (tuyet doi)
    base_image: str                 # scene: anh scene ; concept: anh Fred-tren-trang
    function: str = "present"       # concept: present/demonstrate/undergo/...
    elements: List[Element] = field(default_factory=list)
    sign_title: Optional[str] = None
    video_title: Optional[str] = None   # 25/09/2026: nguyen cau hook cua dong [HOOK-TITLE] -> the chu
    finding: Optional[str] = None       # 01/10/2026: chu cua tag [FINDING: …] -> the ket qua nghien cuu
    intro: Optional[str] = None         # 25/09/2026: "up"/"left"/"right" -> anh nen truot vao


@dataclass
class Sfx:
    path: str           # file am thanh trong thu vien sfx_library
    start: float        # giay bat dau (tuyet doi)
    dur: float          # do dai mong muon (bi cat theo do dai file)
    volume: float = 1.0
    src_start: float = 0.0   # 25/09/2026: bo doan im lang dau file (typing.mp3 ~0.35s)
    group: Optional[str] = None   # 01/10/2026: cung khoa voi 1 Element -> group chung trong CapCut


@dataclass
class DraftSpec:
    draft_root: str
    draft_name: str
    white_image: str                # anh nen trang full khung
    vo_path: str                    # file VO hoan chinh
    total_duration: float           # do dai video (giay)
    beats: List[Beat]
    width: int = 1920
    height: int = 1080
    fps: int = 30
    font_path: str = ""             # trong -> System
    caption_srt: str = ""           # file SRT -> track caption (an san); trong -> khong caption
    sfx: List[Sfx] = field(default_factory=list)   # hieu ung am thanh (24/09/2026)
    # 25/09/2026: khoang nghi chen vao VO truoc moi the Sign — [(moc giay trong VO GOC, do dai)].
    # Moi thoi gian khac trong spec (beat, element, sfx, total_duration) DA duoc doi theo roi;
    # builder chi dung danh sach nay de CAT VO thanh doan va doi gio caption SRT.
    pauses: List[tuple] = field(default_factory=list)


def shift_time(t: float, pauses) -> float:
    """Doi 1 moc thoi gian cua VO GOC sang timeline da chen khoang nghi."""
    return t + sum(d for p, d in pauses if p <= t)


# ------------------------------------------------------------------- builder
def build_draft(spec: DraftSpec) -> str:
    folder = DraftFolder(spec.draft_root)
    sc = folder.create_draft(spec.draft_name, spec.width, spec.height,
                             fps=spec.fps, allow_replace=True)
    # 24/09/2026: anh trang nam TRONG thu muc draft (khong rai vao folder anh cua nguoi dung)
    white = spec.white_image or _white_png(
        os.path.join(spec.draft_root, spec.draft_name, "white.png"), spec.width, spec.height)

    max_el = max((len(b.elements) for b in spec.beats), default=0)

    sc.add_track(TrackType.video, "white_main", relative_index=0)
    sc.add_track(TrackType.video, "base", relative_index=1)
    for i in range(max_el):
        sc.add_track(TrackType.video, "el_%d" % i, relative_index=2 + i)
    sc.add_track(TrackType.text, "title")
    sc.add_track(TrackType.audio, "vo")

    # nen trang chay het video
    sc.add_segment(VideoSegment(white, trange(0, spec.total_duration * SEC)),
                   "white_main")
    # VO: khong co khoang nghi -> 1 doan nguyen; co -> cat tai tung moc, doan sau dich lui
    _add_vo(sc, spec)
    groups = {}     # 01/10/2026: khoa group -> [segment id] (anh phu + tieng pop cua no)
    _add_sfx(sc, spec.sfx, spec.total_duration, groups)

    use_font = FontType.Montserrat if (spec.font_path and os.path.isfile(spec.font_path)) else None

    for b in spec.beats:
        b_len = b.end - b.start
        # 24/09/2026 theo yeu cau nguoi dung: beat scene scale CO DINH SCENE_SCALE.
        # 25/09/2026: rieng beat scene giu lau (>= ZOOM_MIN_LEN, khong co anh phu) zoom cham
        # SCENE_SCALE -> SCENE_SCALE + ZOOM_GAIN tren ca clip.
        clip = ClipSettings(scale_x=SCENE_SCALE, scale_y=SCENE_SCALE) if b.kind == "scene" else None
        base = VideoSegment(b.base_image or white, _span(b.start, b.end),   # thieu anh -> trang
                            clip_settings=clip)
        if b.kind == "scene" and not b.elements and b_len >= ZOOM_MIN_LEN:
            base.add_keyframe(KeyframeProperty.uniform_scale, 0, SCENE_SCALE)
            base.add_keyframe(KeyframeProperty.uniform_scale, base.target_timerange.duration,
                              SCENE_SCALE + ZOOM_GAIN)
        if b.intro in _BASE_SLIDE:
            base.add_animation(_BASE_SLIDE[b.intro], duration="%gs" % min(BASE_INTRO, b_len))
        sc.add_segment(base, "base")

        # element: beat concept present/list, va tu 24/09/2026 ca beat scene co {cell N}
        if b.elements:
            n_plain = 0          # so anh phu thuong (khong tinh anh POV) da dat
            for i, el in enumerate(b.elements):
                t_in = element_in_time(b, el)
                dur = b.end - t_in
                if dur <= 0:
                    continue
                if el.cell == POV_CELL:
                    # 02/10/2026: anh POV -> sat mep duoi, luon truot tu duoi len
                    seg = VideoSegment(el.image, _span(t_in, b.end), clip_settings=pov_clip(el.image))
                    intro = _SLIDE["bottom"]
                else:
                    seg = VideoSegment(el.image, _span(t_in, b.end),
                                       clip_settings=element_clip(el.image))
                    # 01/10/2026: chi 2 anh phu dau cua beat truot vao, tu anh thu 3 -> fade in
                    slide = b.function != "undergo" and n_plain < SLIDE_PIECES
                    intro = _SLIDE[_nearest_edge(el.cell)] if slide else _FADE
                    n_plain += 1
                seg.add_animation(intro, duration="%gs" % _anim_dur(dur))
                sc.add_segment(seg, "el_%d" % i)
                if el.group:
                    groups.setdefault(el.group, []).append(seg.segment_id)

        card = None
        if b.sign_title:
            card = (sign_card_text(b.sign_title), SIGN_SIZE, 0, SIGN_POS, SIGN_SCALE)
        elif b.video_title:
            card = (hook_card_text(b.video_title), 5.0, 1, HOOK_POS, HOOK_SCALE)
        elif b.finding:
            ftext, fscale = finding_card(b.finding)
            card = (ftext, FINDING_SIZE, 1, FINDING_POS, fscale)
        if card:
            text, size, align, (px, py), scale = card
            t0 = card_start(b)
            seg = TextSegment(
                text, _span(t0, b.end), font=use_font,
                style=TextStyle(size=size, bold=True, color=(1.0, 1.0, 1.0), align=align),
                border=TextBorder(color=(0.0, 0.0, 0.0), width=30.0, alpha=1.0),
                clip_settings=ClipSettings(transform_x=px, transform_y=py,
                                           scale_x=scale, scale_y=scale),
            )
            seg.add_animation(TextIntro.打字机_I, duration="%gs" % min(title_anim_len(text), b.end - t0))
            sc.add_segment(seg, "title")

    # 24/09/2026: caption tu file SRT, nam tren track rieng, AN san (show track = off)
    n_cap = _add_captions(sc, spec.caption_srt, spec.pauses) if spec.caption_srt else 0

    sc.save()
    out = os.path.join(spec.draft_root, spec.draft_name, "draft_content.json")

    if use_font is None and not n_cap and not groups:
        return out
    d = json.load(open(out, encoding="utf-8"))
    _write_groups(d, groups)
    cap_ids = set()
    for t in d.get("tracks", []):
        if t.get("name") == CAPTION_TRACK:
            # giong het khi CapCut tu nhap phu de roi bam an track:
            # attribute 2 = an track, flag 1 = track phu de
            t["attribute"] = 2
            t["flag"] = 1
            cap_ids.update(s["material_id"] for s in t.get("segments", []))
    for t in d["materials"].get("texts", []):
        if t["id"] in cap_ids:
            t["type"] = "subtitle"
            continue                 # caption giu font he thong nhu CapCut, khong va Montserrat
        if use_font is not None:
            # va font local — CapCut se thu doc font tu path nay
            c = json.loads(t["content"])
            for st in c.get("styles", []):
                if "font" in st:
                    st["font"]["path"] = spec.font_path.replace("\\", "/")
            t["content"] = json.dumps(c, ensure_ascii=False)
    json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def _add_vo(sc, spec: DraftSpec) -> None:
    """VO len track 'vo'. Co khoang nghi (the Sign) -> cat VO GOC tai tung moc p, doan tu p
    tro di dich lui them do dai khoang nghi; khoang trong giua 2 doan la im lang."""
    mat = AudioMaterial(spec.vo_path)
    src_total = mat.duration / SEC
    cuts = sorted(p for p, _d in spec.pauses if 0 < p < src_total)
    edges = [0.0] + cuts + [src_total]
    for a, z in zip(edges[:-1], edges[1:]):
        if z - a < 0.01:
            continue
        t0 = shift_time(a, spec.pauses)
        t1 = min(t0 + (z - a), spec.total_duration)
        s_us, e_us = int(round(t0 * SEC)), int(round(t1 * SEC))
        if e_us - s_us <= 0:
            continue
        sc.add_segment(AudioSegment(mat, trange(s_us, e_us - s_us),
                                    source_timerange=trange(int(round(a * SEC)), e_us - s_us)), "vo")


def _add_sfx(sc, sfx: List[Sfx], total: float, groups: Optional[dict] = None) -> int:
    """Rai hieu ung am thanh len cac track sfx_0, sfx_1… — am nao chong gio am truoc thi
    sang track ke tiep. Moi am bi cat theo do dai file va khong vuot qua cuoi video."""
    if not sfx:
        return 0
    mats, lanes, placed = {}, [], []
    for s in sorted(sfx, key=lambda x: x.start):
        if s.path not in mats:
            mats[s.path] = AudioMaterial(s.path)
        m = mats[s.path]
        st = max(0.0, s.start)
        src0 = max(0.0, min(s.src_start, m.duration / SEC - 0.05))
        en = min(st + s.dur, st + m.duration / SEC - src0, total)
        if en - st < 0.05:
            continue
        lane = next((i for i, end in enumerate(lanes) if end <= st), None)
        if lane is None:
            lanes.append(0.0)
            lane = len(lanes) - 1
        lanes[lane] = en
        placed.append((lane, m, st, en, s.volume, src0, s.group))
    for i in range(len(lanes)):
        sc.add_track(TrackType.audio, "sfx_%d" % i)
    for lane, m, st, en, vol, src0, gkey in placed:
        s_us, e_us = int(round(st * SEC)), int(round(en * SEC))
        seg = AudioSegment(m, trange(s_us, e_us - s_us),
                           source_timerange=trange(int(round(src0 * SEC)), e_us - s_us),
                           volume=vol)
        sc.add_segment(seg, "sfx_%d" % lane)
        if gkey and groups is not None:
            groups.setdefault(gkey, []).append(seg.segment_id)
    return len(placed)


def _write_groups(d: dict, groups: dict) -> int:
    """01/10/2026: anh phu + tieng pop cua no -> 1 GROUP CapCut (nhu nguoi dung bam Ctrl+G).
    Cau truc chep tu group nguoi dung tu lam trong draft comfort-everyone: group_container.groups
    = [{id, group_index, group_members = id cac segment, material_draft = 1 draft rong}]. Draft
    rong lay tu chinh draft nay (giu config / platform), bo het track, vat lieu, keyframe."""
    import copy
    import uuid
    members = [ids for ids in groups.values() if len(ids) >= 2]
    if not members:
        return 0
    stub = {k: copy.deepcopy(v) for k, v in d.items()
            if k not in ("tracks", "materials", "keyframes", "group_container")}
    stub.update(name="", duration=0, create_time=0, update_time=0, tracks=[], group_container=None,
                keyframe_graph_list=[], relationships=[], path="", cover=None,
                canvas_config={"ratio": "original", "width": 0, "height": 0, "background": None})
    stub["materials"] = {k: [] for k in d.get("materials", {})}
    stub["keyframes"] = {k: [] for k in d.get("keyframes", {})}
    out = []
    for gi, ids in enumerate(members):
        sd = copy.deepcopy(stub)
        sd["id"] = str(uuid.uuid4()).upper()
        out.append({
            "id": str(uuid.uuid4()).upper(), "group_index": gi, "group_members": ids,
            "material_draft": {
                "id": str(uuid.uuid4()).upper(), "type": "composition", "name": "",
                "category_id": "", "category_name": "", "formula_id": "", "combination_id": "",
                "combination_type": "none", "aimusic_mv_template_info": None,
                "precompile_combination": False, "draft": sd,
                "draft_file_path": "", "draft_cover_path": "", "draft_config_path": "",
            },
            "group_main_id": "", "is_expand": True, "name": "",
        })
    gid = {sid: g["id"] for g in out for sid in g["group_members"]}
    for t in d.get("tracks", []):
        for s in t.get("segments", []):
            if s.get("id") in gid:
                s["group_id"] = gid[s["id"]]
    d["group_container"] = {"groups": out}
    return len(out)


CAPTION_TRACK = "caption"


def _add_captions(sc, srt_path: str, pauses=()) -> int:
    """Doc SRT, moi cue 1 TextSegment tren track CAPTION_TRACK, style giong CapCut tu nhap
    phu de (co 5, can giua, tu xuong dong, sat day khung). Cue chong gio len cue truoc thi
    day lui dau cue cho khit (tranh SegmentOverlap). Tra ve so cue da them.
    25/09/2026: gio cue doi theo khoang nghi da chen truoc the Sign (pauses)."""
    import re
    if not os.path.isfile(srt_path):
        print("⚠️  Khong thay file SRT cho caption: %s -> bo qua caption." % srt_path)
        return 0
    text = open(srt_path, encoding="utf-8-sig").read()

    def us(t):
        h, m, s = t.replace(",", ".").split(":")
        sec = int(h) * 3600 + int(m) * 60 + float(s)
        return int(round(shift_time(sec, pauses) * SEC))
    blocks = re.split(r"\n\s*\n", text.strip())
    sc.add_track(TrackType.text, CAPTION_TRACK, relative_index=999)
    style = TextStyle(size=5, align=1, auto_wrapping=True)
    last_end, n = 0, 0
    for blk in blocks:
        lines = [ln for ln in blk.strip().splitlines() if ln.strip()]
        k = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if k is None:
            continue
        a, b = [x.strip() for x in lines[k].split("-->")]
        s, e = max(us(a), last_end), us(b)
        body = "\n".join(lines[k + 1:]).strip()
        if not body or e <= s:
            continue
        sc.add_segment(TextSegment(body, trange(s, e - s), style=style,
                                   clip_settings=ClipSettings(transform_y=-0.8)), CAPTION_TRACK)
        last_end, n = e, n + 1
    return n


# ------------------------------------------------------- self-check (chay truc tiep)
if __name__ == "__main__":
    import subprocess, sys
    root = sys.argv[1] if len(sys.argv) > 1 else "._selfcheck"
    os.makedirs(root, exist_ok=True)
    ad = os.path.join(root, "_assets")
    os.makedirs(ad, exist_ok=True)

    def ff(vf, out):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "quiet", "-f", "lavfi",
                        "-i", vf, "-frames:v", "1", out], check=True)

    ff("color=c=white:s=1920x1080", os.path.join(ad, "white.png"))
    ff("color=c=0x88AACC:s=1920x1080", os.path.join(ad, "scene1.png"))     # beat scene
    ff("color=c=black@0.0:s=1920x1080,format=rgba,drawbox=x=760:y=140:w=400:h=860:color=0x1E88E5:t=fill",
       os.path.join(ad, "fred2.png"))                                       # beat concept base
    ff("color=c=black@0.0:s=1920x1080,format=rgba,drawbox=x=70:y=70:w=500:h=220:color=0xC84B31:t=fill",
       os.path.join(ad, "el_a.png"))
    ff("color=c=black@0.0:s=1920x1080,format=rgba,drawbox=x=1350:y=70:w=500:h=220:color=0x5B8B8E:t=fill",
       os.path.join(ad, "el_b.png"))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "quiet", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=stereo", "-t", "18", os.path.join(ad, "vo.mp3")], check=True)

    spec = DraftSpec(
        draft_root=root, draft_name="fky-selfcheck",
        white_image=os.path.join(ad, "white.png"),
        vo_path=os.path.join(ad, "vo.mp3"),
        total_duration=16.0,
        font_path="",
        beats=[
            Beat(kind="scene", start=0.0, end=6.0, base_image=os.path.join(ad, "scene1.png")),
            Beat(kind="concept", function="present", start=6.0, end=16.0,
                 base_image=os.path.join(ad, "fred2.png"),
                 sign_title="SIGN 1 — SELF CHECK",
                 elements=[
                     Element(os.path.join(ad, "el_a.png"), cell=1, vo_time=8.0),
                     Element(os.path.join(ad, "el_b.png"), cell=3, vo_time=11.0),
                 ]),
        ],
    )
    print("OK ->", build_draft(spec))
