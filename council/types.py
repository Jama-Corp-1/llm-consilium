from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# (alias, prompt) -> answer text
AsyncCaller = Callable[[str, str], Awaitable[str]]
# (alias, prompt) -> CallResult (content + which alias actually served it)
DetailedCaller = Callable[[str, str], Awaitable["CallResult"]]


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
