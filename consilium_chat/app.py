"""FastAPI app factory: wires the chat facade to the real reuse functions.

``create_app`` builds sensible defaults (settings, store, guarded council service)
and installs four admin callables on ``app.state`` as closures over the consilium /
council reuse modules. Every admin closure is defensive: a transient failure yields
a minimal valid payload rather than a 500, so the app always serves ``/`` and
``/api/status`` even before the proxy or provider keys exist.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from consilium import env_file, init, paths, providers
from consilium import service as proxy_service
from consilium_chat.config import Settings, load_settings
from consilium_chat.council_service import CouncilService
from consilium_chat.routes_admin import admin_router
from consilium_chat.routes_chat import chat_router
from consilium_chat.store import ChatStore
from council import orchestrator, registry, usage

_WEB_DIR = Path(__file__).parent / "web"


def is_configured(loaded: Mapping[str, str]) -> bool:
    """True iff at least one provider has all of its env vars present."""
    return any(
        all(loaded.get(v) for v in p.env_vars)
        for p in providers.PROVIDERS
    )


def _make_status_provider() -> Callable[[], dict[str, object]]:
    """Build the ``/api/status`` payload from live provider / proxy / usage state."""

    def status_provider() -> dict[str, object]:
        try:
            loaded = env_file.load()
            provider_rows = [
                {
                    "name": p.name,
                    "tier": p.tier,
                    "ready": all(loaded.get(v) for v in p.env_vars),
                    "env_vars": list(p.env_vars),
                    "signup": p.signup,
                }
                for p in providers.PROVIDERS
            ]
            proxy_up = proxy_service.port_open(paths.PROXY_HOST, paths.PROXY_PORT)
            members = registry.load_members(
                orchestrator.DEFAULT_CONFIG_PATH,
                available_keys=registry.available_env_keys(),
            )
            rows = usage.summary(members, usage.UsageStore().counts())
            return {
                "configured": is_configured(loaded),
                "providers": provider_rows,
                "proxy_up": proxy_up,
                "usage": rows,
                "total_cost_usd": round(
                    sum(c for r in rows if isinstance(c := r["cost_usd"], (int, float))), 6
                ),
                "proxy_host": paths.PROXY_HOST,
                "proxy_port": paths.PROXY_PORT,
            }
        except Exception:
            # Never 500 on status: degrade to a minimal but valid payload.
            return {
                "configured": False,
                "providers": [],
                "proxy_up": False,
                "usage": [],
                "total_cost_usd": 0.0,
                "proxy_host": paths.PROXY_HOST,
                "proxy_port": paths.PROXY_PORT,
            }

    return status_provider


def _make_save_keys() -> Callable[[dict[str, str]], dict[str, object]]:
    """Merge submitted keys into the secure env file; return MASKED values + readiness.

    Never returns raw secret values — only ``init.mask(v)``. For each provider whose
    keys are now all present, a best-effort ``live_ping`` records its readiness.
    """

    def save_keys(body: dict[str, str]) -> dict[str, object]:
        submitted = {k: v for k, v in body.items() if v}
        merged = {**env_file.load(), **submitted}
        env_file.write(values=merged)
        masked = {k: init.mask(v) for k, v in submitted.items()}
        readiness: list[dict[str, object]] = []
        client = httpx.Client(timeout=8.0)
        try:
            for p in providers.PROVIDERS:
                if all(merged.get(v) for v in p.env_vars):
                    res = init.live_ping(p, merged, client=client)
                    readiness.append(
                        {"name": p.name, "tier": p.tier, "ok": res.ok, "detail": res.detail}
                    )
        finally:
            client.close()
        return {"keys": masked, "readiness": readiness}

    return save_keys


def _make_proxy_start() -> Callable[[], dict[str, object]]:
    def proxy_start() -> dict[str, object]:
        try:
            proxy_service.start()
            return {
                "ok": True,
                "proxy_up": proxy_service.port_open(paths.PROXY_HOST, paths.PROXY_PORT),
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as an error
            return {"ok": False, "error": str(exc)}

    return proxy_start


def _make_proxy_stop() -> Callable[[], dict[str, object]]:
    def proxy_stop() -> dict[str, object]:
        try:
            proxy_service.stop()
            return {
                "ok": True,
                "proxy_up": proxy_service.port_open(paths.PROXY_HOST, paths.PROXY_PORT),
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as an error
            return {"ok": False, "error": str(exc)}

    return proxy_stop


def _make_proxy_restart() -> Callable[[], dict[str, object]]:
    def proxy_restart() -> dict[str, object]:
        try:
            proxy_service.stop()
            proxy_service.start()
            return {
                "ok": True,
                "proxy_up": proxy_service.port_open(paths.PROXY_HOST, paths.PROXY_PORT),
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as an error
            return {"ok": False, "error": str(exc)}

    return proxy_restart


def create_app(
    *,
    settings: Settings | None = None,
    store: ChatStore | None = None,
    service: CouncilService | None = None,
) -> FastAPI:
    """Build and wire the chat FastAPI app.

    Missing collaborators are constructed with defaults. ``CouncilService.build()``
    is guarded: if it fails (no proxy / keys yet) the service is left ``None`` and the
    app still serves ``/`` and ``/api/status``; chat routes just won't work until a
    proxy exists.
    """
    settings = settings or load_settings()
    if not isinstance(settings, Settings):  # defensive: unexpected override type
        settings = load_settings()
    store = store or ChatStore(settings.chat_db_path)
    if service is None:
        try:
            service = CouncilService.build()
        except Exception:  # noqa: BLE001 - degrade: app serves without chat
            service = None

    app = FastAPI(title="Consilium Chat")
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.state.status_provider = _make_status_provider()
    app.state.save_keys = _make_save_keys()
    app.state.proxy_start = _make_proxy_start()
    app.state.proxy_stop = _make_proxy_stop()
    app.state.proxy_restart = _make_proxy_restart()

    # API routers first so /api/* wins over the static catch-all mounted at "/".
    app.include_router(chat_router())
    app.include_router(admin_router())
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")

    return app
