from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

try:
    import openai
except Exception:  # pragma: no cover
    openai = None  # type: ignore[assignment]

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


OPENAI_COMPATIBLE = {
    "openai",
    "openai_compatible",
    "openai-chat-completions-endpoint",
    "ollama",
    "grok",
    "gemini",
}


def normalize_provider(provider: str | None) -> str:
    if not provider:
        return "openai"
    raw = provider.strip().lower()
    if raw in {"openai_chat_completions_endpoint"}:
        return "openai-chat-completions-endpoint"
    return raw


def normalize_endpoint(provider: str, endpoint: str | None) -> str | None:
    if endpoint:
        return endpoint.strip() or None
    if provider == "ollama":
        return "http://localhost:11434/v1"
    if provider == "grok":
        return "https://api.x.ai/v1"
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
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
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

    if provider in OPENAI_COMPATIBLE:
        if openai is None:
            raise RuntimeError("openai package is required for OpenAI-compatible providers.")
        client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)
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

    if provider in OPENAI_COMPATIBLE:
        if openai is None:
            raise RuntimeError("openai package is required for OpenAI-compatible providers.")
        client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)
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

    raise ValueError(f"Unsupported provider: {provider}")
