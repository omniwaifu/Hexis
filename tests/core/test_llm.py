import os

import pytest

from core import llm

pytestmark = pytest.mark.core


def test_normalize_provider_variants():
    assert llm.normalize_provider(None) == "openai"
    assert llm.normalize_provider("OpenAI") == "openai"
    assert llm.normalize_provider("openai_chat_completions_endpoint") == "openai-chat-completions-endpoint"


def test_normalize_endpoint_defaults():
    assert llm.normalize_endpoint("ollama", None) == "http://localhost:11434/v1"
    assert llm.normalize_endpoint("openai", "  ") is None
    assert llm.normalize_endpoint("openai", " https://example.com ") == "https://example.com"


def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "abc123")
    assert llm.resolve_api_key("TEST_API_KEY") == "abc123"
    assert llm.resolve_api_key("  ") is None


def test_normalize_llm_config_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "envkey")
    normalized = llm.normalize_llm_config({"provider": "openai", "model": "gpt-4o"})
    assert normalized["api_key"] == "envkey"


def test_extract_system_prompt():
    messages = [
        {"role": "system", "content": "A"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "B"},
    ]
    system, rest = llm._extract_system_prompt(messages)  # noqa: SLF001
    assert system == "A\n\nB"
    assert rest == [{"role": "user", "content": "hi"}]


def test_openai_tool_call_parsing():
    class DummyFn:
        def __init__(self):
            self.name = "recall"
            self.arguments = '{"query":"hi"}'

    class DummyCall:
        def __init__(self):
            self.id = "call-1"
            self.function = DummyFn()

    parsed = llm._openai_tool_calls([DummyCall()])  # noqa: SLF001
    assert parsed == [{"id": "call-1", "name": "recall", "arguments": {"query": "hi"}}]


def test_anthropic_tools_conversion():
    tools = [
        {"function": {"name": "recall", "description": "desc", "parameters": {"type": "object"}}}
    ]
    out = llm._anthropic_tools(tools)  # noqa: SLF001
    assert out == [{"name": "recall", "description": "desc", "input_schema": {"type": "object"}}]


def test_chunk_text():
    assert llm._chunk_text("") == []  # noqa: SLF001
    chunks = llm._chunk_text("abcd", chunk_size=2)  # noqa: SLF001
    assert chunks == ["ab", "cd"]


@pytest.mark.asyncio(loop_scope="session")
async def test_chat_completion_unsupported_provider():
    with pytest.raises(ValueError):
        await llm.chat_completion(
            provider="unknown",
            model="x",
            endpoint=None,
            api_key=None,
            messages=[{"role": "user", "content": "hi"}],
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_stream_text_completion_unsupported_provider():
    with pytest.raises(ValueError):
        async for _ in llm.stream_text_completion(
            provider="unknown",
            model="x",
            endpoint=None,
            api_key=None,
            messages=[{"role": "user", "content": "hi"}],
        ):
            pass


@pytest.mark.asyncio(loop_scope="session")
async def test_chat_completion_requires_openai_package(monkeypatch):
    monkeypatch.setattr(llm, "openai", None)
    with pytest.raises(RuntimeError):
        await llm.chat_completion(
            provider="openai",
            model="x",
            endpoint=None,
            api_key=None,
            messages=[{"role": "user", "content": "hi"}],
        )
