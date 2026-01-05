from __future__ import annotations

import json
import os
import socket
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_FRONTEND_PORT = 3000
DEFAULT_BACKEND_PORT = 8000
PORT_SCAN_RANGE = 50

STATE_PORTS_PATH = Path(".states/hexis_ports.json")
PUBLIC_PORTS_PATH = Path(".web/public/hexis_ports.json")


@dataclass(frozen=True)
class PortRegistry:
    frontend_port: int
    backend_port: int


def _env_port(*names: str) -> int | None:
    for name in names:
        raw = os.getenv(name)
        if raw:
            try:
                return int(raw)
            except ValueError:
                continue
    return None


def _is_port_free(port: int) -> bool:
    addresses = [("0.0.0.0", socket.AF_INET)]
    if socket.has_ipv6:
        addresses.append(("::", socket.AF_INET6))
    for addr, family in addresses:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((addr, port))
            except OSError:
                return False
    return True


def _find_free_port(start: int) -> int:
    for port in range(start, start + PORT_SCAN_RANGE):
        if _is_port_free(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve_port(candidate: int | None, default: int) -> int:
    if candidate is None:
        return _find_free_port(default)
    if _is_port_free(candidate):
        return candidate
    next_start = candidate + 1 if candidate >= 1 else default
    return _find_free_port(next_start)


def _read_ports(path: Path) -> PortRegistry | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return PortRegistry(
            frontend_port=int(payload.get("frontend_port")),
            backend_port=int(payload.get("backend_port")),
        )
    except (TypeError, ValueError):
        return None


def _ports_payload(ports: PortRegistry) -> dict[str, Any]:
    return {
        "frontend_port": ports.frontend_port,
        "backend_port": ports.backend_port,
        "api_url": f"http://localhost:{ports.backend_port}",
        "event_url": f"ws://localhost:{ports.backend_port}/_event",
    }


def _write_ports(path: Path, ports: PortRegistry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_ports_payload(ports)))


def _port_from_url(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if parsed.port:
        return int(parsed.port)
    return None


def _backend_port_from_env_json() -> int | None:
    env_path = Path(".web/env.json")
    if not env_path.exists():
        return None
    try:
        payload = json.loads(env_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for key in ("EVENT", "PING", "HEALTH"):
        value = payload.get(key)
        port = _port_from_url(value)
        if port:
            return port
    return None


def resolve_ports() -> PortRegistry:
    env_front = _env_port("FRONTEND_PORT", "REFLEX_FRONTEND_PORT")
    env_back = _env_port("BACKEND_PORT", "REFLEX_BACKEND_PORT")
    stored = _read_ports(STATE_PORTS_PATH)

    front_candidate = env_front or (stored.frontend_port if stored else None)
    back_candidate = env_back or (stored.backend_port if stored else None)

    frontend_port = _resolve_port(front_candidate, DEFAULT_FRONTEND_PORT)
    backend_port = _resolve_port(back_candidate, DEFAULT_BACKEND_PORT)

    if backend_port == frontend_port:
        backend_port = _find_free_port(backend_port + 1)

    ports = PortRegistry(frontend_port=frontend_port, backend_port=backend_port)
    _write_ports(STATE_PORTS_PATH, ports)
    return ports


def write_public_ports(ports: PortRegistry) -> None:
    _write_ports(PUBLIC_PORTS_PATH, ports)


def publish_runtime_ports() -> None:
    env_front = _env_port("REFLEX_FRONTEND_PORT", "FRONTEND_PORT")
    env_back = _env_port("REFLEX_BACKEND_PORT", "BACKEND_PORT")
    env_front = env_front or _port_from_url(
        os.getenv("REFLEX_DEPLOY_URL") or os.getenv("DEPLOY_URL")
    )
    env_back = env_back or _port_from_url(os.getenv("REFLEX_API_URL") or os.getenv("API_URL"))
    env_back = env_back or _backend_port_from_env_json()
    if env_front is None or env_back is None:
        return

    ports = PortRegistry(frontend_port=env_front, backend_port=env_back)
    _write_ports(STATE_PORTS_PATH, ports)
    write_public_ports(ports)
