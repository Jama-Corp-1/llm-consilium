from __future__ import annotations

import argparse
import threading
import webbrowser

from fastapi import FastAPI

from consilium import env_file
from consilium_chat.app import create_app, is_configured
from consilium_chat.config import load_settings

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _serve(app: FastAPI, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


def _load_env() -> dict[str, str]:
    return env_file.load()


def _should_open(want: str, configured: bool, host: str) -> bool:
    if want == "off" or host not in _LOOPBACK:
        return False
    if want == "on":
        return True
    return not configured  # auto: only on first run


def _open_browser(url: str, delay: float = 1.0) -> None:
    def go() -> None:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001, S110 - best-effort; headless boxes have no browser
            pass

    threading.Timer(delay, go).start()


def main(argv: list[str] | None = None) -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="consilium_chat")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--open", dest="want", action="store_const", const="on",
                     help="always open the browser on start")
    grp.add_argument("--no-open", dest="want", action="store_const", const="off",
                     help="never open the browser")
    parser.set_defaults(want="auto")
    args = parser.parse_args(argv)

    configured = is_configured(_load_env())
    url = f"http://{args.host}:{args.port}/"
    hint = "" if configured else "  (open it to add your API keys)"
    print(f"Consilium Chat on {url}{hint}")
    if _should_open(args.want, configured, args.host):
        _open_browser(url)
    _serve(create_app(), args.host, args.port)


if __name__ == "__main__":
    main()
