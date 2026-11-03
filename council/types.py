from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol


class AsyncCaller(Protocol):
    """(alias, prompt) -> answer text, as the *task-aware* consumers see it.

    ``router.classify`` and ``aggregate`` know what KIND of work a call is but not which
    member will serve it, so they pass intent (``reasoning``) and leave the translation
    into provider parameters to the orchestrator, which knows the member.
    """

    # Positional-only: every call site passes the pair positionally, so an
    # implementation is free to name them (``_alias``, ``text``, ...).
    def __call__(
        self, alias: str, prompt: str, /, *, reasoning: bool = ...
    ) -> Awaitable[str]: ...


class DetailedCaller(Protocol):
    """(alias, prompt) -> CallResult (content + which alias actually served it).

    The provider parameters are already RESOLVED here: ``reasoning_effort`` is passed
    only for a member whose config says it accepts the parameter, and ``max_tokens`` is
    that member's budget. ``None`` means "leave it to the transport's default".
    """

    def __call__(
        self,
        alias: str,
        prompt: str,
        /,
        *,
        reasoning_effort: str | None = ...,
        max_tokens: int | None = ...,
    ) -> Awaitable[CallResult]: ...


class PlainCaller(Protocol):
    """A ``DetailedCaller`` that answers with bare text instead of a ``CallResult``.

    Accepted for injection into the ``Orchestrator`` (it cannot report a proxy fallback).
    """

    def __call__(
        self,
        alias: str,
        prompt: str,
        /,
        *,
        reasoning_effort: str | None = ...,
        max_tokens: int | None = ...,
    ) -> Awaitable[str]: ...


@dataclass(frozen=True)
class Member:
    alias: str
    privacy_tier: str
    scores: dict[str, int]
    rpm: int
    provider_family: str = ""
    rpd: int | None = None
    tpd: int | None = None
    cost_per_1k: float = 0.0
    model_id: str = ""  # litellm_params.model verbatim
    vendor: str = ""  # inference vendor: "groq" | "cloudflare" | "mistral" | "nvidia_nim" | ...
    api_base_ref: str | None = None  # e.g. "os.environ/CLOUDFLARE_API_BASE"
    required_env: frozenset[str] = frozenset()
    timeout: float | None = None  # model_info.timeout (seconds)
    # model_info.reasoning_effort: the member ACCEPTS an explicit reasoning effort.
    # Verified per member, never inferred: the same weights are accepted on Groq and
    # rejected (HTTP 400 UnsupportedParamsError) behind Cloudflare's OpenAI shim.
    accepts_reasoning_effort: bool = False
    max_tokens: int | None = None  # model_info.max_tokens: per-member completion budget

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self.scores)

    @property
    def strength(self) -> int:
        return max(self.scores.values()) if self.scores else 1


@dataclass(frozen=True)
class MemberAnswer:
    alias: str
    ok: bool
    answer: str | None
    detail: str
    served_by: str | None = None  # alias that actually served, when != alias
    permanent: bool = False  # failure was permanent (drives health marking)


@dataclass(frozen=True)
class CallResult:
    content: str
    served_alias: str
    fallbacks: int = 0


@dataclass(frozen=True)
class AskResult:
    answer: str
    model_used: str
    capability: str | None
    note: str


@dataclass(frozen=True)
class CouncilResult:
    answer: str
    per_member: list[MemberAnswer]
    disagreements: str
    judge_used: str | None
    mode: str
    note: str = ""
    confidence: str = ""


@dataclass(frozen=True)
class AggregateResult:
    answer: str
    mode: str
    disagreements: str
    judge_used: str | None
    confidence: str
