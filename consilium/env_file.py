from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from consilium.providers import Provider

DEFAULT_ENV_PATH = Path.home() / ".config" / "consilium" / ".env"
MASTER_KEY_VAR = "LITELLM_MASTER_KEY"

_TIER_TITLES = (
    ("A", "Tier A (safe for any prompt — no-train)"),
    ("B", "Tier B (public prompts only)"),
)


def load(path: str | Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return values
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def groups(provs: Iterable[Provider] | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """Env-file sections derived from the providers (default: proxy/config.yaml).

    Inside a tier: vendor order of first appearance, each vendor's vars as given
    (registry sorts them). A var shared by two vendors is listed once.
    """
    if provs is None:
        from consilium import providers  # lazy: providers -> paths/registry; avoid a cycle

        provs = providers.providers()
    prov_list = list(provs)
    out: list[tuple[str, tuple[str, ...]]] = []
    for tier, title in _TIER_TITLES:
        names: list[str] = []
        for p in prov_list:
            if p.tier == tier:
                names.extend(v for v in p.env_vars if v not in names)
        out.append((title, tuple(names)))
    out.append(("Proxy auth", (MASTER_KEY_VAR,)))
    return out


def master_key(path: str | Path = DEFAULT_ENV_PATH) -> str | None:
    """The proxy master key: non-empty process env first, then the env file."""
    return os.environ.get(MASTER_KEY_VAR) or load(path).get(MASTER_KEY_VAR) or None


def _group(
    lines: list[str], title: str, keys: Iterable[str], values: dict[str, str]
) -> None:
    present = [k for k in keys if k in values]
    if not present:
        return
    lines.append(f"# {title}")
    lines.extend(f"{k}={values[k]}" for k in present)
    lines.append("")


def write(path: str | Path = DEFAULT_ENV_PATH, values: dict[str, str] | None = None) -> None:
    data = dict(values or {})
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Consilium secrets — managed by `python -m consilium init`. chmod 600.", ""]
    try:
        sections = groups()
    except Exception:  # noqa: BLE001 - a broken config must not block saving secrets;
        # nothing is lost: provider keys simply land in "Other".
        sections = [("Proxy auth", (MASTER_KEY_VAR,))]
    known: set[str] = set()
    for title, keys in sections:
        _group(lines, title, keys, data)
        known.update(keys)
    _group(lines, "Other", [k for k in data if k not in known], data)
    content = "\n".join(lines).rstrip() + "\n"
    # Secure atomic write: mkstemp creates the temp file mode 0o600 on POSIX (owner
    # read/write only) — no world-readable window, so no follow-up chmod is needed.
    # os.replace atomically swaps it in, replacing any pre-existing symlink at the
    # destination rather than following it. Keeping nothing between mkstemp and fdopen
    # also removes the theoretical fd-leak on an interim failure.
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
