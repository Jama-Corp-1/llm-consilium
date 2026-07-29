"""Admin API router for the consilium chat facade.

The router is deliberately pure over ``request.app.state``: every handler reads a
callable off ``app.state`` and returns its result. Real implementations (which call
the council/consilium reuse functions) are injected by the wiring task; here the
router only consumes the injected callables, so it is hermetically testable with
fakes.
"""

from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def admin_router() -> APIRouter:
    """Build the admin ``APIRouter`` (status / keys / proxy)."""
    router = APIRouter()

    # response_model=None: handlers return heterogeneous JSON (dict[str, object] /
    # a Response union) which is not a valid Pydantic response field; disabling the
    # inferred response model preserves the original (unvalidated) passthrough.
    @router.get("/api/status", response_model=None)
    async def status(request: Request) -> dict[str, object]:
        # app.state is untyped (Any) in FastAPI; the installed provider returns dict.
        return cast("dict[str, object]", request.app.state.status_provider())

    @router.get("/api/models")
    async def models(request: Request) -> list[dict[str, Any]]:
        service = getattr(request.app.state, "service", None)
        return service.list_models() if service is not None else []

    @router.post("/api/keys", response_model=None)
    async def save_keys(request: Request) -> dict[str, object]:
        body = await request.json()
        return cast("dict[str, object]", request.app.state.save_keys(body))

    @router.post("/api/proxy/start", response_model=None)
    async def proxy_start(request: Request) -> dict[str, object] | JSONResponse:
        result = request.app.state.proxy_start()
        if not result or (isinstance(result, dict) and result.get("ok") is False):
            content = result if isinstance(result, dict) else {"ok": False}
            return JSONResponse(status_code=503, content=content)
        return cast("dict[str, object]", result)

    @router.post("/api/proxy/stop", response_model=None)
    async def proxy_stop(request: Request) -> dict[str, object]:
        return cast("dict[str, object]", request.app.state.proxy_stop())

    @router.post("/api/proxy/restart", response_model=None)
    async def proxy_restart(request: Request) -> dict[str, object]:
        return cast("dict[str, object]", request.app.state.proxy_restart())

    return router
