from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import zlib
from dataclasses import dataclass
from typing import Any

import httpx


# OpenAI Codex (ChatGPT subscription) OAuth flow constants. Mirrored from OpenClaw/pi-ai.
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_ORIGINATOR = "pi"

# JWT claim path used by the Codex backend to identify the ChatGPT account.
OPENAI_AUTH_JWT_CLAIM_PATH = "https://api.openai.com/auth"

# Config key used as the shared token sink.
OPENAI_CODEX_OAUTH_CONFIG_KEY = "oauth.openai_codex"

# Advisory lock key for refresh-token rotation safety (transaction-scoped lock).
_OPENAI_CODEX_OAUTH_LOCK_KEY = zlib.crc32(OPENAI_CODEX_OAUTH_CONFIG_KEY.encode("utf-8"))


@dataclass(frozen=True)
class OpenAICodexCredentials:
    access: str
    refresh: str
    expires_ms: int
    account_id: str


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    s = raw.strip()
    if not s:
        return b""
    # JWT uses base64url without padding.
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode(s + pad)


def generate_pkce() -> tuple[str, str]:
    """
    Generate (verifier, challenge) for PKCE S256.

    Verifier must be 43..128 chars. We use base64url(32 random bytes) => 43 chars.
    """
    verifier = _b64url_encode(secrets.token_bytes(32))
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def create_state() -> str:
    # OpenClaw/pi-ai uses random 16 bytes hex.
    return secrets.token_hex(16)


def build_authorize_url(
    *,
    challenge: str,
    state: str,
    redirect_uri: str = OPENAI_CODEX_REDIRECT_URI,
    originator: str = OPENAI_CODEX_ORIGINATOR,
) -> str:
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": OPENAI_CODEX_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": OPENAI_CODEX_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # OpenClaw/pi-ai flags:
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    return f"{OPENAI_CODEX_AUTHORIZE_URL}?{urlencode(params)}"


def parse_authorization_input(value: str) -> tuple[str | None, str | None]:
    """
    Accepts:
    - full redirect URL (preferred)
    - "code#state"
    - querystring containing code/state
    - raw code
    """
    v = (value or "").strip()
    if not v:
        return None, None

    # Try URL.
    try:
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(v)
        if parsed.scheme and parsed.netloc:
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            return code, state
    except Exception:
        pass

    # code#state
    if "#" in v:
        code, st = v.split("#", 1)
        return code or None, st or None

    # query string (code=...&state=...)
    if "code=" in v:
        try:
            from urllib.parse import parse_qs

            qs = parse_qs(v)
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            return code, state
        except Exception:
            pass

    return v, None


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = _b64url_decode(parts[1])
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


def extract_account_id(access_token: str) -> str | None:
    payload = decode_jwt_payload(access_token)
    auth = payload.get(OPENAI_AUTH_JWT_CLAIM_PATH) if isinstance(payload, dict) else None
    account_id = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    return account_id if isinstance(account_id, str) and account_id else None


async def exchange_authorization_code(
    *,
    code: str,
    verifier: str,
    redirect_uri: str = OPENAI_CODEX_REDIRECT_URI,
) -> OpenAICodexCredentials:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            OPENAI_CODEX_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "client_id": OPENAI_CODEX_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )

    if resp.status_code < 200 or resp.status_code >= 300:
        # Avoid printing secrets; response body should not contain them.
        raise RuntimeError(f"OpenAI Codex token exchange failed: HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires_in, (int, float)):
        raise RuntimeError("OpenAI Codex token exchange failed: missing fields in response.")

    account_id = extract_account_id(access)
    if not account_id:
        raise RuntimeError("OpenAI Codex token exchange failed: could not extract account id from token.")

    now_ms = int(time.time() * 1000)
    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires_ms=now_ms + int(expires_in * 1000),
        account_id=account_id,
    )


async def refresh_openai_codex_token(refresh_token: str) -> OpenAICodexCredentials:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            OPENAI_CODEX_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OPENAI_CODEX_CLIENT_ID,
            },
        )

    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"OpenAI Codex token refresh failed: HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires_in, (int, float)):
        raise RuntimeError("OpenAI Codex token refresh failed: missing fields in response.")

    account_id = extract_account_id(access)
    if not account_id:
        raise RuntimeError("OpenAI Codex token refresh failed: could not extract account id from token.")

    now_ms = int(time.time() * 1000)
    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires_ms=now_ms + int(expires_in * 1000),
        account_id=account_id,
    )


def credentials_to_dict(creds: OpenAICodexCredentials) -> dict[str, Any]:
    return {
        "access": creds.access,
        "refresh": creds.refresh,
        "expires_ms": int(creds.expires_ms),
        "account_id": creds.account_id,
    }


def credentials_from_value(value: Any) -> OpenAICodexCredentials | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, dict):
        return None

    access = value.get("access")
    refresh = value.get("refresh")
    expires_ms = value.get("expires_ms") or value.get("expires")
    account_id = value.get("account_id") or value.get("accountId")

    if not isinstance(access, str) or not isinstance(refresh, str):
        return None
    if not isinstance(expires_ms, (int, float)):
        return None
    if not isinstance(account_id, str) or not account_id:
        # Allow missing account id if only legacy creds exist; recompute if possible.
        account_id = extract_account_id(access) or ""
    if not account_id:
        return None

    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires_ms=int(expires_ms),
        account_id=account_id,
    )


async def load_openai_codex_credentials(conn) -> OpenAICodexCredentials | None:
    value = await conn.fetchval("SELECT get_config($1)", OPENAI_CODEX_OAUTH_CONFIG_KEY)
    return credentials_from_value(value)


async def save_openai_codex_credentials(conn, creds: OpenAICodexCredentials) -> None:
    await conn.execute(
        "SELECT set_config($1, $2::jsonb)",
        OPENAI_CODEX_OAUTH_CONFIG_KEY,
        json.dumps(credentials_to_dict(creds)),
    )


async def delete_openai_codex_credentials(conn) -> None:
    await conn.execute("SELECT delete_config_key($1)", OPENAI_CODEX_OAUTH_CONFIG_KEY)


async def ensure_fresh_openai_codex_credentials(
    conn,
    *,
    skew_seconds: int = 300,
) -> OpenAICodexCredentials:
    """
    Return valid credentials. Refreshes if expiring/expired.

    Uses a transaction-scoped advisory lock so multiple processes don't race and
    rotate refresh tokens out from under each other.
    """
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _OPENAI_CODEX_OAUTH_LOCK_KEY)
        creds = await load_openai_codex_credentials(conn)
        if not creds:
            raise RuntimeError(
                "OpenAI Codex OAuth is not configured. Run: `hexis auth openai-codex login`"
            )

        now_ms = int(time.time() * 1000)
        if creds.expires_ms > now_ms + skew_seconds * 1000:
            return creds

        refreshed = await refresh_openai_codex_token(creds.refresh)
        await save_openai_codex_credentials(conn, refreshed)
        return refreshed

