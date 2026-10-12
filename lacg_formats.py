# -*- coding: utf-8 -*-
"""
lacg_formats.py — dinh dang du lieu cua Like a Chess Game (dung chung cho app + builder)
==========================================================================================
Hop dong voi Claude Project (claude_project/LACG_command_instructions.md):

  • TRANSCRIPT  (app -> /analyze)      transcript_text()
  • MOVES       (/qcmoves -> app)      parse_moves() + validate_moves() + resolve_anchors()
  • CHAPTERS    (app -> /metadata)     chapters_block()
  • THUMB SPEC  (/thumbnail -> app)    parse_thumb_spec()

transcript.json (do lacg_transcribe.py sinh, app sua trong bang duyet):
  {"version": 1, "source": "clip.mp4", "duration": 312.4,
   "speakers": {"SPEAKER_00": {"label": "GUS", "suggest": "GUS", "score": 0.71,
                               "embedding": [...], "sample": [s, e]}},
   "lines": [{"start": 3.2, "end": 5.6, "speaker": "SPEAKER_00", "label": "",
              "text": "...", "words": [{"w": "You", "s": 3.2, "e": 3.4}, ...]}]}
  line["label"] (neu co) ghi de nhan cua speaker cho rieng dong do.

Khong phu thuoc thu vien ngoai (chi stdlib).
"""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════════════
#  LOAI NUOC DI
# ════════════════════════════════════════════════════════════════════════════
CLASSES = ["brilliant", "great", "best", "excellent", "good", "book",
           "inaccuracy", "mistake", "miss", "blunder"]
POSITIVE = {"brilliant", "great", "best"}
NEGATIVE = {"inaccuracy", "mistake", "miss", "blunder"}
NEEDS_BETTER = NEGATIVE
NO_BETTER = {"brilliant", "great", "best", "book"}

# ky hieu sau ten nuoc tren dong "23 Batman · Rachel??"
SYMBOL = {"brilliant": "!!", "great": "!", "inaccuracy": "?!", "mistake": "?", "blunder": "??"}

# "<Move Name> is ..." — tieu de the
TITLE_PHRASE = {
    "brilliant": "is brilliant", "great": "is a great move", "best": "is best",
    "excellent": "is excellent", "good": "is good", "book": "is a book move",
    "inaccuracy": "is an inaccuracy", "mistake": "is a mistake", "miss": "is a miss",
    "blunder": "is a blunder",
}

# diem Accuracy (STYLE-GUIDE §4)
ACCURACY_WEIGHT = {"brilliant": 100, "great": 100, "best": 100, "book": 100, "excellent": 90,
                   "good": 75, "inaccuracy": 55, "mistake": 30, "miss": 25, "blunder": 0}

RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}
SIDE_NAME = {"W": "White", "B": "Black"}


# ════════════════════════════════════════════════════════════════════════════
#  THOI GIAN
# ════════════════════════════════════════════════════════════════════════════
def fmt_ts(sec: float) -> str:
    """12.34 -> '00:12.3' (dinh dang TRANSCRIPT / anchor)."""
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    s = sec - m * 60
    out = "%02d:%04.1f" % (m, s)
    if out.endswith("60.0"):            # 59.96 lam tron -> 60.0
        out = "%02d:00.0" % (m + 1)
    return out


def fmt_mss(sec: float) -> str:
    """75.4 -> '1:15' (chapters YouTube); >= 1h -> 'h:mm:ss'."""
    sec = int(max(0.0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def parse_time(s: str) -> Optional[float]:
    """'01:23.4' / '1:23' / '1:02:03.5' / '83.4' -> giay. Sai -> None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def fmt_srt_time(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


# ════════════════════════════════════════════════════════════════════════════
#  TRANSCRIPT
# ════════════════════════════════════════════════════════════════════════════
_APOS = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})


def norm_tokens(text: str) -> List[str]:
    """'Don't — you DARE!' -> ['dont', 'you', 'dare'] (so khop anchor, bo dau cau va hoa thuong)."""
    t = (text or "").translate(_APOS).lower()
    t = t.replace("'", "")
    t = re.sub(r"[^\w\s]", " ", t)
    return t.split()


def load_transcript(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_transcript(data: dict, path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def label_of(data: dict, line: dict) -> str:
    """Nhan nhan vat hieu luc cua 1 dong: label rieng > label cua speaker > '?'."""
    lab = (line.get("label") or "").strip().upper()
    if lab:
        return lab
    sp = data.get("speakers", {}).get(line.get("speaker") or "", {})
    return (sp.get("label") or "").strip().upper() or "?"


def sorted_lines(data: dict) -> List[dict]:
    return sorted(data.get("lines", []), key=lambda ln: (ln["start"], ln["end"]))


def line_words(line: dict) -> List[dict]:
    """Danh sach tu co moc gio cua dong. Neu nguoi dung da SUA chu trong bang duyet (tu khong con
    khop words goc) thi chia deu thoi gian dong cho cac tu moi — anchor van khop duoc."""
    text_toks = norm_tokens(line.get("text", ""))
    words = line.get("words") or []
    if words and [t for w in words for t in norm_tokens(w.get("w", ""))] == text_toks:
        out, n = [], len(words)
        # noi suy tu thieu moc gio (so, ky hieu — whisperx hay bo trong)
        for i, w in enumerate(words):
            s, e = w.get("s"), w.get("e")
            if s is None or e is None:
                prev_e = next((words[j].get("e") for j in range(i - 1, -1, -1)
                               if words[j].get("e") is not None), line["start"])
                next_s = next((words[j].get("s") for j in range(i + 1, n)
                               if words[j].get("s") is not None), line["end"])
                s = prev_e if s is None else s
                e = max(s, next_s) if e is None else e
            for t in norm_tokens(w.get("w", "")):
                out.append({"t": t, "s": float(s), "e": float(e)})
        return out
    n = len(text_toks)
    if not n:
        return []
    span = max(0.01, line["end"] - line["start"])
    return [{"t": t, "s": line["start"] + span * i / n, "e": line["start"] + span * (i + 1) / n}
            for i, t in enumerate(text_toks)]


def overlap_flags(data: dict, lines: Optional[List[dict]] = None, tol: float = 0.15) -> List[bool]:
    """True = dong nay NOI CHONG len dong truoc cua NGUOI KHAC (danh '~' trong TRANSCRIPT)."""
    lines = lines if lines is not None else sorted_lines(data)
    flags, max_end, max_lab = [], -1.0, None
    for ln in lines:
        lab = label_of(data, ln)
        flags.append(max_end - ln["start"] > tol and max_lab is not None and max_lab != lab)
        if ln["end"] > max_end:
            max_end, max_lab = ln["end"], lab
    return flags


def transcript_text(data: dict, silence_min: float = 2.0) -> str:
    """Khoi === TRANSCRIPT === dan vao /analyze."""
    lines = sorted_lines(data)
    flags = overlap_flags(data, lines)
    labels = []
    for ln in lines:
        lab = label_of(data, ln)
        if lab != "?" and lab not in labels:
            labels.append(lab)
    dur = float(data.get("duration") or (lines[-1]["end"] if lines else 0.0))
    out = ["=== TRANSCRIPT ===",
           "SOURCE: %s (%s)" % (os.path.basename(data.get("source", "clip.mp4")), fmt_mss(dur)),
           "SPEAKERS: %s" % ", ".join(labels)]
    cursor = 0.0
    for ln, ov in zip(lines, flags):
        if ln["start"] - cursor >= silence_min:
            out.append("[%s] (silence %.1fs)" % (fmt_ts(cursor), ln["start"] - cursor))
        text = re.sub(r"\s+", " ", ln.get("text", "")).strip()
        out.append("[%s]%s %s: %s" % (fmt_ts(ln["start"]), "~" if ov else "", label_of(data, ln), text))
        cursor = max(cursor, ln["end"])
    if dur - cursor >= silence_min:
        out.append("[%s] (silence %.1fs)" % (fmt_ts(cursor), dur - cursor))
    out.append("=== END ===")
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════════════════
#  PHU DE (1 nguoi noi / 1 dong, noi chong -> 2 dong gach dau dong)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Cue:
    start: float
    end: float
    text: str
    label: str
    dashed: bool = False


def _wrap2(text: str, width: int) -> str:
    """Ngat 1 cau thanh toi da 2 hang can doi (hang 1 khong qua width ky tu neu duoc)."""
    if len(text) <= width:
        return text
    words = text.split()
    best, best_cost = text, 10 ** 9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        cost = abs(len(a) - len(b)) + (1000 if max(len(a), len(b)) > width else 0)
        if cost < best_cost:
            best, best_cost = a + "\n" + b, cost
    return best


def _chunks(line: dict, max_chars: int, max_dur: float = 6.0) -> List[Tuple[float, float, str]]:
    """Cat 1 dong dai thanh nhieu cue theo moc gio tung tu (uu tien cat sau dau cau)."""
    raw_words = (line.get("text") or "").split()
    if not raw_words:
        return []
    timed = line_words(line)
    text = " ".join(raw_words)
    if len(text) <= max_chars and line["end"] - line["start"] <= max_dur:
        return [(line["start"], line["end"], text)]
    # gan moc gio cho tung tu hien thi (theo so token)
    times, k = [], 0
    for w in raw_words:
        nt = max(1, len(norm_tokens(w)))
        seg = timed[k:k + nt] if timed else []
        s = seg[0]["s"] if seg else line["start"]
        e = seg[-1]["e"] if seg else line["end"]
        times.append((s, e))
        k += len(norm_tokens(w))
    out, cur, c_start = [], [], None
    for i, w in enumerate(raw_words):
        s, e = times[min(i, len(times) - 1)]
        if c_start is None:
            c_start = s
        cur.append(w)
        joined = " ".join(cur)
        nxt = raw_words[i + 1] if i + 1 < len(raw_words) else None
        punct_break = bool(re.search(r"[.!?,;:]$", w)) and len(joined) >= max_chars * 0.5
        too_long = nxt is not None and (len(joined) + 1 + len(nxt) > max_chars or e - c_start > max_dur)
        if nxt is None or punct_break or too_long:
            out.append((c_start, e, joined))
            cur, c_start = [], None
    # duoi qua ngan (1-2 tu le loi) -> gop vao cue truoc
    if len(out) >= 2 and len(out[-1][2]) < 14 and len(out[-2][2]) + 1 + len(out[-1][2]) <= max_chars * 1.3:
        a, b = out[-2], out.pop()
        out[-1] = (a[0], b[1], a[2] + " " + b[2])
    return out


def build_cues(data: dict, row_chars: int = 42) -> List[Cue]:
    """Dong thoai -> cue phu de. Moi cue chi 1 nguoi noi; 2 nguoi noi chong -> 1 cue 2 dong
    '- A\\n- B'; khong bao gio co 2 cue chong gio nhau."""
    lines = sorted_lines(data)
    flags = overlap_flags(data, lines)
    # dong nao bi/duoc noi chong thi cat ngan (1 hang) de ghep '- A / - B' gon
    tight = set()
    for i, f in enumerate(flags):
        if f:
            tight.add(i)
            if i:
                tight.add(i - 1)
    dash_max = int(row_chars * 1.35)          # 1 hang cua cue '- A / - B' duoc dai hon chut
    cues: List[Cue] = []
    for i, ln in enumerate(lines):
        lab = label_of(data, ln)
        limit = dash_max if i in tight else row_chars * 2
        for s, e, text in _chunks(ln, limit):
            if e - s < 0.2:
                e = s + 0.2
            if cues and s < cues[-1].end - 0.05:
                prev = cues[-1]
                if (not prev.dashed and prev.label != lab and len(prev.text) <= dash_max
                        and len(text) <= dash_max and "\n" not in prev.text):
                    prev.text = "- %s\n- %s" % (prev.text, text)
                    prev.end = max(prev.end, e)
                    prev.dashed = True
                    continue
                if s - prev.start >= 0.4:
                    prev.end = s                      # cat cue truoc cho khit
                else:
                    s = prev.end                      # day cue nay lui
                    if e - s < 0.3:
                        e = s + 0.3
            cues.append(Cue(s, e, text, lab))
    for c in cues:
        if not c.dashed:
            c.text = _wrap2(c.text, row_chars)
    return cues


def write_srt(cues: List[Cue], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (i, fmt_srt_time(c.start), fmt_srt_time(c.end), c.text))


# ════════════════════════════════════════════════════════════════════════════
#  EVAL
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Eval:
    cp: float = 0.0            # diem (goc nhin Trang)
    mate: int = 0              # 0 = khong chieu het; >0 Trang chieu het sau N; <0 Den
    raw: str = "0.0"

    @property
    def score(self) -> float:
        """Gia tri lien tuc de ve thanh eval / tinh do lech: mate = +-10."""
        if self.mate:
            return 10.0 if self.mate > 0 else -10.0
        return max(-10.0, min(10.0, self.cp))

    @property
    def label(self) -> str:
        """Chu tren thanh eval (gia tri tuyet doi, nhu chess.com)."""
        if self.mate:
            return "M%d" % abs(self.mate)
        return "%.1f" % abs(self.cp)


def parse_eval(s: str) -> Optional[Eval]:
    s = (s or "").strip().replace("−", "-").replace("–", "-")
    m = re.fullmatch(r"([+-]?)#?M(\d+)", s, flags=re.IGNORECASE)
    if m:
        n = int(m.group(2))
        return Eval(mate=-n if m.group(1) == "-" else n, raw=s)
    m = re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s)
    if m:
        return Eval(cp=float(s), raw=s)
    return None


# ════════════════════════════════════════════════════════════════════════════
#  MOVES
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Anchor:
    kind: str                  # "quote" | "time"
    label: str = ""
    quote: str = ""
    time: float = 0.0

    def __str__(self):
        return 'at %s "%s"' % (self.label, self.quote) if self.kind == "quote" else "at %s" % fmt_ts(self.time)


@dataclass
class Move:
    side: str                  # "W" | "B"
    mover: str                 # LABEL (in hoa)
    name: str
    cls: str
    eval: Eval
    anchor: Anchor
    body: str = ""
    better: str = ""
    tag: str = ""
    line_no: int = 0
    # dien sau khi resolve_anchors()
    time: Optional[float] = None
    status: str = ""           # "ok" | "warn: ..." | "error: ..."
    badge_pos: str = "auto"    # auto | L | C | R (nguoi dung chinh trong app)

    @property
    def mover_display(self) -> str:
        return title_case_label(self.mover)


@dataclass
class Game:
    series: str = ""
    episode: str = ""
    scene: str = ""
    opening: str = ""
    white_name: str = "White"
    white_members: List[str] = field(default_factory=list)
    black_name: str = "Black"
    black_members: List[str] = field(default_factory=list)
    start_eval: Eval = field(default_factory=lambda: Eval(0.3, 0, "+0.3"))
    result: str = "*"
    moves: List[Move] = field(default_factory=list)

    def side_of(self, label: str) -> Optional[str]:
        if label in self.white_members:
            return "W"
        if label in self.black_members:
            return "B"
        return None


def title_case_label(label: str) -> str:
    """'WALTER JR' -> 'Walter Jr', "O'BRIEN" -> "O'Brien"."""
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), label or "")


def move_number(index: int) -> str:
    """0 -> '1', 1 -> '1...', 2 -> '2' ... (index = thu tu nuoc, tinh tu 0)."""
    n = index // 2 + 1
    return "%d" % n if index % 2 == 0 else "%d..." % n


def move_line(index: int, mv: Move) -> str:
    """'23 Batman · Rachel??' / '22... Joker · Both Addresses!!'."""
    return "%s %s · %s%s" % (move_number(index), mv.mover_display, mv.name, SYMBOL.get(mv.cls, ""))


def card_title(mv: Move) -> str:
    return "%s %s" % (mv.name, TITLE_PHRASE.get(mv.cls, "is " + mv.cls))


def short_ref(index: int, mv: Move) -> str:
    """'23. Rachel??' / '22... Both Addresses!!' (Game Review / chapters)."""
    num = move_number(index)
    return "%s%s %s%s" % (num, "." if not num.endswith("...") else "", mv.name, SYMBOL.get(mv.cls, ""))


_HEADER_KEYS = {"SERIES", "EPISODE", "SCENE", "OPENING", "WHITE", "BLACK", "START EVAL", "RESULT"}
_ANCHOR_Q = re.compile(r'^at\s+(.+?)\s+["“”](.+?)["“”]\s*$', re.IGNORECASE)
_ANCHOR_T = re.compile(r"^at\s+(\d{1,2}(?::\d{1,2}){1,2}(?:\.\d+)?|\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> List[str]:
    return [ln for ln in (text or "").replace("\r\n", "\n").split("\n") if not ln.strip().startswith("```")]


def _split_side(value: str) -> Tuple[str, List[str]]:
    if "=" in value:
        name, members = value.split("=", 1)
    else:
        name, members = value, value
    labels = [m.strip().upper() for m in members.split(",") if m.strip()]
    return name.strip(), labels


def parse_moves(text: str) -> Tuple[Game, List[str]]:
    """Doc khoi MOVES. Tra ve (game, loi cu phap). Chap nhan khoi co/khong co ``` va dong thua
    ngoai === MOVES === ... === END ===."""
    g = Game()
    errors: List[str] = []
    lines = _strip_fences(text)
    # cat dung doan giua === MOVES === va === END === neu co
    start = next((i for i, ln in enumerate(lines) if ln.strip().upper().startswith("=== MOVES")), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if ln_is_end(lines[i])), len(lines))
        lines = lines[start + 1:end]
    cur: Optional[Move] = None
    body: List[str] = []

    def flush():
        nonlocal cur, body
        if cur is not None:
            cur.body = re.sub(r"\s+", " ", " ".join(body)).strip()
            g.moves.append(cur)
        cur, body = None, []

    for no, raw in enumerate(lines, 1):
        ln = raw.strip()
        if not ln:
            continue
        if ln.upper().startswith("MOVE ") or ln.upper() == "MOVE":
            flush()
            parts = [p.strip() for p in ln[4:].split("|")]
            if len(parts) != 6:
                errors.append("Dòng %d: MOVE cần 6 trường cách nhau bởi '|', đang có %d: %s" % (no, len(parts), ln))
                continue
            side, mover, name, cls, ev, anc = parts
            side = side.upper()[:1]
            ev_obj = parse_eval(ev)
            m_q, m_t = _ANCHOR_Q.match(anc), _ANCHOR_T.match(anc)
            if m_q:
                anchor = Anchor("quote", label=m_q.group(1).strip().upper(), quote=m_q.group(2).strip())
            elif m_t:
                anchor = Anchor("time", time=parse_time(m_t.group(1)) or 0.0)
            else:
                anchor = Anchor("time", time=-1.0)
                errors.append("Dòng %d: anchor sai cú pháp: %s" % (no, anc))
            if side not in ("W", "B"):
                errors.append("Dòng %d: bên phải là W hoặc B: %s" % (no, parts[0]))
            if cls.lower() not in CLASSES:
                errors.append("Dòng %d: loại nước không hợp lệ: %s" % (no, cls))
            if ev_obj is None:
                errors.append("Dòng %d: eval sai cú pháp: %s" % (no, ev))
            cur = Move(side=side, mover=mover.upper(), name=name.strip().strip('"'), cls=cls.lower(),
                       eval=ev_obj or Eval(), anchor=anchor, line_no=no)
            continue
        key, sep, val = ln.partition(":")
        key_u = key.strip().upper()
        if sep and cur is not None and key_u in ("BETTER", "TAG"):
            if key_u == "BETTER":
                cur.better = val.strip()
            else:
                cur.tag = val.strip()
            continue
        if sep and cur is None and key_u in _HEADER_KEYS:
            val = val.strip()
            if key_u == "SERIES":
                g.series = val
            elif key_u == "EPISODE":
                g.episode = val
            elif key_u == "SCENE":
                g.scene = val
            elif key_u == "OPENING":
                g.opening = val
            elif key_u == "WHITE":
                g.white_name, g.white_members = _split_side(val)
            elif key_u == "BLACK":
                g.black_name, g.black_members = _split_side(val)
            elif key_u == "START EVAL":
                ev = parse_eval(val)
                if ev is None:
                    errors.append("START EVAL sai cú pháp: %s" % val)
                else:
                    g.start_eval = ev
            elif key_u == "RESULT":
                g.result = val.replace("½", "1/2")
                if g.result not in RESULTS:
                    errors.append("RESULT không hợp lệ: %s" % val)
            continue
        if cur is not None:
            body.append(ln)
    flush()
    if not g.moves:
        errors.append("Không tìm thấy dòng MOVE nào.")
    if not g.white_members or not g.black_members:
        errors.append("Thiếu dòng WHITE hoặc BLACK.")
    return g, errors


def ln_is_end(ln: str) -> bool:
    return ln.strip().upper().startswith("=== END")


def validate_moves(g: Game) -> List[Tuple[int, str]]:
    """Kiem tra luat cua khoi MOVES (giong /qcmoves, phan may lam duoc). -> [(so nuoc 1-based|0, loi)]."""
    out: List[Tuple[int, str]] = []
    prev = g.start_eval
    for i, mv in enumerate(g.moves):
        k = i + 1
        want = "W" if i % 2 == 0 else "B"
        if mv.side != want:
            out.append((k, "Sai lượt: nước %d phải là %s" % (k, want)))
        side = g.side_of(mv.mover)
        if side is None:
            out.append((k, "%s không thuộc WHITE/BLACK" % mv.mover))
        elif side != mv.side:
            out.append((k, "%s thuộc phe %s nhưng đi nước của %s" % (mv.mover, side, mv.side)))
        if mv.cls in NEEDS_BETTER and not mv.better:
            out.append((k, "Thiếu BETTER cho %s" % mv.cls))
        if mv.cls in NO_BETTER and mv.better:
            out.append((k, "Không được có BETTER cho %s" % mv.cls))
        if mv.cls == "brilliant" and not mv.tag:
            out.append((k, "Brilliant phải có TAG"))
        if mv.cls == "book" and i >= 4:
            out.append((k, "Book chỉ dùng trong 4 nước đầu"))
        n_words = len(mv.body.split())
        if not mv.body:
            out.append((k, "Thẻ chưa có nội dung"))
        elif n_words > 40:
            out.append((k, "Thẻ dài %d từ (tối đa ~35)" % n_words))
        sign = 1 if mv.side == "W" else -1
        delta = (mv.eval.score - prev.score) * sign
        if mv.cls in POSITIVE and delta < -0.05:
            out.append((k, "Eval đi ngược: %s mà eval bất lợi cho người đi" % mv.cls))
        if mv.cls in NEGATIVE and delta > 0.05:
            out.append((k, "Eval đi ngược: %s mà eval có lợi cho người đi" % mv.cls))
        prev = mv.eval
    n_bril = sum(1 for m in g.moves if m.cls == "brilliant")
    if n_bril > 2:
        out.append((0, "Có %d brilliant (tối đa 2)" % n_bril))
    for i in range(1, len(g.moves)):
        if g.moves[i].tag and g.moves[i].tag == g.moves[i - 1].tag:
            out.append((i + 1, "TAG lặp lại 2 nước liền"))
    if g.moves:
        fin = g.moves[-1].eval.score
        if g.result == "1-0" and fin < 0 or g.result == "0-1" and fin > 0:
            out.append((len(g.moves), "Eval cuối ngược với RESULT %s" % g.result))
    return out


# ─────────────────────────────────────────────────────────────── anchor
def _find_in_lines(cands, q_toks, after: float):
    """Tim chuoi tu q_toks trong cac dong cands (sau moc 'after'). -> [(end_time, line, score)]."""
    hits = []
    n = len(q_toks)
    q_join = " ".join(q_toks)
    for ln, words in cands:
        toks = [w["t"] for w in words]
        for j in range(0, len(toks) - n + 1):
            end_t = words[j + n - 1]["e"]
            if end_t <= after + 0.01:
                continue
            win = toks[j:j + n]
            if win == q_toks:
                hits.append((end_t, ln, 1.0))
            else:
                r = difflib.SequenceMatcher(None, " ".join(win), q_join).ratio()
                if r >= 0.85:
                    hits.append((end_t, ln, r))
    return hits


def resolve_anchors(g: Game, data: dict, lead_out: float = 0.12) -> None:
    """Gan move.time (giay trong clip, luc DUNG HINH) + move.status cho tung nuoc.
    Quote: tim trong cac dong cua dung LABEL sau anchor truoc; khong co -> tim dong cua nguoi khac
    (bao 'sai người nói'). Thoi diem dung = het tu cuoi cua cau trich + lead_out."""
    lines = sorted_lines(data)
    indexed = [(ln, line_words(ln)) for ln in lines]
    dur = float(data.get("duration") or (lines[-1]["end"] if lines else 0.0))
    prev_t = -1.0
    for mv in g.moves:
        mv.time, mv.status = None, ""
        a = mv.anchor
        if a.kind == "time":
            if a.time < 0:
                mv.status = "error: anchor sai cú pháp"
                continue
            t = a.time
            if dur and t > dur:
                mv.status = "error: quá độ dài clip (%s)" % fmt_ts(dur)
                continue
            mv.time = t
            mv.status = "ok"
        else:
            q = norm_tokens(a.quote)
            if not q:
                mv.status = "error: câu trích rỗng"
                continue
            own = [(ln, w) for ln, w in indexed if label_of(data, ln) == a.label]
            hits = _find_in_lines(own, q, prev_t)
            if hits:
                exact = [h for h in hits if h[2] == 1.0]
                pool = exact or hits
                end_t = min(h[0] for h in pool)
                mv.time = min(end_t + lead_out, dur) if dur else end_t + lead_out
                mv.status = "ok" if len(pool) == 1 and pool[0][2] == 1.0 else (
                    "warn: khớp gần đúng" if len(pool) == 1 else "warn: câu trích xuất hiện %d lần, lấy lần đầu" % len(pool))
            else:
                other = _find_in_lines([(ln, w) for ln, w in indexed if label_of(data, ln) != a.label], q, prev_t)
                if other:
                    end_t = min(h[0] for h in other)
                    who = label_of(data, min(other, key=lambda h: h[0])[1])
                    mv.time = end_t + lead_out
                    mv.status = "error: sai người nói (câu này của %s)" % who
                else:
                    mv.status = "error: không tìm thấy câu trích"
                    continue
        if mv.time is not None and mv.time < prev_t - 0.01:
            mv.status = "error: sớm hơn nước trước (%s)" % fmt_ts(prev_t)
        if mv.time is not None:
            prev_t = max(prev_t, mv.time)


# ════════════════════════════════════════════════════════════════════════════
#  GAME REVIEW
# ════════════════════════════════════════════════════════════════════════════
def review_stats(g: Game) -> dict:
    """Dem so nuoc moi loai + Accuracy moi ben + Turning Point."""
    counts = {s: {c: 0 for c in CLASSES} for s in ("W", "B")}
    acc = {}
    for s in ("W", "B"):
        mine = [m for m in g.moves if m.side == s]
        for m in mine:
            counts[s][m.cls] = counts[s].get(m.cls, 0) + 1
        acc[s] = (sum(ACCURACY_WEIGHT.get(m.cls, 50) for m in mine) / len(mine)) if mine else 0.0
    tp, best = None, 0.0
    prev = g.start_eval.score
    for i, m in enumerate(g.moves):
        d = abs(m.eval.score - prev)
        if d > best + 1e-9:
            tp, best = i, d
        prev = m.eval.score
    return {"counts": counts, "accuracy": acc, "turning_point": tp, "swing": best}


def result_text(result: str) -> str:
    return "Game continues..." if result == "*" else "Result: %s" % result.replace("1/2-1/2", "½-½")


# ════════════════════════════════════════════════════════════════════════════
#  CHAPTERS (app -> /metadata)
# ════════════════════════════════════════════════════════════════════════════
def chapters_block(g: Game, move_times: List[float], review_time: float) -> str:
    out = ["=== CHAPTERS ==="]
    for i, (m, t) in enumerate(zip(g.moves, move_times)):
        out.append("M%d %s %s %s %s %s" % (i + 1, fmt_mss(t), m.side, m.mover_display, m.name, m.cls))
    out.append("REVIEW %s" % fmt_mss(review_time))
    out.append("=== END ===")
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════════════════
#  THUMBNAIL SPEC (/thumbnail -> app)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class ThumbSpec:
    id: str
    title: str
    layout: str                # split | single
    frames: List[int]          # chi so nuoc (1-based)
    badges: List[str]          # loai nuoc
    text: str = ""


def parse_thumb_spec(text: str) -> Tuple[List[ThumbSpec], List[str]]:
    specs, errors = [], []
    cur_id, cur_title = None, ""
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        ln = raw.rstrip()
        s = ln.strip()
        if not s or s.startswith("```") or s.upper().startswith("GROUP "):
            continue
        m = re.match(r"^([ABC][1-9])\b(.*)$", s)
        if m and "\t" not in s:
            cur_id = m.group(1)
            mt = re.search(r"for:\s*(.+?)(?:\s+[—–]\s+.*)?$", m.group(2))
            cur_title = mt.group(1).strip() if mt else ""
            continue
        if cur_id is None:
            continue
        cols = s.split("\t") if "\t" in s else re.split(r"\s{2,}", s)
        cols = [c.strip() for c in cols]
        if len(cols) < 3:
            continue
        layout = cols[0].lower()
        frames = [int(x) for x in re.findall(r"M(\d+)", cols[1], flags=re.IGNORECASE)]
        badges = [b.strip().lower() for b in re.split(r"[|,]", cols[2]) if b.strip()]
        txt = cols[3] if len(cols) > 3 else ""
        txt = "" if txt in ("-", "—", "none") else txt.upper()
        bad = [b for b in badges if b not in CLASSES]
        if layout not in ("split", "single"):
            errors.append("%s: layout phải là split/single (%s)" % (cur_id, cols[0]))
        elif not frames:
            errors.append("%s: thiếu khung hình M<k>" % cur_id)
        elif bad:
            errors.append("%s: badge không hợp lệ %s" % (cur_id, ", ".join(bad)))
        else:
            specs.append(ThumbSpec(cur_id, cur_title, layout, frames, badges, txt))
        cur_id = None
    if not specs and not errors:
        errors.append("Không đọc được spec thumbnail nào.")
    return specs, errors
