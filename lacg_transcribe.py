# -*- coding: utf-8 -*-
"""
lacg_transcribe.py — NHAN THOAI cho Like a Chess Game (chay rieng, GPU NVIDIA)
==============================================================================
clip.mp4  ->  transcript.json  (moi dong thoai = 1 NGUOI NOI, co moc gio tung tu)

  1. ffmpeg tach audio
  2. Demucs (htdemucs, two-stems) tach GIONG NOI khoi nhac nen / tieng dong
  3. WhisperX large-v3 nhan loi  (hoac: lay CHU tu phu de chuan .srt, tu do do lech gio)
  4. WhisperX align -> moc gio TUNG TU
  5. pyannote (qua WhisperX) phan biet NGUOI NOI -> gan nguoi noi cho tung tu
  6. Cat dong theo nguoi noi (khong bao gio gop loi 2 nguoi vao 1 dong), danh dau doan NOI CHONG
  7. (thu nghiem) Doan noi chong: tach giong bang pyannote speech-separation, nhan loi tung giong
  8. Van tay giong (wespeaker) -> so voi kho giong da luu -> goi y ten nhan vat

Chay:  python lacg_transcribe.py --clip clip.mp4 --out transcript.json --hf-token hf_xxx
App chess_studio.pyw goi script nay va doc log tu stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

SR = 16000


def log(msg: str) -> None:
    print(msg, flush=True)


def step(i: int, n: int, msg: str) -> None:
    log("[%d/%d] %s" % (i, n, msg))


def run(cmd: List[str]) -> None:
    flags = 0x08000000 if os.name == "nt" else 0
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace", creationflags=flags)
    if p.returncode != 0:
        raise RuntimeError("Lệnh lỗi: %s\n%s" % (" ".join(cmd[:4]), p.stdout[-2000:]))


def ffprobe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", path], stdout=subprocess.PIPE, text=True).stdout.strip()
    return float(out or 0)


# ════════════════════════════════════════════════════════════════════════════
#  PHU DE CHUAN (.srt)
# ════════════════════════════════════════════════════════════════════════════
def parse_srt(path: str) -> List[dict]:
    raw = open(path, encoding="utf-8-sig", errors="replace").read().replace("\r\n", "\n")
    cues = []
    for blk in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in blk.strip().split("\n") if ln.strip()]
        k = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if k is None:
            continue
        a, b = [x.strip() for x in lines[k].split("-->")[:2]]
        b = b.split()[0]

        def sec(t):
            h, m, s = t.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        try:
            s0, s1 = sec(a), sec(b)
        except ValueError:
            continue
        body = [re.sub(r"<[^>]+>|\{[^}]*\}", "", ln).strip() for ln in lines[k + 1:]]
        body = [ln for ln in body if ln]
        if not body:
            continue
        # cue 2 nguoi "- A / - B" -> tach thanh 2 doan rieng
        if len(body) >= 2 and all(ln.startswith("-") for ln in body):
            for ln in body:
                cues.append({"start": s0, "end": s1, "text": ln.lstrip("-– ").strip(), "dash": True})
        else:
            text = " ".join(body)
            text = re.sub(r"^\s*-\s*", "", text)
            text = re.sub(r"\[[^\]]*\]|\([A-Z ]+\)", "", text).strip()     # [door slams] (MAN)
            if text:
                cues.append({"start": s0, "end": s1, "text": text, "dash": False})
    return cues


def _toks(s: str) -> List[str]:
    s = (s or "").lower().replace("’", "'").replace("'", "")
    return re.sub(r"[^\w\s]", " ", s).split()


def srt_offset(asr_segments: List[dict], cues: List[dict]) -> Optional[float]:
    """Do lech gio giua phu de (theo tap phim) va clip: so khop cau ASR <-> cue, lay trung vi."""
    import difflib
    cue_txt = [" ".join(_toks(c["text"])) for c in cues]
    cands = []
    for seg in asr_segments:
        t = " ".join(_toks(seg.get("text", "")))
        if len(t.split()) < 3:
            continue
        best, bi = 0.0, -1
        for i, ct in enumerate(cue_txt):
            if not ct:
                continue
            r = difflib.SequenceMatcher(None, t, ct).ratio()
            if r > best:
                best, bi = r, i
        if best >= 0.6:
            cands.append(seg["start"] - cues[bi]["start"])
    if not cands:
        return None
    # cum nhieu nhat (lam tron 0.5s, gop 2 cum ke ben) roi lay trung vi
    bins: Dict[float, List[float]] = {}
    for c in cands:
        bins.setdefault(round(c * 2) / 2, []).append(c)
    key = max(bins, key=lambda k: len(bins[k]))
    top = sorted(bins[key] + bins.get(key - 0.5, []) + bins.get(key + 0.5, []))
    return top[len(top) // 2]


# ════════════════════════════════════════════════════════════════════════════
#  CAT DONG THEO NGUOI NOI
# ════════════════════════════════════════════════════════════════════════════
def flat_words(segments: List[dict], seg_index: bool = False) -> List[dict]:
    out = []
    for si, seg in enumerate(segments):
        for w in seg.get("words", []) or []:
            word = (w.get("word") or "").strip()
            if not word:
                continue
            out.append({"w": word, "s": w.get("start"), "e": w.get("end"),
                        "sp": w.get("speaker"), "seg": si})
    return out


def smooth_speakers(words: List[dict]) -> None:
    """Sua nhieu ranh gioi diarization: 1-2 tu ngan bi gan nguoi khac giua 2 khuc cung 1 nguoi."""
    n = len(words)
    # tu thieu nguoi noi -> lay nguoi gan nhat
    last = None
    for w in words:
        if w["sp"] is None:
            w["sp"] = last
        else:
            last = w["sp"]
    nxt = None
    for w in reversed(words):
        if w["sp"] is None:
            w["sp"] = nxt
        else:
            nxt = w["sp"]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and words[j + 1]["sp"] == words[i]["sp"]:
            j += 1
        run_len = j - i + 1
        turn_break = bool(re.search(r"[.!?]$", words[i - 1]["w"])) if i > 0 else True
        if (0 < i and j < n - 1 and run_len <= 2 and not turn_break
                and words[i - 1]["sp"] == words[j + 1]["sp"]):
            s = words[i]["s"] if words[i]["s"] is not None else 0
            e = words[j]["e"] if words[j]["e"] is not None else s
            if e - s < 0.6:
                for k in range(i, j + 1):
                    words[k]["sp"] = words[i - 1]["sp"]
        i = j + 1


def build_lines(words: List[dict], by_segment: bool, gap: float = 1.2, max_len: float = 12.0) -> List[dict]:
    """Gom tu thanh dong: dong moi khi doi nguoi noi / ngung lau / qua dai (tai dau cau).
    by_segment=True (che do phu de chuan): moi doan phu de = 1 dong, nguoi noi = da so."""
    lines: List[dict] = []
    if by_segment:
        groups: Dict[int, List[dict]] = {}
        for w in words:
            groups.setdefault(w["seg"], []).append(w)
        for _, ws in sorted(groups.items()):
            votes: Dict[str, float] = {}
            for w in ws:
                if w["sp"]:
                    dur = (w["e"] or 0) - (w["s"] or 0)
                    votes[w["sp"]] = votes.get(w["sp"], 0) + max(0.05, dur)
            sp = max(votes, key=votes.get) if votes else None
            lines.append(_mk_line(ws, sp))
        return [ln for ln in lines if ln]
    cur: List[dict] = []
    for w in words:
        if cur:
            prev = cur[-1]
            pe = prev["e"] if prev["e"] is not None else prev["s"]
            ws_ = w["s"] if w["s"] is not None else pe
            c_s = next((x["s"] for x in cur if x["s"] is not None), ws_)
            sentence_end = bool(re.search(r"[.!?]$", prev["w"]))
            if (w["sp"] != prev["sp"] or (pe is not None and ws_ is not None and ws_ - pe > gap)
                    or (sentence_end and ws_ is not None and c_s is not None and ws_ - c_s > max_len)):
                lines.append(_mk_line(cur, prev["sp"]))
                cur = []
        cur.append(w)
    if cur:
        lines.append(_mk_line(cur, cur[-1]["sp"]))
    return [ln for ln in lines if ln]


def _mk_line(ws: List[dict], sp: Optional[str]) -> Optional[dict]:
    starts = [w["s"] for w in ws if w["s"] is not None]
    ends = [w["e"] for w in ws if w["e"] is not None]
    if not starts or not ends:
        return None
    text = " ".join(w["w"] for w in ws)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return {"start": round(min(starts), 3), "end": round(max(ends), 3), "speaker": sp or "SPEAKER_??",
            "label": "", "text": text,
            "words": [{"w": w["w"], "s": None if w["s"] is None else round(w["s"], 3),
                       "e": None if w["e"] is None else round(w["e"], 3)} for w in ws]}


def turns_from_diar(diar) -> List[Tuple[float, float, str]]:
    """DataFrame cua WhisperX (cot start, end, speaker) -> [(s, e, speaker)]."""
    out = []
    try:
        for _, r in diar.iterrows():
            out.append((float(r["start"]), float(r["end"]), str(r["speaker"])))
    except Exception:
        pass
    return sorted(out)


def mark_overlaps(lines: List[dict], turns, min_ov: float = 0.3) -> None:
    for ln in lines:
        others = set()
        for s, e, sp in turns:
            if sp == ln["speaker"]:
                continue
            if min(e, ln["end"]) - max(s, ln["start"]) >= min_ov:
                others.add(sp)
        ln["overlap"] = bool(others)
        if others:
            ln["overlap_with"] = sorted(others)


def overlap_regions(turns, min_len: float = 0.5) -> List[Tuple[float, float, List[str]]]:
    """Doan co >= 2 nguoi cung noi."""
    ev = []
    for s, e, sp in turns:
        ev.append((s, 1, sp))
        ev.append((e, -1, sp))
    ev.sort(key=lambda x: (x[0], x[1]))
    active: Dict[str, int] = {}
    out, start, who = [], None, set()
    for t, d, sp in ev:
        before = sum(1 for v in active.values() if v > 0)
        active[sp] = active.get(sp, 0) + d
        now = [k for k, v in active.items() if v > 0]
        if before < 2 <= len(now):
            start, who = t, set(now)
        elif before >= 2 and len(now) >= 2:
            who |= set(now)
        elif before >= 2 > len(now):
            if start is not None and t - start >= min_len:
                out.append((start, t, sorted(who)))
            start, who = None, set()
    return out


def _from_pretrained(cls, name: str, token: str):
    """pyannote 4 dung token=, pyannote 3 dung use_auth_token=."""
    try:
        obj = cls.from_pretrained(name, token=token)
    except TypeError:
        obj = cls.from_pretrained(name, use_auth_token=token)
    if obj is None:
        raise RuntimeError("Không tải được %s — vào huggingface.co/%s bấm Agree, kiểm tra token." % (name, name))
    return obj


def windows_cuda_dll_fix() -> None:
    """Windows: CTranslate2 (faster-whisper) can cuDNN 9 — dung luon ban di kem torch (torch/lib)."""
    if os.name != "nt":
        return
    try:
        import torch
        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib):
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
            os.add_dll_directory(lib)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
#  VAN TAY GIONG (kho giong nhan vat)
# ════════════════════════════════════════════════════════════════════════════
def speaker_embeddings(audio, turns, token: str, device: str) -> Dict[str, dict]:
    """Moi speaker: lay toi da ~30s doan noi RIENG (khong chong) dai nhat -> trung binh embedding."""
    import numpy as np
    import torch
    from pyannote.audio import Inference, Model
    from pyannote.core import Segment
    model = _from_pretrained(Model, "pyannote/wespeaker-voxceleb-resnet34-LM", token)
    inf = Inference(model, window="whole")
    inf.to(torch.device(device))
    wav = {"waveform": torch.from_numpy(np.asarray(audio, dtype="float32")).unsqueeze(0), "sample_rate": SR}
    total = len(audio) / SR
    out = {}
    speakers = sorted({sp for _, _, sp in turns})
    for sp in speakers:
        clean = []
        for s, e, who in turns:
            if who != sp:
                continue
            # bo phan bi nguoi khac noi chong
            segs = [(s, e)]
            for s2, e2, w2 in turns:
                if w2 == sp:
                    continue
                nxt = []
                for a, b in segs:
                    if e2 <= a or s2 >= b:
                        nxt.append((a, b))
                    else:
                        if s2 > a:
                            nxt.append((a, s2))
                        if e2 < b:
                            nxt.append((e2, b))
                segs = nxt
            clean += [(a, b) for a, b in segs if b - a >= 1.0]
        clean.sort(key=lambda x: -(x[1] - x[0]))
        picked, acc = [], 0.0
        for a, b in clean:
            if acc >= 30:
                break
            picked.append((a, min(b, a + 10.0)))
            acc += min(b, a + 10.0) - a
        if not picked:
            continue
        embs = []
        for a, b in picked:
            try:
                e = inf.crop(wav, Segment(max(0.0, a), min(total, b)))
                embs.append(np.asarray(e, dtype="float32").reshape(-1))
            except Exception as ex:                       # doan qua ngan / loi le
                log("   (bỏ 1 đoạn vân tay %s: %s)" % (sp, ex))
        if embs:
            m = np.mean(np.stack(embs), axis=0)
            m = m / (np.linalg.norm(m) + 1e-9)
            out[sp] = {"embedding": [round(float(x), 5) for x in m], "sample": [round(picked[0][0], 2),
                                                                             round(picked[0][1], 2)]}
    return out


def match_voice_bank(embs: Dict[str, dict], voices_dir: str) -> None:
    """Gan goi y ten (suggest/score) cho tung speaker theo kho giong."""
    import numpy as np
    bank = {}
    if voices_dir and os.path.isdir(voices_dir):
        for fn in os.listdir(voices_dir):
            if fn.lower().endswith(".json"):
                try:
                    d = json.load(open(os.path.join(voices_dir, fn), encoding="utf-8"))
                    vs = np.asarray(d.get("embeddings") or [], dtype="float32")
                    if len(vs):
                        m = vs.mean(axis=0)
                        bank[d.get("label") or fn[:-5]] = m / (np.linalg.norm(m) + 1e-9)
                except Exception:
                    continue
    used = set()
    pairs = []
    for sp, d in embs.items():
        v = np.asarray(d["embedding"], dtype="float32")
        for lab, m in bank.items():
            pairs.append((float(v @ m), sp, lab))
    # gan tham lam theo diem cao nhat, moi ten 1 speaker
    for score, sp, lab in sorted(pairs, reverse=True):
        if "suggest" in embs[sp] or lab in used:
            continue
        if score >= 0.45:
            embs[sp]["suggest"] = lab
            embs[sp]["score"] = round(score, 3)
            used.add(lab)


# ════════════════════════════════════════════════════════════════════════════
#  TACH GIONG DOAN NOI CHONG (thu nghiem)
# ════════════════════════════════════════════════════════════════════════════
def recover_overlaps(vocals_path: str, audio, regions, lines: List[dict], turns, asr_model, align_model,
                     align_meta, token: str, device: str, lang: str) -> int:
    """Voi doan 2 nguoi noi chong ma 1 nguoi chua co chu nao: tach giong (pyannote
    speech-separation-ami-1.0), nhan loi rieng giong do, them thanh dong moi. Tra ve so dong them."""
    import numpy as np
    import torch
    import whisperx
    from pyannote.audio import Pipeline
    pipe = _from_pretrained(Pipeline, "pyannote/speech-separation-ami-1.0", token)
    pipe.to(torch.device(device))
    wav = {"waveform": torch.from_numpy(np.asarray(audio, dtype="float32")).unsqueeze(0), "sample_rate": SR}
    diar2, sources = pipe(wav)
    labels2 = list(diar2.labels())
    data = np.asarray(sources.data)                      # (samples, n_sources)
    # nhan cua pipeline tach giong -> speaker cua diarization chinh (theo thoi gian trung nhau)
    turns2 = [(seg.start, seg.end, lab) for seg, _, lab in diar2.itertracks(yield_label=True)]
    mapping = {}
    for lab in labels2:
        score: Dict[str, float] = {}
        for s, e, l2 in turns2:
            if l2 != lab:
                continue
            for s1, e1, sp in turns:
                ov = min(e, e1) - max(s, s1)
                if ov > 0:
                    score[sp] = score.get(sp, 0) + ov
        if score:
            mapping[lab] = max(score, key=score.get)
    added = 0
    for (rs, re_, who) in regions:
        for sp in who:
            has_words = any(ln["speaker"] == sp and min(ln["end"], re_) - max(ln["start"], rs) > 0.2 for ln in lines)
            if has_words:
                continue
            src = next((labels2.index(l) for l, m in mapping.items() if m == sp and labels2.index(l) < data.shape[1]), None)
            if src is None:
                continue
            a, b = max(0.0, rs - 0.3), re_ + 0.3
            chunk = data[int(a * SR):int(b * SR), src].astype("float32")
            if len(chunk) < SR * 0.4:
                continue
            res = asr_model.transcribe(chunk, batch_size=4, language=lang)
            if not res.get("segments"):
                continue
            al = whisperx.align(res["segments"], align_model, align_meta, chunk, device, return_char_alignments=False)
            ws = []
            for w in flat_words(al.get("segments", [])):
                ws.append({**w, "s": None if w["s"] is None else w["s"] + a,
                           "e": None if w["e"] is None else w["e"] + a, "sp": sp})
            ln = _mk_line(ws, sp) if ws else None
            if ln:
                ln["overlap"] = True
                ln["separated"] = True
                lines.append(ln)
                added += 1
    lines.sort(key=lambda x: (x["start"], x["end"]))
    return added


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Like a Chess Game — nhận thoại có tên người nói")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--srt", default="", help="phụ đề chuẩn (.srt) — lấy CHỮ từ đây, máy chỉ xác định ai nói")
    ap.add_argument("--hf-token", default=os.environ.get("HF_TOKEN", ""))
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default="en")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute", default="float16")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--no-demucs", action="store_true")
    ap.add_argument("--min-speakers", type=int, default=0)
    ap.add_argument("--max-speakers", type=int, default=0)
    ap.add_argument("--separate-overlaps", action="store_true")
    ap.add_argument("--voices-dir", default="")
    a = ap.parse_args(argv)

    t0 = time.time()
    N = 8
    clip = os.path.abspath(a.clip)
    out = os.path.abspath(a.out)
    work = os.path.join(os.path.dirname(out), "_work")
    os.makedirs(work, exist_ok=True)

    import torch
    windows_cuda_dll_fix()
    device = a.device
    if device == "cuda" and not torch.cuda.is_available():
        log("⚠️  Không thấy GPU CUDA -> chạy CPU (chậm hơn nhiều). Kiểm tra cài torch bản CUDA.")
        device, a.compute = "cpu", "int8"
    if device == "cpu" and a.compute == "float16":
        a.compute = "int8"
    import whisperx

    step(1, N, "Tách audio từ clip")
    dur = ffprobe_duration(clip)
    wav = os.path.join(work, "audio.wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip, "-vn", "-ac", "2", "-ar", "44100", wav])

    vocals = wav
    if not a.no_demucs:
        step(2, N, "Demucs: tách giọng nói khỏi nhạc nền (htdemucs)")
        dem_out = os.path.join(work, "demucs")
        shutil.rmtree(dem_out, ignore_errors=True)
        run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
             "-d", device, "-o", dem_out, wav])
        cand = os.path.join(dem_out, "htdemucs", "audio", "vocals.wav")
        if os.path.isfile(cand):
            vocals = cand
        else:
            log("⚠️  Không thấy vocals.wav của Demucs -> dùng audio gốc.")
    else:
        step(2, N, "Bỏ qua Demucs")
    audio = whisperx.load_audio(vocals)

    step(3, N, "WhisperX %s: nhận lời (%s, %s)" % (a.model, device, a.compute))
    asr = whisperx.load_model(a.model, device, compute_type=a.compute, language=a.language)
    result = asr.transcribe(audio, batch_size=a.batch, language=a.language)
    segments = result.get("segments", [])
    by_segment = False
    if a.srt:
        cues = parse_srt(a.srt)
        off = srt_offset(segments, cues)
        if off is None:
            log("⚠️  Không khớp được phụ đề chuẩn với clip -> dùng lời máy nhận.")
        else:
            log("   Phụ đề chuẩn lệch %+.2fs so với clip" % off)
            segs = []
            for c in cues:
                s, e = c["start"] + off, c["end"] + off
                if e < 0 or s > dur:
                    continue
                segs.append({"text": c["text"], "start": max(0.0, s), "end": min(dur, e)})
            if segs:
                segments, by_segment = segs, True
            else:
                log("⚠️  Phụ đề chuẩn không có câu nào trong khoảng clip -> dùng lời máy nhận.")

    step(4, N, "Căn mốc giờ từng từ")
    align_model, meta = whisperx.load_align_model(language_code=a.language, device=device)
    aligned = whisperx.align(segments, align_model, meta, audio, device, return_char_alignments=False)

    turns = []
    if a.hf_token:
        step(5, N, "pyannote: phân biệt người nói")
        try:
            from whisperx.diarize import DiarizationPipeline
        except ImportError:
            DiarizationPipeline = whisperx.DiarizationPipeline
        try:
            dp = DiarizationPipeline(token=a.hf_token, device=device)            # whisperx >= 3.4 / pyannote 4
        except TypeError:
            dp = DiarizationPipeline(use_auth_token=a.hf_token, device=device)   # ban cu
        kw = {}
        if a.min_speakers:
            kw["min_speakers"] = a.min_speakers
        if a.max_speakers:
            kw["max_speakers"] = a.max_speakers
        diar = dp(audio, **kw)
        if isinstance(diar, tuple):                      # ban moi tra (diar, embeddings)
            diar = diar[0]
        aligned = whisperx.assign_word_speakers(diar, aligned)
        turns = turns_from_diar(diar)
    else:
        step(5, N, "⚠️  Không có HuggingFace token -> bỏ qua phân biệt người nói (mọi dòng = 1 người)")

    step(6, N, "Cắt dòng theo người nói")
    words = flat_words(aligned.get("segments", []))
    if not turns:
        for w in words:
            w["sp"] = "SPEAKER_00"
    smooth_speakers(words)
    lines = build_lines(words, by_segment)
    mark_overlaps(lines, turns)
    regions = overlap_regions(turns)
    log("   %d dòng, %d người nói, %d đoạn nói chồng" % (len(lines), len({ln["speaker"] for ln in lines}), len(regions)))

    if a.separate_overlaps and regions and a.hf_token:
        step(7, N, "Tách giọng đoạn nói chồng (thử nghiệm)")
        try:
            n_add = recover_overlaps(vocals, audio, regions, lines, turns, asr, align_model, meta,
                                     a.hf_token, device, a.language)
            log("   thêm %d dòng từ giọng đã tách" % n_add)
        except Exception as ex:
            log("⚠️  Tách giọng lỗi, bỏ qua: %s" % ex)
    else:
        step(7, N, "Bỏ qua tách giọng nói chồng")

    speakers: Dict[str, dict] = {}
    for ln in lines:
        speakers.setdefault(ln["speaker"], {"label": "", "suggest": "", "score": 0.0})
    if turns and a.hf_token:
        step(8, N, "Vân tay giọng + so với kho giọng nhân vật")
        try:
            embs = speaker_embeddings(audio, turns, a.hf_token, device)
            match_voice_bank(embs, a.voices_dir)
            for sp, d in embs.items():
                info = speakers.setdefault(sp, {"label": "", "suggest": "", "score": 0.0})
                info.update(d)
                if d.get("suggest") and d.get("score", 0) >= 0.6:
                    info["label"] = d["suggest"]
                log("   %s -> %s" % (sp, ("%s (%.2f)" % (d["suggest"], d["score"])) if d.get("suggest") else "giọng mới"))
        except Exception as ex:
            log("⚠️  Không tính được vân tay giọng: %s" % ex)
    else:
        step(8, N, "Bỏ qua vân tay giọng")
    for sp, info in speakers.items():
        if "sample" not in info:
            mine = [ln for ln in lines if ln["speaker"] == sp and not ln.get("overlap")] or \
                   [ln for ln in lines if ln["speaker"] == sp]
            if mine:
                best = max(mine, key=lambda ln: ln["end"] - ln["start"])
                info["sample"] = [best["start"], min(best["end"], best["start"] + 8)]

    data = {"version": 1, "source": clip, "duration": round(dur, 3), "language": a.language,
            "official_srt": bool(by_segment), "speakers": speakers, "lines": lines,
            "overlap_regions": [[round(s, 2), round(e, 2), w] for s, e, w in regions]}
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out)
    log("✅ Xong trong %.0fs -> %s" % (time.time() - t0, out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        import traceback
        traceback.print_exc()
        log("❌ LỖI: %s" % ex)
        sys.exit(1)
