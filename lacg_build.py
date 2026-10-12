# -*- coding: utf-8 -*-
"""
lacg_build.py — SINH DRAFT CAPCUT cho Like a Chess Game
=======================================================
episode.json (app ghi) + clip.mp4 + transcript.json + moves.txt  ->  draft CapCut

Timeline:
  track video "main"    : [clip 0..t1] [DUNG HINH nuoc 1] [clip t1..t2] [DUNG HINH nuoc 2] ... [clip ..het] [GAME REVIEW]
                          (clip = cung 1 file mp4, moi doan 1 khoang nguon; dung hinh = mp4 da render san)
  track video "bar"     : thanh eval (PNG) chi trong cac doan clip dang chay (doan dung hinh da co thanh truot)
  track video "players" : o player tren/duoi (PNG) suot video
  track text  "subs"    : phu de thoai (1 nguoi / dong), cau dang noi luc dung hinh duoc giu suot doan dung
  track audio "sfx_*"   : am badge theo loai nuoc, tieng go phim, checkmate

Xuat them: chapters.txt (dan vao /metadata), frames/M01.png... (cho /thumbnail), dialogue.srt.

Chay:  python lacg_build.py --episode <thu_muc_tap>/episode.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

from PIL import Image

import lacg_formats as F
import lacg_render as R

SEC = 1_000_000
SFX_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg")

DEFAULTS = {
    "width": 1920, "height": 1080, "fps": 30,
    "cps": 30.0,            # toc do go chu (ky tu / giay)
    "pause": 2.5,           # dung doc sau khi go xong (giay)
    "review_hold": 15.0,    # do dai bang Game Review (cho end screen YouTube)
    "lead_out": 0.12,       # dung hinh tre hon tu cuoi cua cau trich bao nhieu giay
    "chapter_lead": 2.0,    # chapter bat dau truoc luc dung hinh bao nhieu giay
    "sub_size": 7.0, "sub_y": -0.80,
    "sfx_volume": 0.9, "typing_volume": 0.55,
    "font_regular": "", "font_bold": "",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def ffprobe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", path], stdout=subprocess.PIPE, text=True).stdout.strip()
    return float(out or 0)


def extract_frame(clip: str, t: float, out_png: str) -> Image.Image:
    flags = 0x08000000 if os.name == "nt" else 0
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.3f" % max(0.0, t), "-i", clip,
                    "-frames:v", "1", out_png], check=True, creationflags=flags)
    if not os.path.isfile(out_png):                       # sat cuoi file: lui 0.2s
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.3f" % max(0.0, t - 0.2), "-i", clip,
                        "-frames:v", "1", out_png], check=True, creationflags=flags)
    return Image.open(out_png).convert("RGB")


def find_sfx(folder: str, name: str) -> str:
    if not folder:
        return ""
    for ext in SFX_EXT:
        p = os.path.join(folder, name + ext)
        if os.path.isfile(p):
            return p
    return ""


# ════════════════════════════════════════════════════════════════════════════
#  TAP (episode.json)
# ════════════════════════════════════════════════════════════════════════════
class Episode:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.dir = os.path.dirname(self.path)
        self.cfg = dict(DEFAULTS)
        self.cfg.update(json.load(open(self.path, encoding="utf-8")))
        self.clip = self.cfg["clip"]
        self.render_dir = os.path.join(self.dir, "render")
        self.frames_dir = os.path.join(self.dir, "frames")
        self.transcript_path = self.cfg.get("transcript") or os.path.join(self.dir, "transcript.json")
        self.moves_path = self.cfg.get("moves") or os.path.join(self.dir, "moves.txt")

    def load(self) -> Tuple[F.Game, dict, List[str]]:
        """-> (game da resolve anchor, transcript, danh sach loi chan)."""
        data = F.load_transcript(self.transcript_path)
        g, errs = F.parse_moves(open(self.moves_path, encoding="utf-8").read())
        problems = list(errs)
        F.resolve_anchors(g, data, float(self.cfg["lead_out"]))
        pos = self.cfg.get("badge_pos") or []
        for i, mv in enumerate(g.moves):
            mv.badge_pos = pos[i] if i < len(pos) and pos[i] else "auto"
            if mv.status.startswith("error") or mv.time is None:
                problems.append("Nước %d (%s): %s" % (i + 1, mv.name, mv.status or "chưa có thời điểm"))
        return g, data, problems

    def style(self) -> R.Style:
        return R.Style(int(self.cfg["width"]), int(self.cfg["height"]),
                       self.cfg.get("font_regular", ""), self.cfg.get("font_bold", ""))


def card_chips(g: F.Game, i: int) -> List[str]:
    mv = g.moves[i]
    chips = [mv.tag] if mv.tag else []
    first_book = next((k for k, m in enumerate(g.moves) if m.cls == "book"), None)
    if g.opening and first_book == i:
        chips.append(g.opening)
    return chips


def avatar_for(ep: Episode, g: F.Game, side: str, frames: Dict[int, Image.Image]) -> Image.Image:
    p = (ep.cfg.get("avatars") or {}).get(side, "")
    if p and os.path.isfile(p):
        return Image.open(p).convert("RGB")
    k = next((i for i, m in enumerate(g.moves) if m.side == side and i in frames), None)
    if k is not None:
        return R.crop_square_face(frames[k])
    return R.initials_avatar(g.white_name if side == "W" else g.black_name)


# ════════════════════════════════════════════════════════════════════════════
#  XEM TRUOC 1 THE (app goi truc tiep, khong can pycapcut)
# ════════════════════════════════════════════════════════════════════════════
def preview_move(ep: Episode, g: F.Game, index: int, out_png: str) -> str:
    st = ep.style()
    mv = g.moves[index]
    os.makedirs(ep.render_dir, exist_ok=True)
    raw = extract_frame(ep.clip, mv.time or 0.0, os.path.join(ep.render_dir, "_preview_frame.png"))
    frame = R.fit_canvas(raw, st.W, st.H)
    card = R.Card(st, index, mv, card_chips(g, index))
    bd = st.px(170)
    bc, cxy = R.place(st, frame, card.W, card.full_height, bd, mv.badge_pos)
    img = R._dimmed(frame, R.DIM_ALPHA).convert("RGBA")
    R.draw_bar(img, st, *R.bar_state(mv.eval))
    b = R.badge_image(mv.cls, bd, st.bold)
    img.alpha_composite(b, (bc[0] - b.width // 2, bc[1] - b.height // 2))
    c = card.render(card.n_chars, 1.0, 0)
    sp = st.px(14)
    img.alpha_composite(c, (cxy[0] - sp, cxy[1] - sp))
    av_w = R.initials_avatar(g.white_name)
    av_b = R.initials_avatar(g.black_name)
    img.alpha_composite(R.players_overlay(st, g.black_name, av_b, g.white_name, av_w))
    img.convert("RGB").save(out_png)
    return out_png


# ════════════════════════════════════════════════════════════════════════════
#  BUILD
# ════════════════════════════════════════════════════════════════════════════
def us(t: float) -> int:
    return int(round(t * SEC))


def build(ep: Episode) -> dict:
    t_start = time.time()
    g, data, problems = ep.load()
    if problems:
        raise RuntimeError("Khối MOVES còn lỗi, sửa trước khi sinh draft:\n  " + "\n  ".join(problems))
    for k, msg in F.validate_moves(g):
        log("⚠️  %s%s" % ("Nước %d: " % k if k else "", msg))
    for i, mv in enumerate(g.moves):
        if mv.status.startswith("warn"):
            log("⚠️  Nước %d (%s): %s" % (i + 1, mv.name, mv.status))

    st = ep.style()
    fps = int(ep.cfg["fps"])
    os.makedirs(ep.render_dir, exist_ok=True)
    os.makedirs(ep.frames_dir, exist_ok=True)
    clip_dur = ffprobe_duration(ep.clip)
    n = len(g.moves)
    # moc dung hinh: tang dan, khong sat cuoi file
    times: List[float] = []
    for mv in g.moves:
        t = min(max(mv.time or 0.0, times[-1] if times else 0.0), max(0.0, clip_dur - 0.06))
        times.append(t)

    log("[1/5] Trích %d khung hình dừng" % n)
    raw_frames: Dict[int, Image.Image] = {}
    for i, t in enumerate(times):
        raw_frames[i] = extract_frame(ep.clip, t, os.path.join(ep.frames_dir, "M%02d.png" % (i + 1)))
    last_raw = extract_frame(ep.clip, max(0.0, clip_dur - 0.06), os.path.join(ep.render_dir, "last_frame.png"))

    av_w = avatar_for(ep, g, "W", raw_frames)
    av_b = avatar_for(ep, g, "B", raw_frames)
    av_w.save(os.path.join(ep.render_dir, "avatar_W.png"))
    av_b.save(os.path.join(ep.render_dir, "avatar_B.png"))
    players_png = os.path.join(ep.render_dir, "players.png")
    R.players_overlay(st, g.black_name, av_b, g.white_name, av_w).save(players_png)
    bar_pngs = []
    evals = [g.start_eval] + [m.eval for m in g.moves]
    for k, ev in enumerate(evals):
        p = os.path.join(ep.render_dir, "bar_%02d.png" % k)
        R.bar_overlay(st, ev).save(p)
        bar_pngs.append(p)

    log("[2/5] Render %d đoạn dừng hình (gõ chữ, badge, thanh eval)" % n)
    freezes = []          # (path, duration, timing)
    for i, mv in enumerate(g.moves):
        frame = R.fit_canvas(raw_frames[i], st.W, st.H)
        card = R.Card(st, i, mv, card_chips(g, i))
        bd = st.px(170)
        bc, cxy = R.place(st, frame, card.W, card.full_height, bd, mv.badge_pos)
        out = os.path.join(ep.render_dir, "freeze_%02d.mp4" % (i + 1))
        tm = R.render_freeze(out, frame, st, card, bc, cxy, evals[i], mv.eval,
                             float(ep.cfg["cps"]), float(ep.cfg["pause"]), fps)
        freezes.append((out, tm["duration"], tm))
        log("   %2d/%d  %s  (%.1fs)" % (i + 1, n, F.move_line(i, mv), tm["duration"]))

    log("[3/5] Render Game Review")
    stats = F.review_stats(g)
    review_mp4 = os.path.join(ep.render_dir, "review.mp4")
    review_dur = R.render_review(review_mp4, R.fit_canvas(last_raw, st.W, st.H), st, g, stats, av_w, av_b,
                                 g.moves[-1].eval if g.moves else g.start_eval,
                                 float(ep.cfg["review_hold"]), fps)

    # ── timeline (micro giay nguyen, cac doan main noi khit nhau) ──
    plan = []             # (kind, target_start_us, dur_us, extra)
    cur = 0
    prev_t = 0.0
    freeze_starts_us = []
    span_index = []       # (target_start_us, dur_us, bar_index)
    for i, t in enumerate(times):
        d_src = us(t) - us(prev_t)
        if d_src >= us(1.0 / fps):
            plan.append(("clip", cur, d_src, us(prev_t)))
            span_index.append((cur, d_src, i))
            cur += d_src
        freeze_starts_us.append(cur)
        fd = us(freezes[i][1])
        plan.append(("freeze", cur, fd, freezes[i][0]))
        cur += fd
        prev_t = t
    tail = us(clip_dur) - us(prev_t)
    if tail >= us(1.0 / fps):
        plan.append(("clip", cur, tail, us(prev_t)))
        span_index.append((cur, tail, n))
        cur += tail
    review_start_us = cur
    plan.append(("review", cur, us(review_dur), review_mp4))
    cur += us(review_dur)
    total_us = cur

    # ── phu de: doi gio clip -> gio video cuoi; cau dang noi luc dung hinh duoc giu suot doan dung ──
    pauses = [(times[i], freezes[i][1]) for i in range(n)]
    cues = F.build_cues(data)
    F.write_srt(cues, os.path.join(ep.dir, "dialogue.srt"))
    starts = sorted(c.start for c in cues)
    for c in cues:
        for tk, _ in pauses:
            if c.start < tk and c.end <= tk < c.end + 0.6 and not any(c.end < s <= tk for s in starts):
                c.end = tk + 0.001
                break

    def to_final(t: float) -> float:
        return t + sum(d for tk, d in pauses if tk < t)

    final_cues = [F.Cue(to_final(c.start), to_final(c.end), c.text, c.label, c.dashed) for c in cues]
    F.write_srt(final_cues, os.path.join(ep.dir, "dialogue_final.srt"))

    # ── SFX ──
    sfx_dir = ep.cfg.get("sfx_dir", "")
    vol, tvol = float(ep.cfg["sfx_volume"]), float(ep.cfg["typing_volume"])
    sfx = []              # (path, start_s, max_dur_s, volume, loop)
    missing = set()
    for i, mv in enumerate(g.moves):
        f0 = freeze_starts_us[i] / SEC
        tm = freezes[i][2]
        mv_snd = find_sfx(sfx_dir, "move")
        if mv_snd:
            sfx.append((mv_snd, f0, 2.0, vol * 0.8, False))
        cls_snd = find_sfx(sfx_dir, mv.cls)
        if cls_snd:
            sfx.append((cls_snd, f0 + tm["badge"], 3.0, vol, False))
        else:
            missing.add(mv.cls)
        ty = find_sfx(sfx_dir, "typing")
        if ty:
            sfx.append((ty, f0 + tm["type_start"], tm["type_end"] - tm["type_start"], tvol, True))
        else:
            missing.add("typing")
    cm = find_sfx(sfx_dir, "checkmate")
    if cm:
        sfx.append((cm, review_start_us / SEC + 0.3, 6.0, vol, False))
    else:
        missing.add("checkmate")
    if missing:
        log("⚠️  Thiếu file SFX (bỏ qua): %s — đặt vào thư mục sfx/ với đúng tên." % ", ".join(sorted(missing)))

    log("[4/5] Ghi draft CapCut")
    draft_path = write_draft(ep, plan, span_index, bar_pngs, players_png, final_cues, sfx, total_us)

    log("[5/5] Xuất chapters + khung hình thumbnail")
    lead = float(ep.cfg["chapter_lead"])
    chap_times, last = [], -10.0
    for fs in freeze_starts_us:
        t = max(0.0, fs / SEC - lead)
        t = max(t, last + 1.0)
        chap_times.append(t)
        last = t
    chapters = F.chapters_block(g, chap_times, review_start_us / SEC)
    with open(os.path.join(ep.dir, "chapters.txt"), "w", encoding="utf-8") as f:
        f.write(chapters + "\n")
    report = {"draft": draft_path, "total": total_us / SEC, "review_start": review_start_us / SEC,
              "freeze_starts": [x / SEC for x in freeze_starts_us], "move_times": times,
              "chapters": chapters, "seconds": round(time.time() - t_start, 1)}
    json.dump(report, open(os.path.join(ep.dir, "build_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("✅ Xong (%.0fs): video %s, draft: %s" % (time.time() - t_start, F.fmt_mss(total_us / SEC), draft_path))
    return report


def write_draft(ep: Episode, plan, span_index, bar_pngs, players_png, cues, sfx, total_us) -> str:
    from pycapcut import (AudioMaterial, AudioSegment, ClipSettings, DraftFolder, TextBorder, TextSegment,
                          TextStyle, TrackType, VideoMaterial, VideoSegment, trange)
    W, H, fps = int(ep.cfg["width"]), int(ep.cfg["height"]), int(ep.cfg["fps"])
    root = ep.cfg.get("draft_root") or os.path.join(ep.dir, "_drafts")
    name = ep.cfg.get("draft_name") or "lacg"
    os.makedirs(root, exist_ok=True)
    folder = DraftFolder(root)
    sc = folder.create_draft(name, W, H, fps=fps, allow_replace=True)
    sc.add_track(TrackType.video, "main", relative_index=0)
    sc.add_track(TrackType.video, "bar", relative_index=1)
    sc.add_track(TrackType.video, "players", relative_index=2)
    sc.add_track(TrackType.text, "subs")

    clip_mat = VideoMaterial(ep.clip)
    for kind, start, dur, extra in plan:
        if kind == "clip":
            src = extra
            dur = min(dur, clip_mat.duration - src)
            if dur <= 0:
                continue
            sc.add_segment(VideoSegment(clip_mat, trange(start, dur), source_timerange=trange(src, dur)), "main")
        else:
            mat = VideoMaterial(extra)
            dur = min(dur, mat.duration)
            sc.add_segment(VideoSegment(mat, trange(start, dur), source_timerange=trange(0, dur), volume=0.0), "main")
    for start, dur, k in span_index:
        sc.add_segment(VideoSegment(bar_pngs[k], trange(start, dur)), "bar")
    sc.add_segment(VideoSegment(players_png, trange(0, total_us)), "players")

    style = TextStyle(size=float(ep.cfg["sub_size"]), bold=True, color=(1.0, 1.0, 1.0), align=1,
                      auto_wrapping=True, max_line_width=0.72)
    border = TextBorder(color=(0.0, 0.0, 0.0), width=45.0)
    last_end = 0
    for c in cues:
        s, e = max(us(c.start), last_end), us(c.end)
        if e - s < us(0.1):
            continue
        sc.add_segment(TextSegment(c.text, trange(s, e - s), style=style, border=border,
                                   clip_settings=ClipSettings(transform_y=float(ep.cfg["sub_y"]))), "subs")
        last_end = e

    # sfx: am nao chong gio am truoc thi sang track ke tiep
    mats: Dict[str, AudioMaterial] = {}
    lanes: List[int] = []
    placed = []
    for path, st_s, max_d, vol, loop in sorted(sfx, key=lambda x: x[1]):
        if path not in mats:
            mats[path] = AudioMaterial(path)
        m = mats[path]
        s = us(st_s)
        remain = us(max_d)
        while remain > us(0.05):                         # file ngan hon doan can (tieng go) -> lap lai
            d = min(remain, m.duration, total_us - s)
            if d <= us(0.05):
                break
            lane = next((i for i, end in enumerate(lanes) if end <= s), None)
            if lane is None:
                lanes.append(0)
                lane = len(lanes) - 1
            lanes[lane] = s + d
            placed.append((lane, m, s, d, vol))
            s += d
            remain -= d
            if not loop:                                 # am mot lan (badge, move) khong lap
                break
    for i in range(len(lanes)):
        sc.add_track(TrackType.audio, "sfx_%d" % i)
    for lane, m, s, d, vol in placed:
        sc.add_segment(AudioSegment(m, trange(s, d), source_timerange=trange(0, d), volume=vol), "sfx_%d" % lane)
    sc.save()
    return os.path.join(root, name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Like a Chess Game — sinh draft CapCut")
    ap.add_argument("--episode", required=True)
    ap.add_argument("--preview", type=int, default=0, help="chỉ xem trước thẻ của nước thứ N (1-based)")
    a = ap.parse_args(argv)
    ep = Episode(a.episode)
    if a.preview:
        g, _, _ = ep.load()
        out = os.path.join(ep.render_dir, "preview_M%02d.png" % a.preview)
        log(preview_move(ep, g, a.preview - 1, out))
        return 0
    build(ep)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        import traceback
        traceback.print_exc()
        print("❌ LỖI: %s" % ex, flush=True)
        sys.exit(1)
