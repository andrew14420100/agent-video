"""Generazione di file ASS (libass) per titoli animati, lower third, cartelli
finali e sottotitoli. Le animazioni usano tag ASS standard (\\fad, \\move, \\t)."""
from __future__ import annotations

from ..project import SubtitleCue, TextOverlay


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return (text.replace("\\", "⧵").replace("{", "(").replace("}", ")")
            .replace("\r", "").replace("\n", "\\N"))


def _style(name, font, size, primary, outline_c, back, bold, border_style, outline, shadow, align, mv, ml=0, mr=0):
    return (f"Style: {name},{font},{size},{primary},&H000000FF,{outline_c},{back},{-1 if bold else 0},0,0,0,"
            f"100,100,0,0,{border_style},{outline},{shadow},{align},{ml},{mr},{mv},1")


ACCENT = "&H00FF8B3D"  # blu elettrico (BGR)
WHITE = "&H00FFFFFF"


def build_ass(width: int, height: int, overlays: list[TextOverlay], subtitles: list[SubtitleCue],
              font: str, burn_subtitles: bool) -> str:
    H, W = height, width
    u = H / 1080.0
    styles = [
        _style("Title", font, int(92 * u), WHITE, "&H64000000", "&H96000000", True, 1, max(1, int(2 * u)), int(3 * u), 5, 0),
        _style("TitleSub", font, int(40 * u), "&H00E6E6E6", "&H64000000", "&H96000000", False, 1, max(1, int(1.5 * u)), int(2 * u), 5, 0),
        _style("L3", font, int(46 * u), WHITE, "&H00000000", "&H00000000", True, 1, 0, 0, 1, 0),
        _style("L3Sub", font, int(32 * u), "&H00D8D8D8", "&H00000000", "&H00000000", False, 1, 0, 0, 1, 0),
        _style("Box", font, 20, "&H40141414", "&H00000000", "&H00000000", False, 1, 0, 0, 7, 0),
        _style("Accent", font, 20, ACCENT, "&H00000000", "&H00000000", False, 1, 0, 0, 7, 0),
        _style("Caption", font, int(52 * u), WHITE, "&H00000000", "&H50000000", True, 1, max(2, int(3.2 * u)),
               int(1.5 * u), 2, int(70 * u), int(160 * u), int(160 * u)),
    ]
    ev: list[str] = []

    def dlg(layer, start, end, style, text):
        ev.append(f"Dialogue: {layer},{_ts(start)},{_ts(end)},{style},,0,0,0,,{text}")

    def rect(x, y, w, h):
        return f"m 0 0 l {w:.0f} 0 l {w:.0f} {h:.0f} l 0 {h:.0f}"

    for o in overlays:
        s, e = o.start, o.start + o.duration
        cx, cy = W / 2, H / 2
        if o.kind in ("title", "end_card"):
            ty = cy - (28 * u if o.subtext else 0)
            dlg(5, s, e, "Title", f"{{\\an5\\pos({cx:.0f},{ty:.0f})\\fad(450,450)\\blur0.6"
                f"\\fscx94\\fscy94\\t(0,600,\\fscx100\\fscy100)}}{esc(o.text)}")
            bw = 160 * u
            dlg(4, s + 0.15, e, "Accent", f"{{\\an7\\pos({cx - bw / 2:.0f},{ty + 62 * u:.0f})\\fad(300,450)"
                f"\\fscx0\\t(0,500,\\fscx100)\\p1}}{rect(0, 0, bw, 6 * u)}{{\\p0}}")
            if o.subtext:
                dlg(5, s + 0.25, e, "TitleSub", f"{{\\an5\\pos({cx:.0f},{ty + 110 * u:.0f})\\fad(500,450)}}{esc(o.subtext)}")
        elif o.kind == "lower_third":
            x0, y0 = 110 * u, H - 250 * u
            bw = max(520 * u, (len(o.text) * 27 + 120) * u)
            bh = 120 * u if o.subtext else 84 * u
            dlg(3, s, e, "Box", f"{{\\an7\\move({x0 - 60 * u:.0f},{y0:.0f},{x0:.0f},{y0:.0f},0,350)"
                f"\\fad(250,350)\\p1}}{rect(0, 0, bw, bh)}{{\\p0}}")
            dlg(4, s, e, "Accent", f"{{\\an7\\move({x0 - 60 * u:.0f},{y0:.0f},{x0:.0f},{y0:.0f},0,350)"
                f"\\fad(250,350)\\p1}}{rect(0, 0, 8 * u, bh)}{{\\p0}}")
            dlg(5, s + 0.1, e, "L3", f"{{\\an7\\move({x0 - 30 * u:.0f},{y0 + 14 * u:.0f},{x0 + 34 * u:.0f},{y0 + 14 * u:.0f},0,400)"
                f"\\fad(300,350)}}{esc(o.text)}")
            if o.subtext:
                dlg(5, s + 0.2, e, "L3Sub", f"{{\\an7\\move({x0 - 30 * u:.0f},{y0 + 70 * u:.0f},{x0 + 34 * u:.0f},{y0 + 70 * u:.0f},0,450)"
                    f"\\fad(350,350)}}{esc(o.subtext)}")
        elif o.kind == "caption":
            dlg(5, s, e, "TitleSub", f"{{\\an8\\pos({cx:.0f},{80 * u:.0f})\\fad(300,300)}}{esc(o.text)}")

    if burn_subtitles:
        for c in subtitles:
            dlg(6, c.start, c.end, "Caption", f"{{\\fad(80,80)}}{esc(c.text)}")

    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(head + ev) + "\n"
