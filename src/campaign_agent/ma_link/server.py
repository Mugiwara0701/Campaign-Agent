"""Run the link app on this machine's Tailscale address, in a thread beside the worker.

The listener must never take the worker down, and must never listen on the LAN: it binds to one Tailscale address
(or one address given on purpose), never to 0.0.0.0. While Tailscale is not up it waits and tries again.
"""

import ipaddress
import logging
import subprocess
import threading
from collections.abc import Callable
from typing import Any

import uvicorn
from fastapi import FastAPI

log = logging.getLogger("campaign_agent.ma_link")

RETRY_S = 15.0
Runner = Callable[..., Any]


def _first_ipv4(text: str) -> str | None:
    for token in text.replace("/", " ").split():
        try:
            ip = ipaddress.ip_address(token)
        except ValueError:
            continue
        if ip.version == 4 and not ip.is_unspecified:
            return str(ip)
    return None


def tailscale_address(run: Runner = subprocess.run) -> str | None:
    """This machine's Tailscale IPv4 address: `tailscale ip -4`, or the address of the tailscale0 interface."""
    commands = (["tailscale", "ip", "-4"], ["ip", "-4", "-o", "addr", "show", "tailscale0"])
    for command in commands:
        try:
            done = run(command, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            address = _first_ipv4(done.stdout)
            if address is not None:
                return address
    return None


def validate_bind(setting: str) -> str | None:
    """None for "tailscale"; otherwise the one literal address. 0.0.0.0 and :: are refused. Raises ValueError."""
    wanted = setting.strip()
    if wanted.lower() == "tailscale":
        return None
    try:
        ip = ipaddress.ip_address(wanted)
    except ValueError as exc:
        raise ValueError(
            f"MA_LINK_BIND must be 'tailscale' or one IP address, not {wanted!r}"
        ) from exc
    if ip.is_unspecified:
        raise ValueError(
            "MA_LINK_BIND must not be 0.0.0.0 or ::, the link must not listen on every interface"
        )
    return str(ip)


def resolve_bind(setting: str, run: Runner = subprocess.run) -> str | None:
    """The address to listen on, or None when it is not known yet (Tailscale not up)."""
    literal = validate_bind(setting)
    return literal if literal is not None else tailscale_address(run)


class LinkServer:
    def __init__(self, app: FastAPI, *, bind: str, port: int) -> None:
        validate_bind(bind)  # a bad setting fails at start, not later
        self._app = app
        self._bind = bind
        self._port = port
        self._stop = threading.Event()
        self._server: uvicorn.Server | None = None
        self._thread = threading.Thread(target=self._run, name="ma-link", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.should_exit = True
        self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                address = resolve_bind(self._bind)
                if address is None:
                    log.warning("link: no Tailscale address yet, retrying", extra={"in_s": RETRY_S})
                else:
                    config = uvicorn.Config(
                        self._app,
                        host=address,
                        port=self._port,
                        log_level="warning",
                        access_log=False,
                    )
                    self._server = uvicorn.Server(config)
                    log.info("link listening", extra={"address": address, "port": self._port})
                    self._server.run()
            except Exception:
                log.exception("link listener failed")
            self._server = None
            self._stop.wait(RETRY_S)
