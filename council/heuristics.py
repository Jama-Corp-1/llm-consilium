"""Local, zero-cost prompt classifier for the router's hot path.

``classify_local`` reads the prompt with plain string/regex signals and returns a
capability plus a *confidence*. The confidence is a **routing-defer signal, not a
probability**: at or above :data:`CONFIDENCE_THRESHOLD` the caller may route on the
local verdict alone, below it the caller must fall back to the LLM classifier. The
numbers are tuning knobs, never calibrated likelihoods.

The module makes no network call and touches no member, so it can never affect the
privacy gate or the health store. It imports nothing beyond the standard library and
``council.router.CAPABILITIES``.

Rule shape, in precedence order:

1. **code** 0.9 on a *structural* marker: a fence, a unified-diff header, a traceback
   header, or a source-file path.
2. **code** 0.8 on a cluster of language keywords -- but only together with code-shaped
   punctuation (:data:`CODE_PUNCTUATION` or an indented line). Prose happily says
   "class-based" and "import strategies"; prose rarely says ``foo(x)``.
3. **reasoning** 0.8 on a *strong* English or Russian reasoning marker, or on an
   arithmetic signal (a power operator between operands, an exponent caret).
4. **reasoning** 0.75 on a long prompt asking several questions.
5. **reasoning** 0.6 -- *below* the threshold, so the caller defers -- on a *soft*
   marker. "Should we…" and "…better" do mark comparisons, but they equally open
   "what should we have for dinner?"; the signal is real but too weak to route on.
6. **fast** 0.8 on a short prompt in a covered script with no other signal.
7. **general** 0.3 otherwise -- below the threshold, so the caller defers to the LLM.

The marker sets cover English and Russian, this council's two traffic languages; the
brevity rule applies to both (Latin and Cyrillic), and only to those. A prompt in a
script we carry no markers for falls through to the low-confidence default and is
classified by the LLM, never guessed at. Misrouting a hard prompt to a weak member is
the failure mode this module must avoid, so every tie leans towards ``reasoning``/
``code`` or towards deferring, never towards ``fast``.
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
#: Deliberately below :data:`CONFIDENCE_THRESHOLD`: a soft marker reports the signal
#: without deciding, so the caller still asks the LLM classifier.
REASONING_SOFT_CONFIDENCE = 0.6
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
#: Share of characters outside the covered scripts (ASCII/Latin and Cyrillic) above
#: which the brevity rule is not trusted: a short prompt in a language whose markers
#: this module does not carry is not "fast", it is simply unclassified and belongs to
#: the LLM. A stray em dash or emoji stays under the bar.
UNCOVERED_SCRIPT_RATIO = 0.1
#: Two or more of these in one prompt read as source code rather than prose.
CODE_KEYWORD_CLUSTER = 2
#: Letters of Russian inflection a reasoning marker may grow ("сравни" -> "сравните").
RU_INFLECTION_TAIL = 3

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

#: Prose that asks for an explanation, a judgement or a comparison. The comparison half
#: matters most: "which of these two scales better?" is short, so without these markers
#: the brevity rule would hand a genuinely hard question to a weak "fast" member.
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
    "which of",
    "versus",
    " vs ",
)

#: Markers that *suggest* a comparison but read just as naturally in a trivial ask
#: ("can you make this sentence better?", "what should we have for dinner?"). They are
#: scored at :data:`REASONING_SOFT_CONFIDENCE`, i.e. they defer rather than decide.
REASONING_MARKERS_SOFT = (
    "better",
    "worse",
    "faster",
    "scales",
    "should we",
)

#: The same asks in Russian, this council's other traffic language. Matched
#: case-insensitively (``re`` uses Unicode semantics for ``str`` patterns, so ``\b`` and
#: ``\w`` cover Cyrillic) with a short inflection tail.
REASONING_MARKERS_RU = (
    "почему",
    "зачем",
    "сравни",
    "объясни",
    "обоснуй",
    "архитектура",
    "спроектируй",
    "по шагам",
    "причина",
    "лучше ли",
    "стоит ли",
)

#: Punctuation that prose almost never produces but code almost always does. Required
#: alongside the keyword cluster so "class-based versus functional import strategies"
#: stays prose. A parenthesised expression (the call shape ``()``/``(x)``) and an
#: indented line count too -- see ``_PAREN_RE`` and ``_INDENT_RE``.
CODE_PUNCTUATION = ("{", "};", "=", "::", "->")

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
#: A source extension at a word boundary. The *stem* before it is walked in Python
#: rather than matched by a starred character class: a greedy class in front of the
#: extension backtracks quadratically on a long path-like blob (a pasted traceback).
_EXT_RE = re.compile(r"\.(?:" + "|".join(SOURCE_EXTENSIONS) + r")\b", re.IGNORECASE)
#: Characters that may precede the dot of a path, besides letters and digits.
PATH_STEM_CHARS = "_./\\-"
#: How far back a path stem is read. A longer run is still a path, just not a new one.
PATH_STEM_MAX = 100
#: A parenthesised expression: "()" as well as "cleanup(x)".
_PAREN_RE = re.compile(r"\([^()]*\)")
#: An indented line is code even without any of :data:`CODE_PUNCTUATION`.
_INDENT_RE = re.compile(r"^(?: {4}|\t)", re.MULTILINE)
#: A power operator *between operands* ("2**82589933", "x**2") or an exponent caret
#: ("2^31"): a computation, not a lookup. The operands must sit flush against ``**`` so
#: Markdown emphasis ("**bold**", "Hi **bold**") is not mistaken for arithmetic, and a
#: bare long number is deliberately no signal at all -- it is as likely an id, a price
#: or a phone number as a maths problem.
_MATH_RE = re.compile(r"\w\*\*[\w(]|\^\d")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
#: Any Cyrillic character, for the covered-script test (not just the Russian letters).
_CYRILLIC_CHAR_RE = re.compile(r"[Ѐ-ӿԀ-ԯ]")


def _keyword_re(keyword: str) -> re.Pattern[str]:
    """Strict word-boundary match for alphanumeric keywords, literal match otherwise."""
    escaped = re.escape(keyword)
    if keyword[:1].isalnum():
        escaped = r"\b" + escaped
    if keyword[-1:].isalnum():
        escaped = escaped + r"\b"
    return re.compile(escaped, re.IGNORECASE)


def _marker_re(marker: str) -> re.Pattern[str]:
    """Reasoning marker: leading word boundary, inflection-tolerant tail.

    English markers also accept a plural "s"/"es" ("trade-offs"); Russian markers accept
    up to :data:`RU_INFLECTION_TAIL` extra Cyrillic letters ("сравни" -> "сравните").
    """
    escaped = re.escape(marker)
    if marker[:1].isalnum():
        escaped = r"\b" + escaped
    if marker[-1:].isalnum():
        escaped += (
            rf"[а-яё]{{0,{RU_INFLECTION_TAIL}}}(?!\w)"
            if _CYRILLIC_RE.search(marker)
            else r"(?:es|s)?\b"
        )
    return re.compile(escaped, re.IGNORECASE)


_CODE_KEYWORD_RES = tuple(_keyword_re(k) for k in sorted(CODE_KEYWORDS))
_REASONING_RES = tuple(_marker_re(m) for m in (*REASONING_MARKERS, *REASONING_MARKERS_RU))
_REASONING_SOFT_RES = tuple(_marker_re(m) for m in REASONING_MARKERS_SOFT)


def _is_path_char(ch: str) -> bool:
    return ch.isalnum() or ch in PATH_STEM_CHARS


def _has_source_path(prompt: str) -> bool:
    """A path with a source extension, e.g. ``council/compose.py``.

    The stem must hold an ASCII letter, so a numbered label such as "rule 1.c" is not
    read as a C source file.
    """
    for match in _EXT_RE.finditer(prompt):
        stem = prompt[max(0, match.start() - PATH_STEM_MAX) : match.start()]
        cut = len(stem)
        while cut > 0 and _is_path_char(stem[cut - 1]):
            cut -= 1
        if any(ch.isascii() and ch.isalpha() for ch in stem[cut:]):
            return True
    return False


def _has_structural_code(prompt: str) -> bool:
    return bool(
        _FENCE_RE.search(prompt)
        or _DIFF_RE.search(prompt)
        or _TRACEBACK_RE.search(prompt)
        or _has_source_path(prompt)
    )


def _code_keyword_hits(prompt: str) -> int:
    return sum(1 for pattern in _CODE_KEYWORD_RES if pattern.search(prompt))


def _has_code_punctuation(prompt: str) -> bool:
    return (
        any(token in prompt for token in CODE_PUNCTUATION)
        or bool(_PAREN_RE.search(prompt))
        or bool(_INDENT_RE.search(prompt))
    )


def _has_reasoning_marker(prompt: str) -> bool:
    return any(pattern.search(prompt) for pattern in _REASONING_RES)


def _has_soft_reasoning_marker(prompt: str) -> bool:
    return any(pattern.search(prompt) for pattern in _REASONING_SOFT_RES)


def _has_math_signal(prompt: str) -> bool:
    return bool(_MATH_RE.search(prompt))


def _script_is_covered(prompt: str) -> bool:
    """True when the prompt is written in a script whose markers this module carries.

    Latin/ASCII and Cyrillic are covered, so a short Russian question is as much a
    lookup as a short English one. A prompt in any other script (Chinese, Arabic,
    Hebrew...) is not "fast" merely because our marker lists missed it: it defers.
    A few stray characters (an em dash, an emoji) do not disqualify a prompt.
    """
    if not prompt:
        return False
    uncovered = sum(1 for ch in prompt if ord(ch) > 127 and not _CYRILLIC_CHAR_RE.match(ch))
    return uncovered / len(prompt) <= UNCOVERED_SCRIPT_RATIO


def classify_local(prompt: str) -> tuple[str, float]:
    """Guess the capability a prompt needs -> ``(capability, confidence)``.

    The capability is always one of :data:`council.router.CAPABILITIES`. Signals are
    evaluated in the fixed precedence documented in the module docstring (code,
    reasoning, brevity) so the verdict is deterministic; nothing matching yields
    ``("general", UNSURE_CONFIDENCE)``, which is below the threshold and tells the
    caller to ask the LLM classifier instead.
    """
    if _has_structural_code(prompt):
        return "code", CODE_STRUCTURAL_CONFIDENCE
    if _code_keyword_hits(prompt) >= CODE_KEYWORD_CLUSTER and _has_code_punctuation(prompt):
        return "code", CODE_KEYWORD_CONFIDENCE
    if _has_reasoning_marker(prompt) or _has_math_signal(prompt):
        return "reasoning", REASONING_MARKER_CONFIDENCE
    if len(prompt) > LONG_PROMPT_CHARS and prompt.count("?") >= LONG_PROMPT_MIN_QUESTIONS:
        return "reasoning", REASONING_LONG_CONFIDENCE
    if _has_soft_reasoning_marker(prompt):
        # Checked before the brevity rule: a soft marker must defer, not become "fast".
        return "reasoning", REASONING_SOFT_CONFIDENCE
    stripped = prompt.strip()
    if len(prompt) < FAST_MAX_CHARS and stripped and _script_is_covered(prompt):
        if "?" in stripped or len(stripped.split()) <= FAST_MAX_WORDS:
            return "fast", FAST_CONFIDENCE
    return "general", UNSURE_CONFIDENCE
