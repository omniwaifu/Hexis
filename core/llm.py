from __future__ import annotations

import base64
import json
import os
from typing import Any, AsyncIterator

import httpx

try:
    import openai
except Exception:  # pragma: no cover
    openai = None  # type: ignore[assignment]

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

try:
    from google import genai  # type: ignore[import-not-found]
    from google.genai import types as gemini_types  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    genai = None  # type: ignore[assignment]
    gemini_types = None  # type: ignore[assignment]


OPENAI_COMPATIBLE = {
    "openai",
    "openai_compatible",
    "openai-chat-completions-endpoint",
    "ollama",
    "grok",
}

# OpenAI Codex (ChatGPT subscription) backend
_CODEX_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
_CODEX_JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _b64url_decode(raw: str) -> bytes:
    s = (raw or "").strip()
    if not s:
        return b""
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _extract_codex_account_id(token: str) -> str:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT")
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
        account_id = payload.get(_CODEX_JWT_CLAIM_PATH, {}).get("chatgpt_account_id")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("Missing account id in token")
        return account_id
    except Exception as exc:
        raise ValueError("Failed to extract accountId from token") from exc


def _resolve_codex_url(base_url: str | None) -> str:
    raw = (base_url or _CODEX_DEFAULT_BASE_URL).strip()
    normalized = raw.rstrip("/")
    if normalized.endswith("/codex/responses"):
        return normalized
    if normalized.endswith("/codex"):
        return f"{normalized}/responses"
    return f"{normalized}/codex/responses"


def _messages_to_codex_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """
    Convert Chat Completions-style messages into Responses API input items in the
    structured content-part format Codex expects.
    """
    system_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
            continue

        if role == "user":
            input_items.append({
                "role": "user",
                "content": [{"type": "input_text", "text": str(content)}],
            })
            continue

        if role == "assistant":
            if content:
                input_items.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": str(content)}],
                })
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args)
                input_items.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": args,
                })
            continue

        if role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": str(content),
            })
            continue

    instructions = "\n\n".join(p for p in system_parts if p.strip()) or None
    return instructions, input_items


async def _iter_sse_events_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """
    Parse SSE responses where each event is JSON in one or more `data:` lines.
    Mirrors the parsing strategy used by OpenClaw/pi-ai for Codex.
    """
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
            continue
        if line.strip() != "":
            # Ignore non-data SSE fields (event:, id:, retry:, etc.)
            continue
        if not data_lines:
            continue
        data = "\n".join(data_lines).strip()
        data_lines = []
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield obj


async def _codex_responses_completion(
    *,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
    on_text_delta: Any | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("Missing OpenAI Codex OAuth access token (api_key).")

    account_id = _extract_codex_account_id(api_key)
    url = _resolve_codex_url(endpoint)
    instructions, input_items = _messages_to_codex_responses_input(messages)
    responses_tools = _tools_to_responses(tools)

    payload: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "input": input_items,
        "text": {"verbosity": "medium"},
        "include": ["reasoning.encrypted_content"],
    }
    if instructions:
        payload["instructions"] = instructions
    if temperature is not None:
        payload["temperature"] = temperature
    # Codex compatibility: max token field support is inconsistent; omit unless proven.
    _ = max_tokens  # kept for signature parity

    if responses_tools:
        payload["tools"] = responses_tools
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = True

    headers = {
        "Authorization": f"Bearer {api_key}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": "pi",
        "accept": "text/event-stream",
        "content-type": "application/json",
        "user-agent": f"hexis (python; {os.uname().sysname if hasattr(os, 'uname') else 'unknown'})",
    }

    timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code < 200 or resp.status_code >= 300:
                text = await resp.aread()
                raise RuntimeError(
                    f"OpenAI Codex request failed: HTTP {resp.status_code}: {text.decode('utf-8', errors='replace')}"
                )

            content_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            # Current function call (arguments stream)
            current_fc: dict[str, Any] | None = None

            async for event in _iter_sse_events_json(resp):
                event_type = event.get("type")

                if event_type == "response.output_text.delta":
                    delta = event.get("delta", "")
                    if isinstance(delta, str) and delta:
                        content_parts.append(delta)
                        if on_text_delta:
                            import asyncio

                            result = on_text_delta(delta)
                            if asyncio.iscoroutine(result):
                                await result
                    continue

                if event_type == "response.output_item.added":
                    item = event.get("item") or {}
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        current_fc = {
                            "call_id": item.get("call_id"),
                            "name": item.get("name"),
                            "args_buf": item.get("arguments") or "",
                        }
                    continue

                if event_type == "response.function_call_arguments.delta":
                    if current_fc is not None:
                        delta = event.get("delta", "")
                        if isinstance(delta, str) and delta:
                            current_fc["args_buf"] = (current_fc.get("args_buf") or "") + delta
                    continue

                if event_type == "response.function_call_arguments.done":
                    if current_fc is not None:
                        args = event.get("arguments", "")
                        if isinstance(args, str) and args:
                            current_fc["args_buf"] = args
                    continue

                if event_type in {"response.done", "response.completed"}:
                    break

                if event_type in {"error", "response.failed"}:
                    message = (
                        event.get("message")
                        or (event.get("error") or {}).get("message")
                        or "Codex request failed"
                    )
                    raise RuntimeError(str(message))

                if event_type == "response.output_item.done":
                    item = event.get("item") or {}
                    if not isinstance(item, dict) or item.get("type") != "function_call":
                        continue
                    call_id = item.get("call_id") or (current_fc or {}).get("call_id")
                    name = item.get("name") or (current_fc or {}).get("name")
                    args_str = item.get("arguments") or (current_fc or {}).get("args_buf") or ""

                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) and args_str else {}
                    except Exception:
                        args = {}
                    tool_calls.append({
                        "id": call_id,
                        "name": name or "",
                        "arguments": args,
                    })
                    current_fc = None
                    continue

            return {"content": "".join(content_parts), "tool_calls": tool_calls, "raw": None}


# ---------------------------------------------------------------------------
# Responses API capability detection
# ---------------------------------------------------------------------------

_HAS_RESPONSES_API: bool = False
if openai is not None:
    try:
        from openai.resources import responses as _responses_mod  # noqa: F401
        _HAS_RESPONSES_API = True
    except ImportError:
        pass

# Per-endpoint cache: normalized URL -> True (supported) / False (unsupported)
_endpoint_responses_support: dict[str, bool] = {}


def _endpoint_cache_key(endpoint: str | None) -> str:
    return (endpoint or "default").rstrip("/")


def _should_try_responses(endpoint: str | None) -> bool:
    if not _HAS_RESPONSES_API:
        return False
    key = _endpoint_cache_key(endpoint)
    cached = _endpoint_responses_support.get(key)
    if cached is False:
        return False
    return True


def _cache_responses_support(endpoint: str | None, supported: bool) -> None:
    _endpoint_responses_support[_endpoint_cache_key(endpoint)] = supported


def _is_responses_unsupported_error(exc: Exception) -> bool:
    """Return True if the error means the endpoint lacks Responses API support."""
    if openai is None:
        return False
    if isinstance(exc, openai.NotFoundError):
        return True
    if isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError)):
        msg = str(exc).lower()
        if "not found" in msg or "unknown" in msg or "unsupported" in msg:
            return True
    if isinstance(exc, openai.APIStatusError) and getattr(exc, "status_code", 0) == 501:
        return True
    return False


# ---------------------------------------------------------------------------
# Responses API format converters
# ---------------------------------------------------------------------------


def _tools_to_responses(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Convert Chat Completions tools (nested function key) to Responses API flat format."""
    if not tools:
        return []
    result: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {})
        result.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return result


def _messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """
    Convert Chat Completions messages to Responses API (instructions, input_items).

    System messages → instructions parameter.
    Assistant tool_calls (nested OpenAI format) → function_call items.
    Tool result messages → function_call_output items.
    """
    system_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "system":
            if content.strip():
                system_parts.append(content)

        elif role == "user":
            input_items.append({"role": "user", "content": content})

        elif role == "assistant":
            if content:
                input_items.append({"role": "assistant", "content": content})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args)
                input_items.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": args,
                })

        elif role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": content,
            })

    instructions = "\n\n".join(p for p in system_parts if p.strip()) or None
    return instructions, input_items


def _extract_responses_result(response: Any) -> dict[str, Any]:
    """Extract {content, tool_calls, raw} from a Responses API response object."""
    content = getattr(response, "output_text", "") or ""
    tool_calls: list[dict[str, Any]] = []

    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) == "function_call":
            raw_args = getattr(item, "arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                args = {}
            tool_calls.append({
                "id": getattr(item, "call_id", None),
                "name": getattr(item, "name", ""),
                "arguments": args,
            })

    return {"content": content, "tool_calls": tool_calls, "raw": response}


def normalize_provider(provider: str | None) -> str:
    if not provider:
        return "openai"
    raw = provider.strip().lower()
    if raw in {"openai_chat_completions_endpoint"}:
        return "openai-chat-completions-endpoint"
    if raw in {"openai_codex"}:
        return "openai-codex"
    return raw


def normalize_endpoint(provider: str, endpoint: str | None) -> str | None:
    if endpoint:
        return endpoint.strip() or None
    if provider == "ollama":
        return "http://localhost:11434/v1"
    if provider == "grok":
        return "https://api.x.ai/v1"
    if provider == "openai-codex":
        return _CODEX_DEFAULT_BASE_URL
    return None


def resolve_api_key(api_key_env: str | None) -> str | None:
    if not api_key_env:
        return None
    value = api_key_env.strip()
    if not value:
        return None
    import os

    return os.getenv(value)


def normalize_llm_config(config: dict[str, Any] | None, *, default_model: str = "gpt-4o") -> dict[str, Any]:
    config = config or {}
    provider = normalize_provider(str(config.get("provider") or "openai"))
    model = str(config.get("model") or default_model)
    endpoint = normalize_endpoint(provider, str(config.get("endpoint") or "").strip() or None)
    api_key = config.get("api_key")
    if not api_key:
        api_key = resolve_api_key(str(config.get("api_key_env") or "").strip() or None)
    if not api_key:
        provider_env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "grok": "XAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openai_compatible": "OPENAI_API_KEY",
            "openai-chat-completions-endpoint": "OPENAI_API_KEY",
        }
        env_name = provider_env_map.get(provider)
        if env_name:
            api_key = os.getenv(env_name)
    return {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "api_key": api_key,
    }


def _extract_system_prompt(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_parts.append(str(msg.get("content") or ""))
        else:
            rest.append(msg)
    return "\n\n".join([p for p in system_parts if p.strip()]), rest


def _openai_tool_calls(raw_calls: list[Any]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for call in raw_calls or []:
        fn = getattr(call, "function", None) or {}
        name = getattr(fn, "name", None) or fn.get("name")
        raw_args = getattr(fn, "arguments", None) or fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            args = {}
        tool_calls.append({"id": getattr(call, "id", None), "name": name, "arguments": args})
    return tool_calls


def _anthropic_tools(openai_tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not openai_tools:
        return []
    tools: list[dict[str, Any]] = []
    for tool in openai_tools:
        fn = tool.get("function", {})
        tools.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return tools


def _gemini_tools(openai_tools: list[dict[str, Any]] | None) -> list[Any]:
    """
    Convert OpenAI-style tools to google-genai tool declarations.

    Returns a list of google.genai.types.Tool instances (typed as Any here to
    avoid importing google-genai at type-check time).
    """
    if not openai_tools or gemini_types is None:
        return []
    decls: list[Any] = []
    for tool in openai_tools:
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = fn.get("name")
        if not name:
            continue
        decls.append(
            gemini_types.FunctionDeclaration(
                name=name,
                description=fn.get("description", ""),
                parameters_json_schema=fn.get("parameters") or {"type": "object", "properties": {}},
            )
        )
    if not decls:
        return []
    return [gemini_types.Tool(function_declarations=decls)]


def _messages_to_gemini_contents(messages: list[dict[str, Any]]) -> list[Any]:
    """
    Convert OpenAI-style message list into google-genai `contents`.

    Handles:
    - user text messages
    - assistant text messages
    - assistant tool_calls (OpenAI format) -> functionCall parts
    - tool result messages (role=tool) -> functionResponse parts
    """
    if gemini_types is None:
        return []

    # Map OpenAI tool call id -> function name so we can attach tool outputs.
    call_id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            call_id = str(tc.get("id") or "")
            fn = tc.get("function") or {}
            name = str((fn.get("name") if isinstance(fn, dict) else "") or "")
            if call_id and name:
                call_id_to_name[call_id] = name

    contents: list[Any] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""

        if role == "user":
            contents.append(
                gemini_types.Content(
                    role="user",
                    parts=[gemini_types.Part(text=str(content))],
                )
            )
            continue

        if role == "assistant":
            parts: list[Any] = []
            if content:
                parts.append(gemini_types.Part(text=str(content)))

            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    continue
                name = str(fn.get("name") or "")
                if not name:
                    continue
                call_id = tc.get("id")
                raw_args: Any = fn.get("arguments", "{}")
                args: dict[str, Any] = {}
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except Exception:
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                parts.append(
                    gemini_types.Part(
                        function_call=gemini_types.FunctionCall(
                            id=str(call_id) if call_id else None,
                            name=name,
                            args=args,
                        )
                    )
                )

            if parts:
                contents.append(gemini_types.Content(role="model", parts=parts))
            continue

        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            fn_name = call_id_to_name.get(call_id) or ""
            if fn_name:
                contents.append(
                    gemini_types.Content(
                        role="user",
                        parts=[
                            gemini_types.Part(
                                function_response=gemini_types.FunctionResponse(
                                    id=call_id or None,
                                    name=fn_name,
                                    response={"content": str(content)},
                                )
                            )
                        ],
                    )
                )
            else:
                # Fallback: if we can't find a matching function name, inject as user text.
                contents.append(
                    gemini_types.Content(
                        role="user",
                        parts=[gemini_types.Part(text=str(content))],
                    )
                )
            continue

        # Ignore other roles (system is handled separately).

    return contents


# ---------------------------------------------------------------------------
# Responses API implementation
# ---------------------------------------------------------------------------


async def _responses_completion(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Non-streaming completion via the Responses API."""
    instructions, input_items = _messages_to_responses_input(messages)
    responses_tools = _tools_to_responses(tools)

    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if instructions:
        payload["instructions"] = instructions
    payload["input"] = input_items or ""
    if responses_tools:
        payload["tools"] = responses_tools
        payload["tool_choice"] = "auto"
    if response_format:
        fmt_type = response_format.get("type", "text")
        if fmt_type == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        elif fmt_type == "json_schema":
            payload["text"] = {"format": response_format}

    response = await client.responses.create(**payload)
    return _extract_responses_result(response)


async def _responses_stream_completion(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
    on_text_delta: Any | None = None,
) -> dict[str, Any]:
    """Streaming completion via the Responses API."""
    instructions, input_items = _messages_to_responses_input(messages)
    responses_tools = _tools_to_responses(tools)

    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if instructions:
        payload["instructions"] = instructions
    payload["input"] = input_items or ""
    if responses_tools:
        payload["tools"] = responses_tools
        payload["tool_choice"] = "auto"

    content_parts: list[str] = []
    # Accumulate tool calls: item_id -> {call_id, name, arguments_parts}
    tc_accum: dict[str, dict[str, Any]] = {}

    async with client.responses.stream(**payload) as stream:
        async for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "response.output_text.delta":
                text = getattr(event, "delta", "")
                content_parts.append(text)
                if on_text_delta:
                    import asyncio
                    result = on_text_delta(text)
                    if asyncio.iscoroutine(result):
                        await result

            elif event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if item and getattr(item, "type", None) == "function_call":
                    raw_args = getattr(item, "arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args = {}
                    tc_accum[getattr(item, "id", "")] = {
                        "call_id": getattr(item, "call_id", None),
                        "name": getattr(item, "name", ""),
                        "arguments": args,
                    }

    tool_calls: list[dict[str, Any]] = []
    for tc in tc_accum.values():
        tool_calls.append({
            "id": tc["call_id"],
            "name": tc["name"],
            "arguments": tc["arguments"],
        })

    return {"content": "".join(content_parts), "tool_calls": tool_calls, "raw": None}


async def chat_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = normalize_provider(provider)
    endpoint = normalize_endpoint(provider, endpoint)

    if provider == "gemini":
        if genai is None or gemini_types is None:
            raise RuntimeError("google-genai package is required for Gemini provider (pip install google-genai).")
        if not api_key:
            raise RuntimeError("Gemini API key is required. Set GEMINI_API_KEY or configure api_key_env.")

        client = genai.Client(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        contents = _messages_to_gemini_contents(rest)
        gemini_tools = _gemini_tools(tools)

        tool_config = None
        if gemini_tools:
            tool_config = gemini_types.ToolConfig(
                function_calling_config=gemini_types.FunctionCallingConfig(
                    mode=gemini_types.FunctionCallingConfigMode.AUTO,
                )
            )

        config = gemini_types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=gemini_tools or None,
            tool_config=tool_config,
        )
        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        content = getattr(response, "text", "") or ""
        tool_calls: list[dict[str, Any]] = []
        for call in getattr(response, "function_calls", None) or []:
            tool_calls.append({
                "id": getattr(call, "id", None),
                "name": getattr(call, "name", "") or "",
                "arguments": getattr(call, "args", None) or {},
            })
        return {"content": content, "tool_calls": tool_calls, "raw": response}

    if provider in OPENAI_COMPATIBLE:
        if openai is None:
            raise RuntimeError("openai package is required for OpenAI-compatible providers.")
        client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)

        # Try Responses API first, fall back to Chat Completions
        if _should_try_responses(endpoint):
            try:
                result = await _responses_completion(
                    client, model, messages, tools, temperature, max_tokens, response_format,
                )
                _cache_responses_support(endpoint, True)
                return result
            except Exception as exc:
                if _is_responses_unsupported_error(exc):
                    _cache_responses_support(endpoint, False)
                else:
                    raise

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        response = await client.chat.completions.create(**payload)
        message = response.choices[0].message
        content = message.content or ""
        tool_calls = _openai_tool_calls(message.tool_calls or [])
        return {"content": content, "tool_calls": tool_calls, "raw": response}

    if provider == "openai-codex":
        return await _codex_responses_completion(
            model=model,
            endpoint=endpoint,
            api_key=api_key,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            on_text_delta=None,
        )

    if provider == "anthropic":
        if anthropic is None:
            raise RuntimeError("anthropic package is required for Anthropic provider.")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        anthropic_tools = _anthropic_tools(tools)
        response = await client.messages.create(
            model=model,
            system=system_prompt or None,
            messages=rest,
            tools=anthropic_tools or None,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content or []:
            if block.type == "text":
                text_parts.append(block.text)
            if block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "arguments": block.input})
        return {"content": "".join(text_parts), "tool_calls": tool_calls, "raw": response}

    raise ValueError(f"Unsupported provider: {provider}")


async def stream_chat_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    on_text_delta: Any | None = None,
) -> dict[str, Any]:
    """
    Streaming chat completion that supports tools.

    Accumulates the full response while optionally calling ``on_text_delta(text)``
    for each token. Returns the same shape as ``chat_completion()``:
    ``{content, tool_calls, raw}``.

    ``on_text_delta`` can be a sync or async callable accepting a single str
    argument. It's called for each text token as it arrives.
    """
    provider = normalize_provider(provider)
    endpoint = normalize_endpoint(provider, endpoint)

    if provider == "gemini":
        if genai is None or gemini_types is None:
            raise RuntimeError("google-genai package is required for Gemini provider (pip install google-genai).")
        if not api_key:
            raise RuntimeError("Gemini API key is required. Set GEMINI_API_KEY or configure api_key_env.")

        client = genai.Client(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        contents = _messages_to_gemini_contents(rest)
        gemini_tools = _gemini_tools(tools)

        tool_config = None
        if gemini_tools:
            tool_config = gemini_types.ToolConfig(
                function_calling_config=gemini_types.FunctionCallingConfig(
                    mode=gemini_types.FunctionCallingConfigMode.AUTO,
                )
            )

        config = gemini_types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=gemini_tools or None,
            tool_config=tool_config,
        )

        # Track emitted text so we can compute deltas if the stream is cumulative.
        emitted: str = ""
        calls_by_id: dict[str, dict[str, Any]] = {}

        async for chunk in client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            text = getattr(chunk, "text", "") or ""
            if text:
                if text.startswith(emitted):
                    delta = text[len(emitted) :]
                    emitted = text
                else:
                    delta = text
                    emitted += text
                if delta and on_text_delta:
                    import asyncio

                    result = on_text_delta(delta)
                    if asyncio.iscoroutine(result):
                        await result

            for call in getattr(chunk, "function_calls", None) or []:
                call_id = getattr(call, "id", None) or ""
                calls_by_id[str(call_id)] = {
                    "id": call_id or None,
                    "name": getattr(call, "name", "") or "",
                    "arguments": getattr(call, "args", None) or {},
                }

        tool_calls = [v for k, v in calls_by_id.items() if k]
        return {"content": emitted, "tool_calls": tool_calls, "raw": None}

    if provider in OPENAI_COMPATIBLE:
        if openai is None:
            raise RuntimeError("openai package is required for OpenAI-compatible providers.")
        client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)

        # Try Responses API first, fall back to Chat Completions
        if _should_try_responses(endpoint):
            try:
                result = await _responses_stream_completion(
                    client, model, messages, tools, temperature, max_tokens, on_text_delta,
                )
                _cache_responses_support(endpoint, True)
                return result
            except Exception as exc:
                if _is_responses_unsupported_error(exc):
                    _cache_responses_support(endpoint, False)
                else:
                    raise

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response = await client.chat.completions.create(**payload)

        content_parts: list[str] = []
        # Accumulate tool calls: index -> {id, name, arguments_parts}
        tc_accum: dict[int, dict[str, Any]] = {}

        async for event in response:
            delta = event.choices[0].delta
            if delta and delta.content:
                content_parts.append(delta.content)
                if on_text_delta:
                    import asyncio
                    result = on_text_delta(delta.content)
                    if asyncio.iscoroutine(result):
                        await result
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_accum:
                        tc_accum[idx] = {
                            "id": getattr(tc_delta, "id", None),
                            "name": None,
                            "arguments_parts": [],
                        }
                    if tc_delta.id:
                        tc_accum[idx]["id"] = tc_delta.id
                    fn = getattr(tc_delta, "function", None)
                    if fn:
                        if getattr(fn, "name", None):
                            tc_accum[idx]["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            tc_accum[idx]["arguments_parts"].append(fn.arguments)

        # Build final tool calls
        tool_calls: list[dict[str, Any]] = []
        for idx in sorted(tc_accum.keys()):
            tc = tc_accum[idx]
            raw_args = "".join(tc["arguments_parts"])
            try:
                args = json.loads(raw_args) if raw_args else {}
            except Exception:
                args = {}
            tool_calls.append({"id": tc["id"], "name": tc["name"], "arguments": args})
        return {"content": "".join(content_parts), "tool_calls": tool_calls, "raw": None}

    if provider == "openai-codex":
        return await _codex_responses_completion(
            model=model,
            endpoint=endpoint,
            api_key=api_key,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            on_text_delta=on_text_delta,
        )

    if provider == "anthropic":
        if anthropic is None:
            raise RuntimeError("anthropic package is required for Anthropic provider.")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        anthropic_tools = _anthropic_tools(tools)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": rest,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        async with client.messages.stream(**kwargs) as stream:
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            current_tool: dict[str, Any] | None = None

            async for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            current_tool = {"id": block.id, "name": block.name, "arguments_json": ""}
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            text_parts.append(delta.text)
                            if on_text_delta:
                                import asyncio
                                result = on_text_delta(delta.text)
                                if asyncio.iscoroutine(result):
                                    await result
                        elif delta.type == "input_json_delta" and current_tool is not None:
                            current_tool["arguments_json"] += delta.partial_json
                    elif event.type == "content_block_stop":
                        if current_tool is not None:
                            raw_args = current_tool["arguments_json"]
                            try:
                                args = json.loads(raw_args) if raw_args else {}
                            except Exception:
                                args = {}
                            tool_calls.append({
                                "id": current_tool["id"],
                                "name": current_tool["name"],
                                "arguments": args,
                            })
                            current_tool = None

            return {"content": "".join(text_parts), "tool_calls": tool_calls, "raw": None}

    raise ValueError(f"Unsupported provider: {provider}")


async def stream_text_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 1400,
) -> AsyncIterator[str]:
    provider = normalize_provider(provider)
    endpoint = normalize_endpoint(provider, endpoint)

    if provider in OPENAI_COMPATIBLE:
        if openai is None:
            raise RuntimeError("openai package is required for OpenAI-compatible providers.")
        client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for event in response:
            delta = event.choices[0].delta
            if delta and delta.content:
                yield delta.content
        return

    if provider == "gemini":
        if genai is None or gemini_types is None:
            raise RuntimeError("google-genai package is required for Gemini provider (pip install google-genai).")
        if not api_key:
            raise RuntimeError("Gemini API key is required. Set GEMINI_API_KEY or configure api_key_env.")

        client = genai.Client(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        contents = _messages_to_gemini_contents(rest)
        config = gemini_types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        emitted = ""
        async for chunk in client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            text = getattr(chunk, "text", "") or ""
            if not text:
                continue
            if text.startswith(emitted):
                delta = text[len(emitted) :]
                emitted = text
            else:
                delta = text
                emitted += text
            if delta:
                yield delta
        return

    if provider == "anthropic":
        if anthropic is None:
            raise RuntimeError("anthropic package is required for Anthropic provider.")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        async with client.messages.stream(
            model=model,
            system=system_prompt or None,
            messages=rest,
            max_tokens=max_tokens,
            temperature=temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text
        return

    if provider == "openai-codex":
        if not api_key:
            raise RuntimeError("Missing OpenAI Codex OAuth access token (api_key).")
        account_id = _extract_codex_account_id(api_key)
        url = _resolve_codex_url(endpoint)
        instructions, input_items = _messages_to_codex_responses_input(messages)
        payload: dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": True,
            "input": input_items,
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
            "temperature": temperature,
        }
        if instructions:
            payload["instructions"] = instructions
        _ = max_tokens  # max token field support is inconsistent; omit unless proven.
        headers = {
            "Authorization": f"Bearer {api_key}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "pi",
            "accept": "text/event-stream",
            "content-type": "application/json",
            "user-agent": f"hexis (python; {os.uname().sysname if hasattr(os, 'uname') else 'unknown'})",
        }
        timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code < 200 or resp.status_code >= 300:
                    text = await resp.aread()
                    raise RuntimeError(
                        f"OpenAI Codex request failed: HTTP {resp.status_code}: {text.decode('utf-8', errors='replace')}"
                    )
                async for event in _iter_sse_events_json(resp):
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if isinstance(delta, str) and delta:
                            yield delta
                    elif event_type in {"response.done", "response.completed"}:
                        return
                    elif event_type in {"error", "response.failed"}:
                        message = event.get("message") or "Codex request failed"
                        raise RuntimeError(str(message))
        return

    raise ValueError(f"Unsupported provider: {provider}")
