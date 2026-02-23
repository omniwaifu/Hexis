"""CLI auth sub-commands for all OAuth / token providers.

Public API
----------
``register_auth_subparsers(auth_parser, db_parent)``
    Wire all provider subparsers under ``hexis auth``.

``dispatch_auth_command(func, args, dsn)``
    Handle all ``auth_*`` func strings.  Returns an exit code.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import sys
import time
from typing import Any


def _print_err(msg: str) -> None:
    sys.stderr.write(msg + "\n")


# ---------------------------------------------------------------------------
# Argparse registration
# ---------------------------------------------------------------------------

def register_auth_subparsers(
    auth_sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    db_parent: argparse.ArgumentParser,
) -> None:
    """Register all provider subparsers under the ``auth`` command."""

    # ── Chutes ────────────────────────────────────────────────────
    _register_pkce_provider(auth_sub, db_parent, "chutes", "Chutes AI (PKCE OAuth)", extra_login_args=[
        ("--client-id", {"default": None, "help": "Override Chutes client ID"}),
    ])

    # ── Qwen Portal ───────────────────────────────────────────────
    qw = auth_sub.add_parser("qwen-portal", parents=[db_parent], help="Qwen Portal (device code)")
    qw_sub = qw.add_subparsers(dest="qwen_portal_command")
    _qwl = qw_sub.add_parser("login", parents=[db_parent], help="Login via device code flow")
    _qwl.add_argument("--timeout-seconds", type=int, default=300, help="Polling timeout")
    _qwl.set_defaults(func="auth_qwen_portal_login")
    _qws = qw_sub.add_parser("status", parents=[db_parent], help="Show status")
    _qws.add_argument("--json", action="store_true", help="Output JSON")
    _qws.set_defaults(func="auth_qwen_portal_status")
    _qwo = qw_sub.add_parser("logout", parents=[db_parent], help="Delete stored credentials")
    _qwo.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    _qwo.set_defaults(func="auth_qwen_portal_logout")
    qw.set_defaults(func="auth_qwen_portal")

    # ── MiniMax Portal ────────────────────────────────────────────
    mm = auth_sub.add_parser("minimax-portal", parents=[db_parent], help="MiniMax Portal (user-code + PKCE)")
    mm_sub = mm.add_subparsers(dest="minimax_portal_command")
    _mml = mm_sub.add_parser("login", parents=[db_parent], help="Login via user-code flow")
    _mml.add_argument("--region", choices=["global", "cn"], default="global", help="API region")
    _mml.add_argument("--timeout-seconds", type=int, default=300, help="Polling timeout")
    _mml.set_defaults(func="auth_minimax_portal_login")
    _mms = mm_sub.add_parser("status", parents=[db_parent], help="Show status")
    _mms.add_argument("--json", action="store_true", help="Output JSON")
    _mms.set_defaults(func="auth_minimax_portal_status")
    _mmo = mm_sub.add_parser("logout", parents=[db_parent], help="Delete stored credentials")
    _mmo.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    _mmo.set_defaults(func="auth_minimax_portal_logout")
    mm.set_defaults(func="auth_minimax_portal")



def _register_pkce_provider(
    auth_sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    db_parent: argparse.ArgumentParser,
    name: str,
    help_text: str,
    extra_login_args: list[tuple[str, dict[str, Any]]] | None = None,
) -> None:
    """Helper: register a standard PKCE provider with login/status/logout."""
    slug = name.replace("-", "_")
    p = auth_sub.add_parser(name, parents=[db_parent], help=help_text)
    p_sub = p.add_subparsers(dest=f"{slug}_command")

    login = p_sub.add_parser("login", parents=[db_parent], help="Login via browser OAuth (PKCE)")
    login.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    login.add_argument("--timeout-seconds", type=int, default=120, help="Callback wait timeout")
    login.add_argument("--manual", action="store_true", help="Manual paste flow (skip callback server)")
    for flag, kwargs in extra_login_args or []:
        login.add_argument(flag, **kwargs)
    login.set_defaults(func=f"auth_{slug}_login")

    status = p_sub.add_parser("status", parents=[db_parent], help="Show status")
    status.add_argument("--json", action="store_true", help="Output JSON")
    status.set_defaults(func=f"auth_{slug}_status")

    logout = p_sub.add_parser("logout", parents=[db_parent], help="Delete stored credentials")
    logout.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    logout.set_defaults(func=f"auth_{slug}_logout")

    p.set_defaults(func=f"auth_{slug}")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch_auth_command(func: str, args: Any, dsn: str) -> int | None:
    """Handle an ``auth_*`` func string. Returns exit code, or None if not handled."""
    ws = getattr(args, "wait_seconds", 30)

    # ── Chutes ──
    if func == "auth_chutes":
        return asyncio.run(_generic_oauth_status(dsn, ws, "chutes", as_json=False))
    if func == "auth_chutes_login":
        return asyncio.run(_chutes_login(dsn, ws, args))
    if func == "auth_chutes_status":
        return asyncio.run(_generic_oauth_status(dsn, ws, "chutes", as_json=bool(getattr(args, "json", False))))
    if func == "auth_chutes_logout":
        return asyncio.run(_generic_logout(dsn, ws, "chutes", getattr(args, "yes", False)))

    # ── Qwen Portal ──
    if func == "auth_qwen_portal":
        return asyncio.run(_generic_oauth_status(dsn, ws, "qwen-portal", as_json=False))
    if func == "auth_qwen_portal_login":
        return asyncio.run(_qwen_portal_login(dsn, ws, args))
    if func == "auth_qwen_portal_status":
        return asyncio.run(_generic_oauth_status(dsn, ws, "qwen-portal", as_json=bool(getattr(args, "json", False))))
    if func == "auth_qwen_portal_logout":
        return asyncio.run(_generic_logout(dsn, ws, "qwen-portal", getattr(args, "yes", False)))

    # ── MiniMax Portal ──
    if func == "auth_minimax_portal":
        return asyncio.run(_generic_oauth_status(dsn, ws, "minimax-portal", as_json=False))
    if func == "auth_minimax_portal_login":
        return asyncio.run(_minimax_portal_login(dsn, ws, args))
    if func == "auth_minimax_portal_status":
        return asyncio.run(_generic_oauth_status(dsn, ws, "minimax-portal", as_json=bool(getattr(args, "json", False))))
    if func == "auth_minimax_portal_logout":
        return asyncio.run(_generic_logout(dsn, ws, "minimax-portal", getattr(args, "yes", False)))

    return None  # not handled


# ---------------------------------------------------------------------------
# Provider modules registry (lazy import, keyed by provider slug)
# ---------------------------------------------------------------------------

def _provider_module(provider: str):  # noqa: ANN202
    """Lazy-import the auth module for a provider."""
    _map = {
        "chutes": "core.auth.chutes",
        "qwen-portal": "core.auth.qwen_portal",
        "minimax-portal": "core.auth.minimax_portal",
    }
    import importlib
    return importlib.import_module(_map[provider])


_PROVIDER_LABELS = {
    "chutes": "Chutes",
    "qwen-portal": "Qwen Portal",
    "minimax-portal": "MiniMax Portal",
}


# ---------------------------------------------------------------------------
# Generic status / logout (works for any provider with load_credentials / delete_credentials)
# ---------------------------------------------------------------------------

async def _generic_oauth_status(dsn: str, wait_seconds: int, provider: str, *, as_json: bool) -> int:
    mod = _provider_module(provider)
    creds = mod.load_credentials()

    label = _PROVIDER_LABELS.get(provider, provider)
    if not creds:
        if as_json:
            sys.stdout.write(json.dumps({"configured": False, "provider": provider}, indent=2) + "\n")
        else:
            sys.stdout.write(f"{label}: not logged in\n")
        return 0

    now = int(time.time() * 1000)
    expires_in_s = int((creds.expires_ms - now) / 1000)
    expires_at = _dt.datetime.fromtimestamp(creds.expires_ms / 1000, tz=_dt.timezone.utc).isoformat()

    payload: dict[str, Any] = {
        "configured": True,
        "provider": provider,
        "expires_at": expires_at,
        "expires_in_seconds": expires_in_s,
    }
    # Add provider-specific fields
    for field in ("email", "account_id", "base_url", "project_id", "resource_url", "region"):
        val = getattr(creds, field, None)
        if val is not None:
            payload[field] = val

    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        parts = [f"expires_in={expires_in_s}s"]
        for field in ("email", "account_id", "base_url", "project_id", "region"):
            val = getattr(creds, field, None)
            if val:
                parts.append(f"{field}={val}")
        sys.stdout.write(f"{label}: {' '.join(parts)}\n")
    return 0


async def _generic_logout(dsn: str, wait_seconds: int, provider: str, yes: bool) -> int:
    from apps.cli_theme import console
    mod = _provider_module(provider)
    label = _PROVIDER_LABELS.get(provider, provider)

    if not yes:
        try:
            answer = input(f"Delete stored {label} credentials? Type 'yes' to confirm: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Aborted.[/dim]")
            return 1
        if answer != "yes":
            console.print("[dim]Aborted.[/dim]")
            return 1

    mod.delete_credentials()
    console.print(f"[ok]Deleted {label} credentials.[/ok]")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_authorization_input(text: str) -> tuple[str | None, str | None]:
    """Parse a pasted authorization code or full redirect URL into (code, state)."""
    from urllib.parse import parse_qs, urlparse
    text = text.strip()
    if "?" in text or text.startswith("http"):
        qs = parse_qs(urlparse(text).query)
        code = (qs.get("code") or [None])[0]
        state = (qs.get("state") or [None])[0]
        return code, state
    return text or None, None


# ---------------------------------------------------------------------------
# Chutes login
# ---------------------------------------------------------------------------

async def _chutes_login(dsn: str, wait_seconds: int, args: Any) -> int:
    import os
    import webbrowser

    from apps.cli_theme import console
    from core.auth import create_state, generate_pkce
    from core.auth.callback_server import run_callback_server
    from core.auth.chutes import exchange_code, save_credentials

    client_id = getattr(args, "client_id", None) or os.getenv("CHUTES_CLIENT_ID", "")
    if not client_id:
        _print_err("Chutes requires CHUTES_CLIENT_ID env var or --client-id flag.")
        return 1

    redirect_uri = os.getenv("CHUTES_REDIRECT_URI", "http://localhost:11435/auth/callback")
    verifier, challenge = generate_pkce()
    state = create_state()

    from core.auth.chutes import build_authorize_url
    auth_url = build_authorize_url(
        challenge=challenge, state=state, client_id=client_id, redirect_uri=redirect_uri,
    )

    console.print("\n[bold]Chutes OAuth[/bold]")
    console.print(f"[dim]{auth_url}[/dim]\n")

    no_open = getattr(args, "no_open", False)
    manual = getattr(args, "manual", False)
    timeout_seconds = getattr(args, "timeout_seconds", 120)
    non_interactive = bool(getattr(args, "non_interactive", False))

    code: str | None = None
    if not manual:
        from urllib.parse import urlparse
        parsed_uri = urlparse(redirect_uri)
        port = parsed_uri.port or 80
        path = parsed_uri.path or "/auth/callback"

        if not no_open:
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass

        result = run_callback_server(
            port=port, callback_path=path, timeout_seconds=timeout_seconds, expected_state=state,
        )
        code = result.get("code") if result else None

    if not code and non_interactive:
        _print_err(
            "Authorization callback not received. Retry and complete browser OAuth, or run "
            "`hexis auth chutes login --manual` in a terminal."
        )
        return 1

    if not code:
        try:
            pasted = input("Paste the authorization code (or full redirect URL): ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Aborted.[/dim]")
            return 1
        parsed_code, parsed_state = _parse_authorization_input(pasted)
        if parsed_state and parsed_state != state:
            _print_err("State mismatch.")
            return 1
        code = parsed_code

    if not code:
        _print_err("Missing authorization code.")
        return 1

    console.print("[accent]Exchanging code for tokens...[/accent]")
    creds = await exchange_code(
        code=code, verifier=verifier, client_id=client_id, redirect_uri=redirect_uri,
        client_secret=os.getenv("CHUTES_CLIENT_SECRET"),
    )

    save_credentials(creds)
    console.print(f"[ok]Logged in.[/ok] email={creds.email or 'unknown'}")
    return 0


# ---------------------------------------------------------------------------
# Qwen Portal login
# ---------------------------------------------------------------------------

async def _qwen_portal_login(dsn: str, wait_seconds: int, args: Any) -> int:
    import webbrowser

    from apps.cli_theme import console
    from core.auth.qwen_portal import poll_for_token, save_credentials, start_device_flow

    console.print("\n[bold]Qwen Portal (device code flow)[/bold]")
    device, verifier = await start_device_flow()

    uri = device.verification_uri_complete or device.verification_uri
    console.print(f"\n1. Open: [link]{uri}[/link]")
    if not device.verification_uri_complete:
        console.print(f"2. Enter code: [bold]{device.user_code}[/bold]")
    console.print()

    try:
        webbrowser.open(uri)
    except Exception:
        pass

    console.print("[accent]Waiting for authorization...[/accent]")
    creds = await poll_for_token(device.device_code, verifier, device.interval, device.expires_in)

    save_credentials(creds)
    console.print("[ok]Logged in.[/ok]")
    return 0


# ---------------------------------------------------------------------------
# MiniMax Portal login
# ---------------------------------------------------------------------------

async def _minimax_portal_login(dsn: str, wait_seconds: int, args: Any) -> int:
    import webbrowser

    from apps.cli_theme import console
    from core.auth.minimax_portal import poll_for_token, save_credentials, start_user_code_flow

    region = getattr(args, "region", "global")
    console.print(f"\n[bold]MiniMax Portal (user-code flow)[/bold]  region={region}")
    user_code_resp, verifier = await start_user_code_flow(region)

    console.print(f"\n1. Open: [link]{user_code_resp.verification_uri}[/link]")
    console.print(f"2. Enter code: [bold]{user_code_resp.user_code}[/bold]\n")

    try:
        webbrowser.open(user_code_resp.verification_uri)
    except Exception:
        pass

    console.print("[accent]Waiting for authorization...[/accent]")
    creds = await poll_for_token(
        user_code_resp.user_code, verifier, user_code_resp.interval,
        user_code_resp.expires_in, region,
    )

    save_credentials(creds)
    console.print("[ok]Logged in.[/ok]")
    return 0


