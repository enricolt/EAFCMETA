"""Sottotitoli -> testo. Solo libreria standard, nessuna rete.

Le trascrizioni automatiche di YouTube non hanno punteggiatura: il testo esce a RIGHE di ~N parole (un "a capo" ogni
riga), perché l'estrazione a parole chiave ragiona per frasi e un parere va attribuito alla carta nominata poco prima,
non a tutto il video. Se il video ha capitoli, ogni capitolo è una sezione a sé.
"""
import html
import json
import re

Seg = tuple[float, str]  # (secondo di inizio, testo)

_TAG = re.compile(r"<[^>]*>")
_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_CUE = re.compile(r"^(\S+)\s+-->\s+\S+")


def _clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", s))).strip()


def parse_json3(raw: str) -> list[Seg]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[Seg] = []
    for ev in (data.get("events") if isinstance(data, dict) else None) or []:
        if not isinstance(ev, dict) or not isinstance(ev.get("segs"), list):
            continue
        text = _clean_line("".join(str(s.get("utf8", "")) for s in ev["segs"] if isinstance(s, dict)).replace("\n", " "))
        if text:
            out.append(((ev.get("tStartMs") or 0) / 1000.0, text))
    return out


def _vtt_seconds(ts: str) -> float:
    m = _TS.match(ts)
    if not m:
        return 0.0
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_vtt(raw: str) -> list[Seg]:
    """WebVTT. Le trascrizioni automatiche ripetono le righe (effetto 'a scorrimento'): le righe uguali alla precedente
    si scartano."""
    out: list[Seg] = []
    start, last = 0.0, ""
    for line in (raw or "").splitlines():
        m = _CUE.match(line.strip())
        if m:
            start = _vtt_seconds(m.group(1))
            continue
        s = line.strip()
        if not s or s.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE")) or s.isdigit():
            continue
        text = _clean_line(s)
        if text and text != last:
            out.append((start, text))
            last = text
    return out


def parse(raw: str, ext: str) -> list[Seg]:
    return parse_json3(raw) if ext.lower().lstrip(".") == "json3" else parse_vtt(raw)


def to_lines(segs: list[Seg], words_per_line: int = 14) -> list[Seg]:
    """Riunisce i frammenti in righe di circa `words_per_line` parole, tenendo il secondo d'inizio della riga."""
    lines: list[Seg] = []
    buf: list[str] = []
    t0 = 0.0
    for t, text in segs:
        if not buf:
            t0 = t
        buf.extend(text.split())
        if len(buf) >= words_per_line:
            lines.append((t0, " ".join(buf)))
            buf = []
    if buf:
        lines.append((t0, " ".join(buf)))
    return lines


def sections(segs: list[Seg], chapters: list[tuple[float, str]] | None = None, words_per_line: int = 14,
             max_chars: int = 30000) -> list[str]:
    """Testo della trascrizione diviso in sezioni: per capitolo se ci sono, altrimenti a blocchi di `max_chars`.
    Ogni sezione è formata da righe separate da a capo (e, per i capitoli, dal titolo come prima riga)."""
    lines = to_lines(segs, words_per_line)
    if not lines:
        return []
    chapters = sorted((c for c in (chapters or []) if isinstance(c[0], (int, float))), key=lambda c: c[0])
    groups: list[tuple[str, list[str]]] = []
    if chapters:
        bounds = [c[0] for c in chapters]
        buckets: list[list[str]] = [[] for _ in chapters]
        pre: list[str] = []
        for t, text in lines:
            idx = max((i for i, b in enumerate(bounds) if b <= t), default=None)
            (pre if idx is None else buckets[idx]).append(text)
        if pre:
            groups.append(("", pre))
        groups += [(c[1], b) for c, b in zip(chapters, buckets) if b]
    else:
        groups.append(("", [t for _, t in lines]))
    out: list[str] = []
    for title, ls in groups:
        cur, size = ([title] if title else []), len(title)
        for line in ls:
            if size + len(line) > max_chars and len(cur) > (1 if title else 0):
                out.append("\n".join(cur))
                cur, size = ([title] if title else []), len(title)
            cur.append(line)
            size += len(line) + 1
        if cur:
            out.append("\n".join(cur))
    return out
