"""Sottotitoli: segmentazione leggibile, esportazione SRT/VTT, validazione."""
from __future__ import annotations

import re

from .project import SubtitleCue

MAX_LINE = 42
MAX_LINES = 2
MIN_DUR = 0.9
MAX_CPS = 20.0


def _fmt(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def wrap(text: str, max_line: int = MAX_LINE) -> str:
    words = text.split()
    if len(text) <= max_line or len(words) < 2:
        return text
    # divisione bilanciata in due righe
    best, best_cost = text, 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        cost = abs(len(a) - len(b)) + (1000 if max(len(a), len(b)) > max_line else 0)
        if cost < best_cost:
            best, best_cost = a + "\n" + b, cost
    return best


def from_words(words: list[tuple[float, float, str]], max_chars: int = MAX_LINE * MAX_LINES,
               max_dur: float = 6.0) -> list[SubtitleCue]:
    """Raggruppa parole con timestamp (start, end, parola) in cue leggibili,
    spezzando su punteggiatura, pause, lunghezza e durata."""
    cues: list[SubtitleCue] = []
    cur: list[tuple[float, float, str]] = []

    def flush():
        if cur:
            text = " ".join(w for _, _, w in cur).strip()
            text = re.sub(r"\s+([,.;:!?])", r"\1", text)
            cues.append(SubtitleCue(start=cur[0][0], end=cur[-1][1], text=wrap(text)))
            cur.clear()

    for i, (s, e, w) in enumerate(words):
        w = w.strip()
        if not w:
            continue
        if cur:
            text_len = sum(len(x) + 1 for _, _, x in cur) + len(w)
            gap = s - cur[-1][1]
            if text_len > max_chars or (e - cur[0][0]) > max_dur or gap > 0.7:
                flush()
        cur.append((s, e, w))
        if re.search(r"[.!?]$", w) or (re.search(r"[,;:]$", w) and sum(len(x) for _, _, x in cur) > 30):
            flush()
    flush()
    return normalize(cues)


def from_sentences(segments: list[tuple[float, float, str]]) -> list[SubtitleCue]:
    """Cue da frasi temporizzate (es. segmenti TTS), con suddivisione proporzionale
    al numero di caratteri quando una frase è troppo lunga."""
    words: list[tuple[float, float, str]] = []
    for s, e, text in segments:
        toks = text.split()
        if not toks:
            continue
        total = sum(len(t) + 1 for t in toks)
        t = s
        for tok in toks:
            d = (e - s) * (len(tok) + 1) / total
            words.append((t, t + d, tok))
            t += d
    return from_words(words)


def normalize(cues: list[SubtitleCue], total: float | None = None) -> list[SubtitleCue]:
    out: list[SubtitleCue] = []
    for c in sorted(cues, key=lambda c: c.start):
        start, end = c.start, max(c.end, c.start + MIN_DUR)
        if out and start < out[-1].end:
            out[-1].end = max(out[-1].start + 0.3, start - 0.04)
        if total is not None:
            if start >= total:
                continue
            end = min(end, total)
        out.append(SubtitleCue(start=round(start, 3), end=round(end, 3), text=c.text))
    return out


def to_srt(cues: list[SubtitleCue]) -> str:
    parts = []
    for i, c in enumerate(cues, 1):
        parts.append(f"{i}\n{_fmt(c.start, ',')} --> {_fmt(c.end, ',')}\n{c.text}\n")
    return "\n".join(parts)


def to_vtt(cues: list[SubtitleCue]) -> str:
    parts = ["WEBVTT\n"]
    for c in cues:
        parts.append(f"{_fmt(c.start, '.')} --> {_fmt(c.end, '.')}\n{c.text}\n")
    return "\n".join(parts)


def validate(cues: list[SubtitleCue], total: float) -> list[str]:
    problems = []
    prev_end = -1.0
    for i, c in enumerate(cues, 1):
        if c.end <= c.start:
            problems.append(f"cue {i}: durata non positiva")
        if c.start < prev_end - 1e-3:
            problems.append(f"cue {i}: sovrapposta alla precedente")
        if c.end > total + 0.05:
            problems.append(f"cue {i}: oltre la fine del video")
        lines = c.text.split("\n")
        if len(lines) > MAX_LINES or any(len(line) > MAX_LINE + 6 for line in lines):
            problems.append(f"cue {i}: righe troppo lunghe")
        cps = len(c.text.replace("\n", "")) / max(0.01, c.end - c.start)
        if cps > MAX_CPS + 5:
            problems.append(f"cue {i}: velocità di lettura alta ({cps:.0f} car/s)")
        prev_end = c.end
    return problems
