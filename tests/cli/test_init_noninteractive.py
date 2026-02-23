"""Tests for non-interactive hexis init mode."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from apps.hexis_init import (
    _DEFAULT_MODELS,
    _PROVIDER_ENV_VARS,
    build_parser,
    detect_provider,
)

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.cli]


# ---------------------------------------------------------------------------
# detect_provider unit tests
# ---------------------------------------------------------------------------


def test_detect_provider_anthropic():
    assert detect_provider("sk-ant-abc123") == "anthropic"


def test_detect_provider_openai():
    assert detect_provider("sk-abc123def") == "openai"


def test_detect_provider_grok():
    assert detect_provider("gsk_abc123") == "grok"


def test_detect_provider_gemini():
    assert detect_provider("AIzaSyAbc123") == "gemini"


def test_detect_provider_unknown():
    with pytest.raises(ValueError, match="Cannot detect provider"):
        detect_provider("xyz-unknown-key")


def test_detect_provider_ordering():
    """sk-ant- must match anthropic, not openai."""
    assert detect_provider("sk-ant-api03-xxxx") == "anthropic"


# ---------------------------------------------------------------------------
# build_parser tests
# ---------------------------------------------------------------------------


def test_build_parser_noninteractive_flags():
    parser = build_parser()
    args = parser.parse_args([
        "--api-key", "sk-ant-test",
        "--character", "hexis",
        "--provider", "anthropic",
        "--model", "claude-sonnet-4-20250514",
        "--name", "Alice",
        "--no-docker",
        "--no-pull",
    ])
    assert args.api_key == "sk-ant-test"
    assert args.character == "hexis"
    assert args.provider == "anthropic"
    assert args.model == "claude-sonnet-4-20250514"
    assert args.name == "Alice"
    assert args.no_docker is True
    assert args.no_pull is True


def test_build_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.api_key is None
    assert args.character is None
    assert args.provider is None
    assert args.model is None
    assert args.name is None
    assert args.no_docker is False
    assert args.no_pull is False


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


def test_default_models_has_all_providers():
    for provider in _PROVIDER_ENV_VARS:
        assert provider in _DEFAULT_MODELS, f"Missing default model for {provider}"


# ---------------------------------------------------------------------------
# CLI smoke tests (subprocess)
# ---------------------------------------------------------------------------

_CWD = str(Path(__file__).resolve().parents[2])


async def test_cli_init_help():
    """hexis init --help includes non-interactive flags."""
    p = subprocess.run(
        [sys.executable, "-m", "apps.hexis_init", "--help"],
        capture_output=True, text=True,
        env=os.environ.copy(),
        cwd=_CWD,
    )
    assert p.returncode == 0
    assert "--api-key" in p.stdout
    assert "--character" in p.stdout
    assert "--provider" in p.stdout
    assert "--no-docker" in p.stdout
    assert "--no-pull" in p.stdout


async def test_cli_init_bad_key_prefix():
    """Unrecognised key prefix exits with error."""
    p = subprocess.run(
        [sys.executable, "-m", "apps.hexis_init",
         "--api-key", "xyz-unknown",
         "--no-docker", "--no-pull"],
        capture_output=True, text=True,
        env=os.environ.copy(),
        cwd=_CWD,
    )
    assert p.returncode != 0
    combined = p.stdout + p.stderr
    assert "Cannot detect provider" in combined or "init failed" in combined
