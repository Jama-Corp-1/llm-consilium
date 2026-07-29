from __future__ import annotations

from collections.abc import Callable
from typing import Any

from consilium import env_file
from council import orchestrator, registry
from council.orchestrator import Orchestrator
from council.types import AskResult, CouncilResult


class CouncilService:
    def __init__(self, orch: Orchestrator) -> None:
        self._orch = orch

    @classmethod
    def build(cls) -> CouncilService:
        key = env_file.load().get("LITELLM_MASTER_KEY", "")
        return cls(orchestrator.build(api_key=key))

    async def ask(
        self, prompt: str, *, model: str | None = None,
        capability: str | None = None, sensitivity: str = "sensitive",
    ) -> AskResult:
        return await self._orch.ask(prompt, model=model, capability=capability,
                                    sensitivity=sensitivity)

    async def council(
        self, prompt: str, *, members: list[str] | None = None,
        size: int | None = None, mode: str | None = None,
        sensitivity: str = "sensitive",
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> CouncilResult:
        return await self._orch.council(prompt, members=members, size=size, mode=mode,
                                        sensitivity=sensitivity, on_progress=on_progress)

    def list_models(self) -> list[dict[str, Any]]:
        members = registry.load_members(
            orchestrator.DEFAULT_CONFIG_PATH, available_keys=registry.available_env_keys())
        return [{"alias": m.alias, "tier": m.privacy_tier,
                 "provider_family": m.provider_family, "capabilities": list(m.capabilities)}
                for m in members]
