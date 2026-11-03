"""Local, zero-cost prompt classifier for the router's hot path.

``classify_local`` reads the prompt with plain string/regex signals and returns a
capability plus a *confidence*. The confidence is a **routing-defer signal, not a
probability**: at or above :data:`CONFIDENCE_THRESHOLD` the caller may route on the
local verdict alone, below it the caller must fall back to the LLM classifier. The
numbers are tuning knobs, never calibrated likelihoods.

The module makes no network call and touches no member, so it can never affect the
privacy gate or the health store. It imports nothing beyond the standard library and
``council.router.CAPABILITIES``.
"""

from __future__ import annotations

import re

from council.router import CAPABILITIES

# -- tuning constants ---------------------------------------------------------

#: At or above this confidence the local verdict is good enough to route on.
CONFIDENCE_THRESHOLD = 0.7

CODE_STRUCTURAL_CONFIDENCE = 0.9
CODE_KEYWORD_CONFIDENCE = 0.8
REASONING_MARKER_CONFIDENCE = 0.8
REASONING_LONG_CONFIDENCE = 0.75
FAST_CONFIDENCE = 0.8
UNSURE_CONFIDENCE = 0.3

#: A prompt shorter than this (and otherwise unmarked) may be a quick question.
FAST_MAX_CHARS = 120
#: A short prompt only counts as "fast" when it actually asks something ("?") or is
#: no longer than this many words. A short statement like "Tell me about the thing we
#: discussed." is not a lookup: it leans on context the heuristic cannot see, so it is
#: left to the LLM classifier.
FAST_MAX_WORDS = 4
#: A prompt longer than this with several questions reads as a reasoning ask.
LONG_PROMPT_CHARS = 1200
LONG_PROMPT_MIN_QUESTIONS = 2
#: Share of non-ASCII characters above which the brevity rule is not trusted:
#: the marker sets are English-only, so a short non-English prompt is not "fast",
#: it is simply unclassified and belongs to the LLM.
NON_ASCII_RATIO = 0.1
#: Two or more of these in one prompt read as source code rather than prose.
CODE_KEYWORD_CLUSTER = 2

#: Every verdict this module can return; kept in step with the router's capabilities.
VERDICTS = ("code", "reasoning", "fast", "general")
_unknown = set(VERDICTS) - set(CAPABILITIES)
if _unknown:  # pragma: no cover - guards a rename of council.router.CAPABILITIES
    raise RuntimeError(f"heuristics verdicts are not router capabilities: {sorted(_unknown)}")

CODE_KEYWORDS = frozenset(
    {
        "def",
        "class",
        "function",
        "import",
        "return",
        "async",
        "select",
        "impl",
        "#include",
        "const",
        "fn ",
    }
)

REASONING_MARKERS = (
    "why",
    "prove",
    "trade-off",
    "tradeoff",
    "compare",
    "design",
    "architecture",
    "step by step",
    "root cause",
    "explain how",
    "pros and cons",
)

SOURCE_EXTENSIONS = (
    "py", "pyi", "ipynb", "js", "mjs", "cjs", "ts", "tsx", "jsx", "go", "rs", "java",
    "kt", "swift", "c", "h", "cc", "cpp", "hpp", "cs", "rb", "php", "pl", "lua", "sh",
    "bash", "zsh", "sql", "yaml", "yml", "toml", "ini", "cfg", "json",
)

_FENCE_RE = re.compile(r"```")
_DIFF_RE = re.compile(r"^(?:--- |\+\+\+ |@@ )", re.MULTILINE)
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*at [\w.$]+\("
    r"|Exception in thread"
    r"|panic: ",
    re.MULTILINE,
)
_PATH_RE = re.compile(
    r"[\w./\\-]*\w\.(?:" + "|".join(SOURCE_EXTENSIONS) + r")\b", re.IGNORECASE
)


def _keyword_re(keyword: str, *, plural: bool = False) -> re.Pattern[str]:
    """Word-boundary match for alphanumeric keywords, literal match otherwise.

    ``plural`` also accepts a trailing "s"/"es" so "trade-offs" matches "trade-off".
    """
    escaped = re.escape(keyword)
    if keyword[:1].isalnum():
        escaped = r"\b" + escaped
    if keyword[-1:].isalnum():
        escaped = escaped + (r"(?:es|s)?\b" if plural else r"\b")
    return re.compile(escaped, re.IGNORECASE)


_CODE_KEYWORD_RES = tuple(_keyword_re(k) for k in sorted(CODE_KEYWORDS))
_REASONING_RES = tuple(_keyword_re(m, plural=True) for m in REASONING_MARKERS)


def _has_structural_code(prompt: str) -> bool:
    return bool(
        _FENCE_RE.search(prompt)
        or _DIFF_RE.search(prompt)
        or _TRACEBACK_RE.search(prompt)
        or _PATH_RE.search(prompt)
    )


def _code_keyword_hits(prompt: str) -> int:
    return sum(1 for pattern in _CODE_KEYWORD_RES if pattern.search(prompt))


def _has_reasoning_marker(prompt: str) -> bool:
    return any(pattern.search(prompt) for pattern in _REASONING_RES)


def _mostly_ascii(prompt: str) -> bool:
    if not prompt:
        return False
    non_ascii = sum(1 for ch in prompt if ord(ch) > 127)
    return non_ascii / len(prompt) <= NON_ASCII_RATIO


def classify_local(prompt: str) -> tuple[str, float]:
    """Guess the capability a prompt needs -> ``(capability, confidence)``.

    The capability is always one of :data:`council.router.CAPABILITIES`. Signals are
    evaluated in a fixed precedence (code, reasoning, brevity) so the verdict is
    deterministic; nothing matching yields ``("general", UNSURE_CONFIDENCE)``, which is
    below the threshold and tells the caller to ask the LLM classifier instead.
    """
    if _has_structural_code(prompt):
        return "code", CODE_STRUCTURAL_CONFIDENCE
    if _code_keyword_hits(prompt) >= CODE_KEYWORD_CLUSTER:
        return "code", CODE_KEYWORD_CONFIDENCE
    if _has_reasoning_marker(prompt):
        return "reasoning", REASONING_MARKER_CONFIDENCE
    if len(prompt) > LONG_PROMPT_CHARS and prompt.count("?") >= LONG_PROMPT_MIN_QUESTIONS:
        return "reasoning", REASONING_LONG_CONFIDENCE
    stripped = prompt.strip()
    if len(prompt) < FAST_MAX_CHARS and stripped and _mostly_ascii(prompt):
        if "?" in stripped or len(stripped.split()) <= FAST_MAX_WORDS:
            return "fast", FAST_CONFIDENCE
    return "general", UNSURE_CONFIDENCE
