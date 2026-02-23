from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

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
    "chutes",
    "qwen-portal",
}

# OpenAI client cache: (api_key, base_url, provider) -> client
_openai_clients: dict[tuple[str, str, str], Any] = {}

# LLM retry configuration
_LLM_MAX_RETRIES = 3
_LLM_RETRY_BACKOFF_BASE = 2  # seconds


async def _retry_on_transient(coro_factory, *, max_retries: int = _LLM_MAX_RETRIES) -> Any:
    """Retry an LLM call on transient errors (rate limits, server errors, network)."""
    import asyncio as _asyncio

    last_exc = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc).lower()
            status = getattr(exc, 'status_code', None) or getattr(exc, 'status', None)
            is_transient = (
                status in (429, 502, 503, 529)
                or 'rate' in exc_str
                or 'overloaded' in exc_str
                or 'timeout' in exc_str
                or 'connection' in exc_str
                or isinstance(exc, (ConnectionError, TimeoutError, OSError))
            )
            if is_transient and attempt < max_retries - 1:
                wait = _LLM_RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, max_retries, wait, exc,
                )
                await _asyncio.sleep(wait)
                continue
            raise
    raise last_exc  # Should not reach here, but just in case


def _get_openai_client(api_key: str | None, base_url: str | None, provider: str, default_headers: dict[str, Any] | None = None) -> Any:
    """Get or create a cached OpenAI client."""
    if openai is None:
        raise RuntimeError("openai package is required for OpenAI-compatible providers.")

    headers_key = tuple(sorted((default_headers or {}).items())) if default_headers else ()
    cache_key = (api_key or "", base_url or "", provider, headers_key)
    if cache_key in _openai_clients:
        return _openai_clients[cache_key]

    client_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
    if default_headers:
        client_kwargs["default_headers"] = default_headers

    client = openai.AsyncOpenAI(**client_kwargs)
    _openai_clients[cache_key] = client
    return client


def _clear_openai_client_cache() -> None:
    """Clear the OpenAI client cache. Useful for testing."""
    _openai_clients.clear()




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
                logger.debug("Failed to parse tool arguments: %r", str(raw_args)[:200])
                args = {}
            tool_calls.append({
                "id": getattr(item, "call_id", None),
                "name": getattr(item, "name", ""),
                "arguments": args,
            })

    return {"content": content, "tool_calls": tool_calls, "raw": response}


_PROVIDER_ALIASES: dict[str, str] = {
    "openai_chat_completions_endpoint": "openai-chat-completions-endpoint",
    "qwen_portal": "qwen-portal",
    "minimax_portal": "minimax-portal",
}


def normalize_provider(provider: str | None) -> str:
    if not provider:
        return "openai"
    raw = provider.strip().lower()
    return _PROVIDER_ALIASES.get(raw, raw)


def normalize_endpoint(provider: str, endpoint: str | None) -> str | None:
    if endpoint:
        return endpoint.strip() or None
    _DEFAULTS: dict[str, str] = {
        "ollama": "http://localhost:11434/v1",
        "grok": "https://api.x.ai/v1",
        "chutes": "https://api.chutes.ai/v1",
        "qwen-portal": "https://portal.qwen.ai/v1",
    }
    return _DEFAULTS.get(provider)


def resolve_api_key(api_key_env: str | None) -> str | None:
    if not api_key_env:
        return None
    value = api_key_env.strip()
    if not value:
        return None
    import os

    return os.getenv(value)


def normalize_llm_config(config: dict[str, Any] | None, *, default_model: str = "gpt-4o") -> dict[str, Any]:
    """Normalize a raw LLM config dict (provider aliases, env-var API keys, endpoint defaults).

    .. warning::
        This function does **not** run provider-specific credential loaders
        (OAuth token refresh for Chutes, Qwen Portal, MiniMax Portal, etc.).  Entry
        points that need fully-resolved credentials should use
        :func:`core.llm_config.resolve_llm_config` or
        :func:`core.llm_config.load_llm_config` instead.
    """
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
    result: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "api_key": api_key,
    }
    # Preserve auth_mode when set by provider-specific config loaders.
    auth_mode = config.get("auth_mode")
    if auth_mode:
        result["auth_mode"] = auth_mode
    return result


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
            logger.debug("Failed to parse tool arguments: %r", str(raw_args)[:200])
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
                        logger.debug("Failed to parse tool arguments: %r", raw_args[:200])
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
                        logger.debug("Failed to parse tool arguments: %r", str(raw_args)[:200])
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
    auth_mode: str | None = None,
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

        async def _do_gemini_completion():
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

        return await _retry_on_transient(_do_gemini_completion)

    if provider in OPENAI_COMPATIBLE:
        client = _get_openai_client(api_key, endpoint, provider)

        # Try Responses API first, fall back to Chat Completions
        if _should_try_responses(endpoint):
            try:
                result = await _retry_on_transient(lambda: _responses_completion(
                    client, model, messages, tools, temperature, max_tokens, response_format,
                ))
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

        async def _do_chat_completion():
            response = await client.chat.completions.create(**payload)
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = _openai_tool_calls(message.tool_calls or [])
            return {"content": content, "tool_calls": tool_calls, "raw": response}

        return await _retry_on_transient(_do_chat_completion)

    if provider == "anthropic":
        if auth_mode == "setup-token":
            from core.providers.anthropic_http import anthropic_http_completion
            system_prompt, rest = _extract_system_prompt(messages)
            return await _retry_on_transient(lambda: anthropic_http_completion(
                endpoint=endpoint or "https://api.anthropic.com",
                api_key=api_key or "",
                model=model,
                messages=rest,
                tools=tools,
                auth_mode="setup-token",
                max_tokens=max_tokens,
                system_prompt=system_prompt or None,
            ))
        if anthropic is None:
            raise RuntimeError("anthropic package is required for Anthropic provider.")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        anthropic_tools = _anthropic_tools(tools)

        async def _do_anthropic_completion():
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

        return await _retry_on_transient(_do_anthropic_completion)

    if provider == "minimax-portal":
        from core.providers.anthropic_http import anthropic_http_completion
        system_prompt, rest = _extract_system_prompt(messages)
        return await anthropic_http_completion(
            endpoint=endpoint or "https://api.minimax.io/anthropic",
            api_key=api_key or "",
            model=model,
            messages=rest,
            tools=tools,
            auth_mode="api-key",
            max_tokens=max_tokens,
            system_prompt=system_prompt or None,
        )

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
    auth_mode: str | None = None,
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

        async def _do_gemini_stream():
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

        return await _retry_on_transient(_do_gemini_stream)

    if provider in OPENAI_COMPATIBLE:
        client = _get_openai_client(api_key, endpoint, provider)

        # Try Responses API first, fall back to Chat Completions
        if _should_try_responses(endpoint):
            try:
                result = await _retry_on_transient(lambda: _responses_stream_completion(
                    client, model, messages, tools, temperature, max_tokens, on_text_delta,
                ))
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

        async def _do_stream_completion():
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
                    logger.debug("Failed to parse tool arguments: %r", raw_args[:200])
                    args = {}
                tool_calls.append({"id": tc["id"], "name": tc["name"], "arguments": args})
            return {"content": "".join(content_parts), "tool_calls": tool_calls, "raw": None}

        return await _retry_on_transient(_do_stream_completion)

    if provider == "anthropic":
        if auth_mode == "setup-token":
            from core.providers.anthropic_http import stream_anthropic_http_completion
            system_prompt, rest = _extract_system_prompt(messages)
            return await _retry_on_transient(lambda: stream_anthropic_http_completion(
                endpoint=endpoint or "https://api.anthropic.com",
                api_key=api_key or "",
                model=model,
                messages=rest,
                tools=tools,
                auth_mode="setup-token",
                max_tokens=max_tokens,
                system_prompt=system_prompt or None,
                on_text_delta=on_text_delta,
            ))
        if anthropic is None:
            raise RuntimeError("anthropic package is required for Anthropic provider.")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt, rest = _extract_system_prompt(messages)
        anthropic_tools = _anthropic_tools(tools)
        sdk_kwargs: dict[str, Any] = {
            "model": model,
            "messages": rest,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            sdk_kwargs["system"] = system_prompt
        if anthropic_tools:
            sdk_kwargs["tools"] = anthropic_tools

        async def _do_anthropic_stream():
            async with client.messages.stream(**sdk_kwargs) as stream:
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
                                    logger.debug("Failed to parse tool arguments: %r", raw_args[:200])
                                    args = {}
                                tool_calls.append({
                                    "id": current_tool["id"],
                                    "name": current_tool["name"],
                                    "arguments": args,
                                })
                                current_tool = None

                return {"content": "".join(text_parts), "tool_calls": tool_calls, "raw": None}

        return await _retry_on_transient(_do_anthropic_stream)

    if provider == "minimax-portal":
        from core.providers.anthropic_http import stream_anthropic_http_completion
        system_prompt, rest = _extract_system_prompt(messages)
        return await stream_anthropic_http_completion(
            endpoint=endpoint or "https://api.minimax.io/anthropic",
            api_key=api_key or "",
            model=model,
            messages=rest,
            tools=tools,
            auth_mode="api-key",
            max_tokens=max_tokens,
            system_prompt=system_prompt or None,
            on_text_delta=on_text_delta,
        )

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
        client = _get_openai_client(api_key, endpoint, provider)

        async def _do_text_stream():
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

        # Note: We can't wrap a generator with retry logic the same way,
        # but we retry the initial request
        async def _start_stream():
            return _do_text_stream()

        stream = await _retry_on_transient(_start_stream)
        async for chunk in stream:
            yield chunk
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

    if provider == "minimax-portal":
        from core.providers.anthropic_http import stream_anthropic_http_completion
        system_prompt, rest = _extract_system_prompt(messages)
        result = await stream_anthropic_http_completion(
            endpoint=endpoint or "https://api.minimax.io/anthropic",
            api_key=api_key or "",
            model=model,
            messages=rest,
            tools=None,
            auth_mode="api-key",
            max_tokens=max_tokens,
            system_prompt=system_prompt or None,
        )
        if result["content"]:
            yield result["content"]
        return

    raise ValueError(f"Unsupported provider: {provider}")
