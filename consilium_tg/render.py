from __future__ import annotations

from collections.abc import Iterable, Mapping

_PENDING, _OK, _FAIL = "○", "✓", "✗"


def short(alias: str) -> str:
    return alias.split("/", 1)[1] if alias.startswith("council/") else alias


def progress_text(
    roster: Iterable[str], done: Mapping[str, bool], aggregating: bool
) -> str:
    lines = ["🧠 Council"]
    for alias in roster:
        if alias not in done:
            mark = _PENDING
        else:
            mark = _OK if done[alias] else _FAIL
        lines.append(f"{mark} {short(alias)}")
    lines.append("…synthesizing…" if aggregating else "…gathering answers…")
    return "\n".join(lines)


def answer_text(content: str, meta: Mapping[str, object], *, show_footer: bool) -> str:
    if not show_footer or not meta:
        return content
    bits = [str(meta[k]) for k in ("mode", "confidence", "model") if meta.get(k)]
    footer = " · ".join(bits)
    note = meta.get("note")
    out = f"{content}\n\n— {footer}" if footer else content
    if note:
        out = f"{out}\nⓘ {note}"
    return out


def chunk(text: str, limit: int = 4096) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


def settings_layout(settings: Mapping[str, object]) -> list[list[tuple[str, str]]]:
    tool = settings.get("tool", "council")
    sens = settings.get("sensitivity", "sensitive")
    mode = settings.get("mode") or "auto"
    size = settings.get("size") if settings.get("size") is not None else "auto"
    foot = "on" if settings.get("show_footer") else "off"
    return [
        [(f"Tool: {tool}", "set:tool")],
        [(f"Sensitivity (tier): {sens}", "set:sensitivity")],
        [(f"Council mode: {mode}", "set:mode"), (f"Size: {size}", "set:size")],
        [("Models (council roster)…", "menu:models")],
        [(f"Footer: {foot}", "set:show_footer")],
    ]


def models_layout(
    models: Iterable[Mapping[str, object]], selected: Iterable[str] | None
) -> list[list[tuple[str, str]]]:
    sel = set(selected or [])
    rows = [[("Auto (compose by size)", "mdl:auto")]]
    for m in models:
        mark = "☑" if m["alias"] in sel else "☐"
        rows.append([(f"{mark} {short(str(m['alias']))} [{m.get('tier', '?')}]",
                      f"mdl:{m['alias']}")])
    rows.append([("‹ Back", "menu:settings")])
    return rows


def sessions_layout(
    sessions: Iterable[Mapping[str, object]]
) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    for s in sessions:
        mark = "● " if s.get("active") else "○ "
        rows.append([
            (f"{mark}{s['title']}", f"sess:switch:{s['id']}"),
            ("🗑", f"sess:del:{s['id']}"),
        ])
    rows.append([("➕ New session", "sess:new")])
    return rows
