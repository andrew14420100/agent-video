"""Interpretazione della richiesta in linguaggio naturale (italiano/inglese).

Due livelli:
1. **Regole deterministiche** (sempre disponibili): durata, risoluzione,
   orientamento, codec, ritmo, look, titolo, testo della voce, musica, sottotitoli,
   argomenti per la ricerca.
2. **LLM locale (Ollama)**, se raggiungibile: completa ciò che le regole non
   possono fare (es. scrivere la narrazione o tradurre gli argomenti in inglese).
   I valori espliciti trovati dalle regole hanno sempre la precedenza.
"""
from __future__ import annotations

import re
from typing import Optional

from .. import llm
from ..project import Brief, ExportSettings

_NUM_WORDS = {"un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
              "dieci": 10, "quindici": 15, "venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50,
              "sessanta": 60, "novanta": 90, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10,
              "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60}

STOP = set("""a ad al alla alle allo ai agli all anche che chi con col come da dal dalla dei del della delle
dello di e ed gli i il in la le lo ma mi mio nel nella nelle nei negli o per piu più poi se si sia sul sulla
su tra fra un una uno vorrei voglio fammi fai crea creare realizza realizzare genera generare montaggio video
clip clips filmato filmati immagini musica secondi secondo minuti minuto durata titolo stile ritmo voce
sottotitoli logo con senza dinamico veloce calmo lento cinematografico verticale orizzontale formato
the of and to for with in on an make create edit video please seconds minutes music title style subtitles
mio mia miei mie tuo questo questa cerca cercare online internet usa usare utilizza materiale libero
licenza gratuito dei degli ogni molto poco breve lungo bel bello bella professionale qualità alta 4k hd
full 1080p 2160p hevc h264 h265 fps uhd testo narrazione narrante vista visto visti viste dalla dallo
rilassante dinamica veloci energico ritmato cinematografica mie miei tue nostre filmati footage prova""".split())

IT_EN = {"spazio": "space", "terra": "earth", "luna": "moon", "razzo": "rocket", "lancio": "launch",
         "stelle": "stars", "stella": "star", "pianeta": "planet", "pianeti": "planets", "sole": "sun",
         "mare": "sea", "oceano": "ocean", "onde": "waves", "montagna": "mountain", "montagne": "mountains",
         "città": "city", "citta": "city", "natura": "nature", "foresta": "forest", "bosco": "forest",
         "neve": "snow", "ghiaccio": "ice", "fuoco": "fire", "vulcano": "volcano", "nuvole": "clouds",
         "cielo": "sky", "tramonto": "sunset", "alba": "sunrise", "astronauta": "astronaut",
         "astronauti": "astronauts", "stazione": "station", "spaziale": "space", "galassia": "galaxy",
         "aurora": "aurora", "uragano": "hurricane", "tempesta": "storm", "fiume": "river", "lago": "lake",
         "animali": "animals", "uccelli": "birds", "auto": "car", "treno": "train", "aereo": "airplane",
         "notte": "night", "pioggia": "rain", "deserto": "desert", "isola": "island", "spiaggia": "beach",
         "satellite": "satellite", "missione": "mission", "marte": "mars", "giove": "jupiter"}


def parse_duration(text: str) -> Optional[float]:
    t = text.lower()
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:[.,]\d+)?|\b(?:" + "|".join(_NUM_WORDS) + r"))\s*"
                                r"(minuti|minuto|min|minutes|minute|m\b|secondi|secondo|sec|seconds|second|s\b)", t):
        n = float(num.replace(",", ".")) if num[0].isdigit() else _NUM_WORDS[num]
        total += n * 60 if unit.startswith("m") else n
        found = True
    if "mezzo minuto" in t or "half a minute" in t:
        return 30.0
    if "minuto e mezzo" in t:
        return 90.0
    return total if found and total > 0 else None


def _quoted(text: str) -> list[str]:
    return [a or b or c for a, b, c in re.findall(r"\"([^\"]{2,120})\"|«([^»]{2,120})»|“([^”]{2,120})”", text)]


def parse_rules(prompt: str) -> tuple[Brief, dict, list[str]]:
    """Restituisce (brief, override_export, campi_espliciti)."""
    b = Brief(prompt=prompt)
    explicit: list[str] = []
    exp: dict = {}
    m = re.search(r"(?:testo(?: della voce| narrazione)?|narrazione|voiceover|voice-over|voce narrante|script)\s*:\s*"
                  r"(.+)$", prompt, re.I | re.S)
    core = prompt
    if m:
        txt = m.group(1).strip().strip("\"«»“”")
        if len(txt) > 3:
            b.narration = txt
            b.want_voice = True
            explicit.append("narration")
            core = prompt[: m.start()]
    t = core.lower()

    d = parse_duration(core)
    if d:
        b.duration = max(3.0, min(1800.0, d))
        explicit.append("duration")

    if re.search(r"\b(4k|2160p|uhd|ultra hd)\b", t):
        exp.update(width=3840, height=2160)
    elif re.search(r"\b(1440p|2k)\b", t):
        exp.update(width=2560, height=1440)
    elif re.search(r"\b(1080p|full ?hd|fhd)\b", t):
        exp.update(width=1920, height=1080)
    elif re.search(r"\b720p\b", t):
        exp.update(width=1280, height=720)
    if re.search(r"vertical|verticale|9:16|reel|tiktok|shorts|stories", t):
        w, h = exp.get("width", 1920), exp.get("height", 1080)
        exp.update(width=min(w, h), height=max(w, h))
    elif re.search(r"quadrat|square|1:1", t):
        s = min(exp.get("width", 1080), exp.get("height", 1080))
        exp.update(width=s, height=s)
    if re.search(r"\b(hevc|h\.?265|x265)\b", t):
        exp["codec"] = "hevc"
    elif re.search(r"\b(h\.?264|avc|x264)\b", t):
        exp["codec"] = "h264"
    m = re.search(r"\b(24|25|30|50|60)\s*fps\b", t)
    if m:
        exp["fps"] = int(m.group(1))

    if re.search(r"dinamic|veloce|energic|ritmat|frenetic|adrenalin|trailer|action|fast|hype|incalzant", t):
        b.pace = "dynamic"
    elif re.search(r"calm|rilassant|lent|tranquill|slow|relax|meditativ|emozional|poetic|sognant", t):
        b.pace = "calm"
    if b.pace != "normal":
        explicit.append("pace")

    if re.search(r"cinematograf|cinematic|film look|filmic", t):
        b.look = "cinematic"
    elif re.search(r"bianco e nero|b/n|black and white|monocrom", t):
        b.look = "mono"
    elif re.search(r"vivac|vivid|saturat|colorat", t):
        b.look = "vivid"
    elif re.search(r"\bcald[oi]\b|warm", t):
        b.look = "warm"

    m = re.search(r"(?:titolo|intitolat[oa]|title[d]?)\s*[:=]?\s*[\"«“]([^\"»”]{2,100})[\"»”]", core, re.I)
    if m:
        b.title = m.group(1).strip()
        explicit.append("title")
    else:
        m = re.search(r"(?:titolo|title)\s*:\s*([^\n.;]{2,80})", core, re.I)
        if m:
            b.title = m.group(1).strip()
            explicit.append("title")
    m = re.search(r"(?:sottotitolo|tagline)\s*[:=]\s*[\"«“]?([^\"»”\n]{2,100})", core, re.I)
    if m:
        b.subtitle = m.group(1).strip()

    if re.search(r"voce narrante|narrazione|voiceover|voice-over|narrator|con (la )?voce|speaker", t):
        b.want_voice = True

    if re.search(r"senza musica|no music|niente musica|without music", t):
        b.want_music = False
        explicit.append("music")
    if re.search(r"senza sottotitoli|no subtitles|niente sottotitoli|without subtitles", t):
        b.want_subtitles = False
        explicit.append("subtitles")

    if re.search(r"\bcerca|online|internet|nasa|wikimedia|commons|creative commons|materiale libero|stock", t):
        b.search_online = True
    # argomenti: parole significative, escluse quelle del testo della voce
    for q in _quoted(core):
        core = core.replace(q, " ")
    words = [w for w in re.findall(r"[a-zà-ÿ]{3,}", core.lower()) if w not in STOP]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    b.topics = seen[:6]
    return b, exp, explicit


LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "duration_seconds": {"type": "number"},
        "pace": {"type": "string", "enum": ["calm", "normal", "dynamic"]},
        "narration": {"type": "string"},
        "search_keywords_en": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "duration_seconds", "pace", "narration", "search_keywords_en", "sections"],
}

SYSTEM = (
    "Sei il regista di uno studio di montaggio video. Interpreti la richiesta dell'utente e restituisci "
    "SOLO JSON conforme allo schema. Regole: la narrazione è in italiano, originale, senza inventare fatti "
    "specifici non presenti nella richiesta, e deve poter essere letta in circa {words} parole "
    "(lascia vuoto se l'utente non vuole una voce). search_keywords_en sono 2-5 parole chiave inglesi per "
    "cercare filmati a licenza libera. sections sono 0-4 brevi etichette di capitolo."
)


def interpret(prompt: str, use_llm: bool = True) -> tuple[Brief, dict, list[str]]:
    """Restituisce (brief, override_export, note)."""
    b, exp, explicit = parse_rules(prompt)
    notes = ["Richiesta interpretata con regole deterministiche"]
    b.planner = "rules"
    if use_llm:
        st = llm.status()
        if st.get("reachable") and st.get("model_present"):
            words = int((b.duration - 2) * 2.3)
            try:
                data = llm.chat_json(SYSTEM.format(words=words), prompt, LLM_SCHEMA)
                if "title" not in explicit and data.get("title"):
                    b.title = str(data["title"])[:90]
                if not b.subtitle and data.get("subtitle"):
                    b.subtitle = str(data["subtitle"])[:90]
                if "duration" not in explicit and data.get("duration_seconds"):
                    b.duration = float(max(5, min(600, float(data["duration_seconds"]))))
                if "pace" not in explicit and data.get("pace") in ("calm", "normal", "dynamic"):
                    b.pace = data["pace"]
                if b.want_voice and "narration" not in explicit and data.get("narration"):
                    b.narration = str(data["narration"]).strip()
                kws = [str(k).lower() for k in (data.get("search_keywords_en") or []) if str(k).strip()]
                if kws:
                    b.topics = kws[:5]
                b.notes = [str(s)[:60] for s in (data.get("sections") or [])][:4]
                b.planner = f"llm:{st['model']}"
                notes = [f"Richiesta interpretata da LLM locale ({st['model']}) + regole"]
            except llm.LLMUnavailable as e:
                notes.append(f"LLM non usato: {e}")
        elif st.get("enabled"):
            notes.append("LLM locale non disponibile (Ollama non attivo o modello assente): uso solo regole")
    if b.search_online and b.planner == "rules":
        b.topics = [IT_EN.get(w, w) for w in b.topics]
    if b.want_voice and not b.narration:
        notes.append("Voce richiesta ma nessun testo fornito e nessun LLM disponibile per scriverlo: "
                     "aggiungi 'testo: ...' alla richiesta")
    return b, exp, notes


def apply_export(exp: ExportSettings, override: dict) -> ExportSettings:
    return exp.model_copy(update=override)
