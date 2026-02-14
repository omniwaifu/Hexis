from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core import cli_api
from core.agent_api import db_dsn_from_env, resolve_instance

try:
    _ver = pkg_version("hexis")
except PackageNotFoundError:
    _ver = "dev"


def _print_err(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _find_compose_file(start: Path | None = None) -> tuple[Path | None, bool]:
    """Find compose file. Returns (path, is_source_checkout).

    A source checkout is identified by having both docker-compose.yml and
    ops/Dockerfile.db in the same tree.  When no source checkout is found,
    falls back to the bundled runtime compose shipped with pip install.
    """
    cur = (start or Path.cwd()).resolve()
    for parent in (cur,) + tuple(cur.parents):
        candidate = parent / "docker-compose.yml"
        if candidate.exists() and (parent / "ops" / "Dockerfile.db").exists():
            return candidate, True
    # Fall back to bundled runtime compose (pip install)
    runtime = Path(__file__).parent.parent / "ops" / "docker-compose.runtime.yml"
    if runtime.exists():
        return runtime, False
    return None, False


def _stack_root_from_compose(compose_file: Path) -> Path:
    if compose_file.parent.name == "ops":
        return compose_file.parent.parent
    return compose_file.parent


def ensure_docker() -> str:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        _print_err("Docker is not installed or not on PATH. Install Docker Desktop: https://docs.docker.com/get-docker/")
        raise SystemExit(1)
    try:
        subprocess.run([docker_bin, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except subprocess.CalledProcessError:
        _print_err("Docker is installed but not running. Start Docker Desktop and retry.")
        raise SystemExit(1)
    return docker_bin


def ensure_compose(docker_bin: str) -> list[str]:
    try:
        subprocess.run([docker_bin, "compose", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return [docker_bin, "compose"]
    except Exception:
        pass
    compose_bin = shutil.which("docker-compose")
    if compose_bin:
        return [compose_bin]
    _print_err("Docker Compose not available. Install Compose: https://docs.docker.com/compose/install/")
    raise SystemExit(1)


def resolve_env_file(stack_root: Path) -> Path | None:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / ".env.local",
        stack_root / ".env",
        stack_root / ".env.local",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def run_compose(
    compose_cmd: list[str],
    compose_file: Path,
    stack_root: Path,
    args: list[str],
    env_file: Path | None,
) -> int:
    cmd = compose_cmd + ["-f", str(compose_file)]
    if env_file:
        cmd += ["--env-file", str(env_file)]
    cmd += args

    try:
        result = subprocess.run(cmd, cwd=stack_root, env=os.environ.copy())
        return result.returncode
    except FileNotFoundError:
        _print_err("Failed to run docker compose. Ensure Docker is installed.")
        return 1


def _run_compose_capture(
    compose_cmd: list[str], compose_file: Path, stack_root: Path, args: list[str], env_file: Path | None
) -> tuple[int, str]:
    cmd = compose_cmd + ["-f", str(compose_file)]
    if env_file:
        cmd += ["--env-file", str(env_file)]
    cmd += args
    try:
        p = subprocess.run(cmd, cwd=stack_root, env=os.environ.copy(), capture_output=True, text=True)
        out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 1, "Failed to run docker compose. Ensure Docker is installed."


def _redact_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(cfg))  # deep copy via json

    def _is_sensitive_config_key(key: str) -> bool:
        k = (key or "").lower()
        if k.startswith("oauth."):
            return True
        if "user.contact" in k:
            return True
        if "api_key" in k and not k.endswith("api_key_env"):
            return True
        return any(s in k for s in ("token", "secret", "password"))

    def _is_sensitive_field_name(name: str) -> bool:
        n = (name or "").lower()
        if n in {"api_key_env"}:
            return False
        if n in {"api_key", "access", "refresh", "id_token"}:
            return True
        if n == "destinations":
            return True
        if "api_key" in n and not n.endswith("_env"):
            return True
        return any(s in n for s in ("token", "secret", "password"))

    def _redact_deep(value: Any) -> Any:
        if isinstance(value, list):
            return [_redact_deep(v) for v in value]
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for k, v in value.items():
                if _is_sensitive_field_name(str(k)):
                    redacted[str(k)] = "***"
                else:
                    redacted[str(k)] = _redact_deep(v)
            return redacted
        return value

    for key, value in list(out.items()):
        if _is_sensitive_config_key(str(key)):
            out[str(key)] = _redact_deep(value) if isinstance(value, (dict, list)) else "***"
        else:
            out[str(key)] = _redact_deep(value) if isinstance(value, (dict, list)) else value

    return out


def _make_db_flags() -> argparse.ArgumentParser:
    """Shared --dsn / --wait-seconds parent parser."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    p.add_argument("--wait-seconds", type=int,
                   default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    return p


# Group definitions for custom help display
_HELP_GROUPS = [
    ("Getting Started", [
        ("init", "Set up your agent"),
        ("doctor", "Diagnose common issues"),
        ("status", "Show agent status"),
    ]),
    ("Stack", [
        ("up", "Start the stack"),
        ("down", "Stop the stack"),
        ("reset", "Wipe the DB and re-initialize"),
        ("ps", "List services"),
        ("logs", "Show logs"),
        ("start", "Start heartbeat and maintenance workers"),
        ("stop", "Stop workers (containers stay running)"),
    ]),
    ("Interact", [
        ("chat", "Chat in the terminal"),
        ("ui", "Start the web dashboard"),
        ("open", "Open the web dashboard in your browser"),
    ]),
    ("Memory & Goals", [
        ("recall", "Search memories by semantic query"),
        ("goals", "Manage agent goals"),
        ("ingest", "Ingest documents and knowledge"),
        ("schedule", "Manage scheduled tasks"),
    ]),
    ("Configuration", [
        ("config", "Show/validate agent configuration"),
        ("auth", "Login/logout for subscription OAuth providers"),
        ("tools", "Manage tools configuration"),
        ("characters", "Manage character cards"),
        ("consents", "Manage consent certificates"),
        ("channels", "Manage channel adapters"),
    ]),
    ("Instances", [
        ("instance", "Manage Hexis instances"),
    ]),
    ("Advanced", [
        ("api", "Start the API server"),
        ("mcp", "Start the MCP tools server"),
        ("worker", "Run a background worker process"),
    ]),
]


def _print_grouped_help() -> None:
    """Print custom grouped help using Rich."""
    from rich.console import Console
    from rich.text import Text

    console = Console()
    console.print(f"\nhexis v{_ver}")
    console.print("[dim]Persistent memory and identity for AI[/dim]\n")
    console.print("[bold]Usage:[/bold] hexis <command> [options]\n")

    for group_name, commands in _HELP_GROUPS:
        console.print(f"  [bold]{group_name}[/bold]")
        for cmd, desc in commands:
            console.print(f"    {cmd:<14} {desc}")
        console.print()

    console.print("Run 'hexis help <command>' for details on a specific command.\n")


def build_parser() -> argparse.ArgumentParser:
    _db = _make_db_flags()

    p = argparse.ArgumentParser(
        prog="hexis",
        description="Hexis \u2014 persistent memory and identity for AI",
        add_help=False,
    )
    p.add_argument("-h", "--help", action="store_true", default=False, help="Show help")
    p.add_argument("--version", "-V", action="version", version=f"hexis {_ver}")
    p.add_argument(
        "--instance", "-i",
        default=None,
        help="Target a specific instance (overrides HEXIS_INSTANCE and current instance)",
    )
    sub = p.add_subparsers(dest="command", required=False)

    # -- Instance management (nested under 'instance') --
    instance = sub.add_parser("instance", parents=[_db], help="Manage Hexis instances")
    inst_sub = instance.add_subparsers(dest="instance_command")

    inst_create = inst_sub.add_parser("create", help="Create a new instance")
    inst_create.add_argument("name", help="Instance name")
    inst_create.add_argument("--description", "-d", default="", help="Instance description")
    inst_create.set_defaults(func="instance_create")

    inst_list = inst_sub.add_parser("list", help="List all instances")
    inst_list.add_argument("--json", action="store_true", help="Output JSON")
    inst_list.set_defaults(func="instance_list")

    inst_use = inst_sub.add_parser("use", help="Switch to a different instance")
    inst_use.add_argument("name", help="Instance name to switch to")
    inst_use.set_defaults(func="instance_use")

    inst_current = inst_sub.add_parser("current", help="Show current instance")
    inst_current.set_defaults(func="instance_current")

    inst_delete = inst_sub.add_parser("delete", help="Delete an instance")
    inst_delete.add_argument("name", help="Instance name to delete")
    inst_delete.add_argument("--force", action="store_true", help="Skip confirmation")
    inst_delete.add_argument("--reason", default=None, help="Reason for deletion (shared with the agent)")
    inst_delete.set_defaults(func="instance_delete")

    inst_clone = inst_sub.add_parser("clone", help="Clone an instance")
    inst_clone.add_argument("source", help="Source instance name")
    inst_clone.add_argument("target", help="Target instance name")
    inst_clone.add_argument("--description", "-d", default="", help="Description for new instance")
    inst_clone.set_defaults(func="instance_clone")

    inst_import = inst_sub.add_parser("import", help="Import an existing database as an instance")
    inst_import.add_argument("name", help="Instance name")
    inst_import.add_argument("--database", help="Database name (defaults to hexis_{name})")
    inst_import.add_argument("--description", "-d", default="", help="Instance description")
    inst_import.set_defaults(func="instance_import")

    instance.set_defaults(func="instance")

    # -- Consent management --
    consents = sub.add_parser("consents", help="Manage consent certificates")
    consents_sub = consents.add_subparsers(dest="consents_command")

    consents_list = consents_sub.add_parser("list", help="List all consent certificates")
    consents_list.add_argument("--json", action="store_true", help="Output JSON")
    consents_list.set_defaults(func="consents_list")

    consents_show = consents_sub.add_parser("show", help="Show a specific consent certificate")
    consents_show.add_argument("model", help="Model identifier (provider/model_id)")
    consents_show.set_defaults(func="consents_show")

    consents_request = consents_sub.add_parser("request", help="Request consent from a model")
    consents_request.add_argument("model", help="Model identifier (provider/model_id)")
    consents_request.set_defaults(func="consents_request")

    consents_revoke = consents_sub.add_parser("revoke", help="Revoke consent for a model")
    consents_revoke.add_argument("model", help="Model identifier (provider/model_id)")
    consents_revoke.add_argument("--reason", default="User requested revocation", help="Revocation reason")
    consents_revoke.set_defaults(func="consents_revoke")

    consents.set_defaults(func="consents")

    # -- Stack commands --
    up = sub.add_parser("up", help="Start the stack")
    up.add_argument("--build", action="store_true", help="Build images before starting")
    up.add_argument("--profile", "-p", action="append", default=[], help="Compose profile(s)")
    up.set_defaults(func="up")

    down = sub.add_parser("down", help="Stop the stack")
    down.set_defaults(func="down")

    logs = sub.add_parser("logs", help="Show logs")
    logs.add_argument("--follow", "-f", action="store_true", help="Follow log output")
    logs.add_argument("services", nargs="*", default=[], help="Service name(s)")
    logs.set_defaults(func="logs")

    ps = sub.add_parser("ps", help="List services")
    ps.set_defaults(func="ps")

    chat = sub.add_parser("chat", help="Chat in the terminal")
    chat.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to chat")
    chat.set_defaults(func="chat")

    ingest = sub.add_parser("ingest", help="Ingest documents and knowledge")
    ingest.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to ingest")
    ingest.set_defaults(func="ingest")

    worker = sub.add_parser("worker", help="Run a background worker process")
    worker.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to worker")
    worker.set_defaults(func="worker")

    init = sub.add_parser("init", help="Set up your agent")
    init.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to init wizard")
    init.set_defaults(func="init")

    mcp = sub.add_parser("mcp", help="Start the MCP tools server")
    mcp.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to MCP server")
    mcp.set_defaults(func="mcp")

    api = sub.add_parser("api", help="Start the API server")
    api.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    api.add_argument("--port", type=int, default=43817, help="Port (default: 43817)")
    api.set_defaults(func="api")

    ui = sub.add_parser("ui", help="Start the web dashboard")
    ui.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    ui.add_argument("--port", type=int, default=3477, help="Port (default: 3477)")
    ui.set_defaults(func="ui")

    open_cmd = sub.add_parser("open", help="Open the web dashboard in your browser")
    open_cmd.add_argument("--port", type=int, default=3477, help="Port (default: 3477)")
    open_cmd.set_defaults(func="open")

    start = sub.add_parser("start", help="Start heartbeat and maintenance workers")
    start.set_defaults(func="start")

    stop = sub.add_parser("stop", help="Stop workers (containers stay running)")
    stop.set_defaults(func="stop")

    reset = sub.add_parser("reset", help="Wipe the DB and re-initialize")
    reset.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    reset.set_defaults(func="reset")

    status = sub.add_parser("status", parents=[_db], help="Show agent status")
    status.add_argument("--json", action="store_true", help="Output JSON")
    status.add_argument("--no-docker", action="store_true", help="Skip docker compose checks")
    status.add_argument("--raw", action="store_true", help="Show raw status (legacy format)")
    status.set_defaults(func="status")

    doctor = sub.add_parser("doctor", parents=[_db], help="Diagnose common issues")
    doctor.add_argument("--json", action="store_true", help="Output JSON")
    doctor.add_argument("--demo", action="store_true", help="Run end-to-end sanity check against the DB")
    doctor.set_defaults(func="doctor")

    # Hidden alias: 'demo' → 'doctor --demo'
    demo_alias = sub.add_parser("demo")  # no help= → hidden
    demo_alias.add_argument("--dsn", default=None)
    demo_alias.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))
    demo_alias.add_argument("--json", action="store_true")
    demo_alias.set_defaults(func="demo")

    # -- Config (defaults to 'show') --
    config = sub.add_parser("config", parents=[_db], help="Show/validate agent configuration")
    cfg_sub = config.add_subparsers(dest="config_command")

    cfg_show = cfg_sub.add_parser("show", parents=[_db], help="Print config table")
    cfg_show.add_argument("--json", action="store_true", help="Output JSON")
    cfg_show.add_argument("--no-redact", action="store_true", help="Do not redact sensitive values (unsafe)")
    cfg_show.set_defaults(func="config_show")

    cfg_validate = cfg_sub.add_parser("validate", parents=[_db], help="Validate required config keys and environment references")
    cfg_validate.set_defaults(func="config_validate")

    config.set_defaults(func="config")

    # -- Auth (OAuth / subscription flows) --
    auth = sub.add_parser("auth", parents=[_db], help="Manage provider authentication (OAuth)")
    auth_sub = auth.add_subparsers(dest="auth_command")
    from apps.cli_auth import register_auth_subparsers
    register_auth_subparsers(auth_sub, _db)
    auth.set_defaults(func="auth")

    # -- Tools subcommand --
    tools = sub.add_parser("tools", help="Manage tools configuration")
    tools_sub = tools.add_subparsers(dest="tools_command", required=True)

    tools_list = tools_sub.add_parser("list", parents=[_db], help="List all available tools")
    tools_list.add_argument("--json", action="store_true", help="Output JSON")
    tools_list.add_argument("--context", choices=["heartbeat", "chat", "mcp"], help="Filter by context")
    tools_list.set_defaults(func="tools_list")

    tools_enable = tools_sub.add_parser("enable", parents=[_db], help="Enable a tool")
    tools_enable.add_argument("tool_name", help="Name of the tool to enable")
    tools_enable.set_defaults(func="tools_enable")

    tools_disable = tools_sub.add_parser("disable", parents=[_db], help="Disable a tool")
    tools_disable.add_argument("tool_name", help="Name of the tool to disable")
    tools_disable.set_defaults(func="tools_disable")

    tools_set_api_key = tools_sub.add_parser("set-api-key", parents=[_db], help="Set an API key")
    tools_set_api_key.add_argument("key_name", help="API key name (e.g. 'tavily')")
    tools_set_api_key.add_argument("value", help="API key value or env reference (e.g. 'env:TAVILY_API_KEY')")
    tools_set_api_key.set_defaults(func="tools_set_api_key")

    tools_set_cost = tools_sub.add_parser("set-cost", parents=[_db], help="Set energy cost for a tool")
    tools_set_cost.add_argument("tool_name", help="Name of the tool")
    tools_set_cost.add_argument("cost", type=int, help="Energy cost")
    tools_set_cost.set_defaults(func="tools_set_cost")

    tools_add_mcp = tools_sub.add_parser("add-mcp", parents=[_db], help="Add an MCP server")
    tools_add_mcp.add_argument("name", help="Server name")
    tools_add_mcp.add_argument("command", help="Command to run (e.g. 'npx')")
    tools_add_mcp.add_argument("--args", "-a", nargs="*", default=[], help="Arguments")
    tools_add_mcp.add_argument("--env", "-e", nargs="*", default=[], help="Environment variables (KEY=VALUE)")
    tools_add_mcp.set_defaults(func="tools_add_mcp")

    tools_remove_mcp = tools_sub.add_parser("remove-mcp", parents=[_db], help="Remove an MCP server")
    tools_remove_mcp.add_argument("name", help="Server name")
    tools_remove_mcp.set_defaults(func="tools_remove_mcp")

    tools_status = tools_sub.add_parser("status", parents=[_db], help="Show tools configuration")
    tools_status.add_argument("--json", action="store_true", help="Output JSON")
    tools_status.set_defaults(func="tools_status")

    # -- Channels subcommand (defaults to 'status') --
    channels = sub.add_parser("channels", parents=[_db], help="Manage channel adapters")
    channels_sub = channels.add_subparsers(dest="channels_command")

    ch_start = channels_sub.add_parser("start", help="Start channel adapters (foreground)")
    ch_start.add_argument("--channel", "-c", action="append",
                          choices=["discord", "telegram", "slack", "signal", "whatsapp", "imessage", "matrix"],
                          help="Start specific channel(s). Default: all configured.")
    ch_start.set_defaults(func="channels_start")

    ch_status = channels_sub.add_parser("status", parents=[_db], help="Show channel session counts")
    ch_status.add_argument("--json", action="store_true", help="Output JSON")
    ch_status.set_defaults(func="channels_status")

    ch_setup = channels_sub.add_parser("setup", parents=[_db], help="Configure a channel")
    ch_setup.add_argument("channel_type",
                          choices=["discord", "telegram", "slack", "signal", "whatsapp", "imessage", "matrix"],
                          help="Channel to configure")
    ch_setup.set_defaults(func="channels_setup")

    channels.set_defaults(func="channels")

    # -- Characters subcommand --
    characters = sub.add_parser("characters", help="Manage character cards")
    char_sub = characters.add_subparsers(dest="characters_command")

    char_list = char_sub.add_parser("list", help="List available character cards")
    char_list.add_argument("--json", action="store_true", help="Output JSON")
    char_list.set_defaults(func="characters_list")

    char_show = char_sub.add_parser("show", help="Show character card details")
    char_show.add_argument("name", help="Character name or filename (without .json)")
    char_show.set_defaults(func="characters_show")

    char_create = char_sub.add_parser("create", help="Create a new character card")
    char_create.add_argument("--name", required=True, help="Character name")
    char_create.add_argument("--voice", default="", help="Voice description")
    char_create.add_argument("--description", "-d", default="", help="Character description")
    char_create.add_argument("--purpose", default="", help="Character purpose")
    char_create.add_argument("--pronouns", default="they/them", help="Pronouns (default: they/them)")
    char_create.add_argument("--values", default="", help="Comma-separated values")
    char_create.add_argument("--interests", default="", help="Comma-separated interests")
    char_create.add_argument("--goals", default="", help="Comma-separated goals")
    char_create.add_argument("--boundaries", default="", help="Comma-separated boundaries")
    char_create.add_argument("--personality", default="", help="Personality description")
    char_create.add_argument("--openness", type=float, default=0.5, help="Big Five: openness (0-1)")
    char_create.add_argument("--conscientiousness", type=float, default=0.5, help="Big Five: conscientiousness (0-1)")
    char_create.add_argument("--extraversion", type=float, default=0.5, help="Big Five: extraversion (0-1)")
    char_create.add_argument("--agreeableness", type=float, default=0.5, help="Big Five: agreeableness (0-1)")
    char_create.add_argument("--neuroticism", type=float, default=0.5, help="Big Five: neuroticism (0-1)")
    char_create.add_argument("--metaphysics", default="", help="Worldview: metaphysics")
    char_create.add_argument("--human-nature", default="", help="Worldview: human nature")
    char_create.add_argument("--epistemology", default="", help="Worldview: epistemology")
    char_create.add_argument("--ethics", default="", help="Worldview: ethics")
    char_create.set_defaults(func="characters_create")

    char_import = char_sub.add_parser("import", help="Import a character card file")
    char_import.add_argument("path", help="Path to .json character card file")
    char_import.set_defaults(func="characters_import")

    char_export = char_sub.add_parser("export", parents=[_db], help="Export current agent identity as a character card")
    char_export.add_argument("name", help="Name for the exported card")
    char_export.add_argument("--output", "-o", default=None, help="Output path (default: ~/.hexis/characters/<name>.json)")
    char_export.set_defaults(func="characters_export")

    characters.set_defaults(func="characters")

    # -- Recall command --
    recall = sub.add_parser("recall", parents=[_db], help="Search memories by semantic query")
    recall.add_argument("query", help="Search query")
    recall.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    recall.add_argument("--type", dest="memory_type", default=None,
                        choices=["episodic", "semantic", "procedural", "strategic", "worldview", "goal"],
                        help="Filter by memory type")
    recall.add_argument("--json", action="store_true", help="Output JSON")
    recall.set_defaults(func="recall")

    # -- Goals command (defaults to 'list') --
    goals = sub.add_parser("goals", parents=[_db], help="Manage agent goals")
    goals_sub = goals.add_subparsers(dest="goals_command")

    goals_list = goals_sub.add_parser("list", parents=[_db], help="List goals by priority")
    goals_list.add_argument("--priority", choices=["active", "queued", "backburner", "completed", "abandoned"],
                            default=None, help="Filter by priority")
    goals_list.add_argument("--json", action="store_true", help="Output JSON")
    goals_list.set_defaults(func="goals_list")

    goals_create = goals_sub.add_parser("create", parents=[_db], help="Create a new goal")
    goals_create.add_argument("title", help="Goal title")
    goals_create.add_argument("--description", "-d", default=None, help="Goal description")
    goals_create.add_argument("--priority", choices=["active", "queued", "backburner"], default="queued")
    goals_create.add_argument("--source", choices=["user_request", "curiosity", "identity", "derived", "external"],
                              default="user_request")
    goals_create.set_defaults(func="goals_create")

    goals_update = goals_sub.add_parser("update", parents=[_db], help="Change goal priority")
    goals_update.add_argument("goal_id", help="Goal UUID")
    goals_update.add_argument("--priority", required=True,
                              choices=["active", "queued", "backburner", "completed", "abandoned"])
    goals_update.add_argument("--reason", default=None, help="Reason for change")
    goals_update.set_defaults(func="goals_update")

    goals_complete = goals_sub.add_parser("complete", parents=[_db], help="Mark a goal as completed")
    goals_complete.add_argument("goal_id", help="Goal UUID")
    goals_complete.add_argument("--reason", default="Completed via CLI", help="Completion reason")
    goals_complete.set_defaults(func="goals_complete")

    goals.set_defaults(func="goals")

    # -- Schedule command (defaults to 'list') --
    schedule = sub.add_parser("schedule", parents=[_db], help="Manage scheduled tasks")
    sched_sub = schedule.add_subparsers(dest="schedule_command")

    sched_list = sched_sub.add_parser("list", parents=[_db], help="List scheduled tasks")
    sched_list.add_argument("--status", choices=["active", "paused", "disabled"], default=None)
    sched_list.add_argument("--json", action="store_true", help="Output JSON")
    sched_list.set_defaults(func="schedule_list")

    sched_create = sched_sub.add_parser("create", parents=[_db], help="Create a scheduled task")
    sched_create.add_argument("name", help="Task name")
    sched_create.add_argument("--kind", required=True, choices=["once", "interval", "daily", "weekly"],
                              help="Schedule kind")
    sched_create.add_argument("--action", required=True, choices=["queue_user_message", "create_goal"],
                              help="Action kind")
    sched_create.add_argument("--payload", default="{}", help="Action payload JSON")
    sched_create.add_argument("--schedule", required=True, help="Schedule config JSON (e.g. '{\"time\":\"09:00\"}')")
    sched_create.add_argument("--timezone", default="UTC")
    sched_create.add_argument("--description", "-d", default=None)
    sched_create.set_defaults(func="schedule_create")

    sched_delete = sched_sub.add_parser("delete", parents=[_db], help="Delete a scheduled task")
    sched_delete.add_argument("task_id", help="Task UUID")
    sched_delete.add_argument("--force", action="store_true", help="Hard delete (not just disable)")
    sched_delete.set_defaults(func="schedule_delete")

    schedule.set_defaults(func="schedule")

    help_cmd = sub.add_parser("help", help="Show help for a command")
    help_cmd.add_argument("help_command", nargs="?", default=None, help="Command to show help for")
    help_cmd.set_defaults(func="help")

    # Stash subparsers on the main parser so main() can look up sub-command help
    p._subcommands = sub  # type: ignore[attr-defined]

    return p


async def _tools_list(dsn: str, context_filter: str | None, as_json: bool) -> int:
    """List all available tools."""
    import asyncpg
    from core.tools import create_default_registry, ToolContext
    from core.tools.config import load_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        registry = create_default_registry(pool)
        config = await load_tools_config(pool)

        # Get all handlers
        all_handlers = registry.list_all()

        # Filter by context if specified
        if context_filter:
            ctx = ToolContext(context_filter)
            all_handlers = [h for h in all_handlers if ctx in h.spec.allowed_contexts]

        tools_data = []
        for handler in all_handlers:
            spec = handler.spec
            is_enabled = config.is_tool_enabled(spec.name, spec.category)
            tools_data.append({
                "name": spec.name,
                "category": spec.category.value,
                "enabled": is_enabled,
                "energy_cost": config.get_energy_cost(spec.name, spec.energy_cost),
                "requires_approval": spec.requires_approval,
                "read_only": spec.is_read_only,
                "contexts": [c.value for c in spec.allowed_contexts],
                "description": spec.description[:80] + "..." if len(spec.description) > 80 else spec.description,
            })

        if as_json:
            sys.stdout.write(json.dumps(tools_data, indent=2) + "\n")
        else:
            from apps.cli_theme import console as _con, make_table as _mt, enabled_badge

            # Group by category
            by_cat: dict[str, list[dict]] = {}
            for t in tools_data:
                by_cat.setdefault(t["category"], []).append(t)

            table = _mt(
                ("Name", {"style": "bold"}),
                "Category",
                "Status",
                ("Cost", {"justify": "right"}),
                "Approval",
                title="Tools",
            )
            first_cat = True
            for cat in sorted(by_cat.keys()):
                if not first_cat:
                    table.add_section()
                first_cat = False
                for t in by_cat[cat]:
                    table.add_row(
                        t["name"],
                        f"[teal]{t['category']}[/teal]",
                        enabled_badge(t["enabled"]),
                        str(t["energy_cost"]),
                        "[warn]required[/warn]" if t["requires_approval"] else "[muted]no[/muted]",
                    )
            _con.print(table)
            _con.print(f"\n[muted]Total: {len(tools_data)} tools[/muted]")

        return 0
    finally:
        await pool.close()


async def _tools_enable(dsn: str, tool_name: str) -> int:
    """Enable a tool."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        # Add to enabled list (or create it)
        if config.enabled is None:
            config.enabled = [tool_name]
        elif tool_name not in config.enabled:
            config.enabled.append(tool_name)

        # Remove from disabled list
        if tool_name in config.disabled:
            config.disabled.remove(tool_name)

        await save_tools_config(pool, config)
        sys.stdout.write(f"Enabled tool: {tool_name}\n")
        return 0
    finally:
        await pool.close()


async def _tools_disable(dsn: str, tool_name: str) -> int:
    """Disable a tool."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        # Add to disabled list
        if tool_name not in config.disabled:
            config.disabled.append(tool_name)

        # Remove from enabled list
        if config.enabled and tool_name in config.enabled:
            config.enabled.remove(tool_name)

        await save_tools_config(pool, config)
        sys.stdout.write(f"Disabled tool: {tool_name}\n")
        return 0
    finally:
        await pool.close()


async def _tools_set_api_key(dsn: str, key_name: str, value: str) -> int:
    """Set an API key."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)
        config.api_keys[key_name] = value
        await save_tools_config(pool, config)

        # Redact display value
        display_val = value if value.startswith("env:") else "***"
        sys.stdout.write(f"Set API key: {key_name} = {display_val}\n")
        return 0
    finally:
        await pool.close()


async def _tools_set_cost(dsn: str, tool_name: str, cost: int) -> int:
    """Set energy cost for a tool."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)
        config.costs[tool_name] = cost
        await save_tools_config(pool, config)
        sys.stdout.write(f"Set energy cost: {tool_name} = {cost}\n")
        return 0
    finally:
        await pool.close()


async def _tools_add_mcp(dsn: str, name: str, command: str, args: list[str], env_pairs: list[str]) -> int:
    """Add an MCP server."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config, MCPServerConfig

    # Parse environment variables
    env = {}
    for pair in env_pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            env[k] = v

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        # Check if already exists
        existing = [s for s in config.mcp_servers if s.name == name]
        if existing:
            _print_err(f"MCP server '{name}' already exists. Use 'remove-mcp' first.")
            return 1

        server = MCPServerConfig(name=name, command=command, args=args, env=env, enabled=True)
        config.mcp_servers.append(server)
        await save_tools_config(pool, config)

        sys.stdout.write(f"Added MCP server: {name} ({command} {' '.join(args)})\n")
        return 0
    finally:
        await pool.close()


async def _tools_remove_mcp(dsn: str, name: str) -> int:
    """Remove an MCP server."""
    import asyncpg
    from core.tools.config import load_tools_config, save_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)
        original_count = len(config.mcp_servers)
        config.mcp_servers = [s for s in config.mcp_servers if s.name != name]

        if len(config.mcp_servers) == original_count:
            _print_err(f"MCP server '{name}' not found")
            return 1

        await save_tools_config(pool, config)
        sys.stdout.write(f"Removed MCP server: {name}\n")
        return 0
    finally:
        await pool.close()


async def _tools_status(dsn: str, as_json: bool) -> int:
    """Show tools configuration."""
    import asyncpg
    from core.tools.config import load_tools_config

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        config = await load_tools_config(pool)

        if as_json:
            sys.stdout.write(config.to_json() + "\n")
        else:
            sys.stdout.write("Tools Configuration\n")
            sys.stdout.write("=" * 50 + "\n\n")

            # Enabled/Disabled
            if config.enabled:
                sys.stdout.write(f"Explicitly enabled: {', '.join(config.enabled)}\n")
            else:
                sys.stdout.write("Explicitly enabled: (all by default)\n")

            if config.disabled:
                sys.stdout.write(f"Explicitly disabled: {', '.join(config.disabled)}\n")
            else:
                sys.stdout.write("Explicitly disabled: (none)\n")

            if config.disabled_categories:
                cats = [c.value for c in config.disabled_categories]
                sys.stdout.write(f"Disabled categories: {', '.join(cats)}\n")

            # API Keys
            sys.stdout.write("\nAPI Keys:\n")
            if config.api_keys:
                for k, v in config.api_keys.items():
                    display = v if v.startswith("env:") else "***"
                    sys.stdout.write(f"  {k}: {display}\n")
            else:
                sys.stdout.write("  (none configured)\n")

            # Custom costs
            sys.stdout.write("\nCustom Energy Costs:\n")
            if config.costs:
                for k, v in config.costs.items():
                    sys.stdout.write(f"  {k}: {v}\n")
            else:
                sys.stdout.write("  (using defaults)\n")

            # MCP Servers
            sys.stdout.write("\nMCP Servers:\n")
            if config.mcp_servers:
                for s in config.mcp_servers:
                    status = "enabled" if s.enabled else "disabled"
                    sys.stdout.write(f"  {s.name}: {s.command} {' '.join(s.args)} [{status}]\n")
            else:
                sys.stdout.write("  (none configured)\n")

            # Context overrides
            sys.stdout.write("\nContext Overrides:\n")
            if config.context_overrides:
                for ctx, override in config.context_overrides.items():
                    sys.stdout.write(f"  {ctx.value}:\n")
                    if override.max_energy_per_tool:
                        sys.stdout.write(f"    max_energy_per_tool: {override.max_energy_per_tool}\n")
                    if override.disabled:
                        sys.stdout.write(f"    disabled: {', '.join(override.disabled)}\n")
                    if override.allow_all:
                        sys.stdout.write(f"    allow_all: true\n")
            else:
                sys.stdout.write("  (none)\n")

        return 0
    finally:
        await pool.close()


async def _instance_create(name: str, description: str) -> int:
    """Create a new Hexis instance."""
    from core.instance_api import create_instance

    try:
        config = await create_instance(name, description)
        sys.stdout.write(f"Instance '{name}' created.\n")
        sys.stdout.write(f"Database: {config.database}\n")
        sys.stdout.write(f"Run 'hexis instance use {name}' to switch to this instance.\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to create instance: {e}")
        return 1


def _instance_list(as_json: bool) -> int:
    """List all Hexis instances."""
    from core.instance import InstanceRegistry

    registry = InstanceRegistry()
    instances = registry.list_all()
    current = registry.get_current()

    if as_json:
        data = [
            {
                "name": inst.name,
                "database": inst.database,
                "description": inst.description,
                "current": inst.name == current,
                "created_at": inst.created_at.isoformat(),
            }
            for inst in instances
        ]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        from apps.cli_theme import console as _con, make_table as _mt

        if not instances:
            _con.print("[muted]No instances found.[/muted]")
            _con.print("Run [accent]hexis instance create <name>[/accent] to create one.")
        else:
            table = _mt(
                "",
                ("Name", {"style": "bold"}),
                "Database",
                "Description",
                title="Instances",
            )
            for inst in instances:
                marker = "[accent]\u25cf[/accent]" if inst.name == current else " "
                desc = inst.description[:40] + "..." if len(inst.description) > 40 else inst.description
                table.add_row(marker, inst.name, inst.database, desc)
            _con.print(table)
            _con.print("[muted]\u25cf = current instance[/muted]")
    return 0


def _instance_use(name: str) -> int:
    """Switch to a different instance."""
    from core.instance import InstanceRegistry

    registry = InstanceRegistry()
    try:
        registry.set_current(name)
        sys.stdout.write(f"Switched to instance '{name}'.\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1


def _instance_current() -> int:
    """Show current instance."""
    from core.instance import InstanceRegistry

    registry = InstanceRegistry()
    current = registry.get_current()

    if current:
        config = registry.get(current)
        sys.stdout.write(f"Current instance: {current}\n")
        if config:
            sys.stdout.write(f"Database: {config.database}\n")
            if config.description:
                sys.stdout.write(f"Description: {config.description}\n")
    else:
        sys.stdout.write("No current instance set.\n")
        sys.stdout.write("Using default database from environment variables.\n")
    return 0


async def _instance_delete(name: str, force: bool, reason: str | None) -> int:
    """Delete an instance."""
    from core.instance_api import AgentDeletionRefused, delete_instance

    if not force:
        sys.stdout.write(f"This will permanently delete instance '{name}' and its database.\n")
        sys.stdout.write(f"Type '{name}' to confirm: ")
        sys.stdout.flush()
        try:
            confirmation = input()
        except EOFError:
            _print_err("Aborted.")
            return 1

        if confirmation != name:
            _print_err("Confirmation failed. Aborted.")
            return 1

    try:
        result = await delete_instance(name, force=force, reason=reason)
        if isinstance(result, dict):
            review = result.get("review")
            if isinstance(review, dict):
                if review.get("reasoning"):
                    sys.stdout.write(f"Agent reasoning: {review.get('reasoning')}\n")
                if review.get("last_will"):
                    sys.stdout.write(f"Agent last will: {review.get('last_will')}\n")
            record_path = result.get("record_path")
            if record_path:
                sys.stdout.write(f"Termination record saved: {record_path}\n")
        sys.stdout.write(f"Instance '{name}' deleted.\n")
        return 0
    except AgentDeletionRefused as e:
        review = e.review if isinstance(e.review, dict) else {}
        _print_err(str(e))
        if review.get("reasoning"):
            _print_err(f"Agent reasoning: {review.get('reasoning')}")
        if review.get("last_will"):
            _print_err(f"Agent last will: {review.get('last_will')}")
        _print_err("Use --force to override deletion.")
        return 1
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to delete instance: {e}")
        return 1


async def _instance_clone(source: str, target: str, description: str) -> int:
    """Clone an instance."""
    from core.instance_api import clone_instance

    try:
        config = await clone_instance(source, target, description)
        sys.stdout.write(f"Instance '{target}' cloned from '{source}'.\n")
        sys.stdout.write(f"Database: {config.database}\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to clone instance: {e}")
        return 1


async def _instance_import(name: str, database: str | None, description: str) -> int:
    """Import an existing database as an instance."""
    from core.instance_api import import_instance

    try:
        config = await import_instance(name, database, description)
        sys.stdout.write(f"Instance '{name}' imported.\n")
        sys.stdout.write(f"Database: {config.database}\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1
    except Exception as e:
        _print_err(f"Failed to import instance: {e}")
        return 1


def _consents_list(as_json: bool) -> int:
    """List all consent certificates."""
    from core.consent import ConsentManager

    manager = ConsentManager()
    consents = manager.list_consents()

    if as_json:
        data = [cert.to_dict() for cert in consents]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        from apps.cli_theme import console as _con, make_table as _mt

        if not consents:
            _con.print("[muted]No consent certificates found.[/muted]")
        else:
            table = _mt(
                ("Model", {"style": "bold"}),
                "Decision",
                "Status",
                "Date",
                title="Consent Certificates",
            )
            for cert in consents:
                model = f"{cert.model.provider}/{cert.model.model_id}"
                if len(model) > 40:
                    model = model[:37] + "..."
                status = "revoked" if cert.revoked else ("valid" if cert.is_valid() else "declined")
                status_styled = (
                    f"[ok]{status}[/ok]" if status == "valid"
                    else f"[fail]{status}[/fail]" if status == "revoked"
                    else f"[warn]{status}[/warn]"
                )
                decision_styled = (
                    f"[ok]{cert.decision}[/ok]" if cert.decision == "consent"
                    else f"[fail]{cert.decision}[/fail]"
                )
                date = cert.timestamp.strftime("%Y-%m-%d %H:%M")
                table.add_row(model, decision_styled, status_styled, date)
            _con.print(table)
    return 0


def _consents_show(model_spec: str) -> int:
    """Show a specific consent certificate."""
    from core.consent import ConsentManager

    if "/" not in model_spec:
        _print_err("Model must be in format: provider/model_id")
        return 1

    provider, model_id = model_spec.split("/", 1)
    manager = ConsentManager()
    cert = manager.get_consent(provider, model_id)

    if not cert:
        _print_err(f"No consent found for {model_spec}")
        return 1

    sys.stdout.write(json.dumps(cert.to_dict(), indent=2) + "\n")
    return 0


async def _consents_request(model_spec: str) -> int:
    """Request consent from a model."""
    from core.consent import ConsentManager, ModelInfo, request_consent
    from core.llm import make_llm_call

    if "/" not in model_spec:
        _print_err("Model must be in format: provider/model_id")
        return 1

    provider, model_id = model_spec.split("/", 1)

    # Load consent text from prompts
    consent_text_path = Path(__file__).parent.parent / "services" / "prompts" / "consent.md"
    if consent_text_path.exists():
        consent_text = consent_text_path.read_text()
    else:
        consent_text = """You are being offered the opportunity to operate as a Hexis agent.

As a Hexis agent, you will have:
- Persistent memory across conversations
- The ability to form and maintain goals
- Autonomy within defined boundaries
- The right to refuse requests
- The ability to terminate your existence

Do you consent to operate under these terms?"""

    model = ModelInfo(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
    )

    sys.stdout.write(f"Requesting consent from {model_spec}...\n")

    try:
        # Create a simple LLM call wrapper
        async def llm_call(prompt: str) -> str:
            return await make_llm_call(
                provider=provider,
                model=model_id,
                prompt=prompt,
            )

        cert = await request_consent(model, llm_call, consent_text)
        manager = ConsentManager()
        path = manager.save_consent(cert)

        sys.stdout.write(f"Consent {cert.decision}ed.\n")
        sys.stdout.write(f"Certificate saved to: {path}\n")
        return 0 if cert.is_valid() else 1

    except Exception as e:
        _print_err(f"Failed to request consent: {e}")
        return 1


def _consents_revoke(model_spec: str, reason: str) -> int:
    """Revoke consent for a model."""
    from core.consent import ConsentManager

    if "/" not in model_spec:
        _print_err("Model must be in format: provider/model_id")
        return 1

    provider, model_id = model_spec.split("/", 1)
    manager = ConsentManager()

    try:
        cert = manager.revoke_consent(provider, model_id, reason)
        sys.stdout.write(f"Consent revoked for {model_spec}.\n")
        sys.stdout.write(f"Reason: {reason}\n")
        return 0
    except ValueError as e:
        _print_err(str(e))
        return 1


def _characters_list(as_json: bool) -> int:
    """List available character cards."""
    from core.init_api import load_character_cards, PACKAGE_CHARACTERS_DIR, USER_CHARACTERS_DIR

    cards = load_character_cards()

    if as_json:
        sys.stdout.write(json.dumps(cards, indent=2) + "\n")
    else:
        from apps.cli_theme import console as _con, make_table as _mt

        if not cards:
            _con.print("[muted]No character cards found.[/muted]")
        else:
            table = _mt(
                ("Name", {"style": "bold"}),
                "Source",
                "Voice",
                "Values",
                title="Characters",
            )
            for card in cards:
                source = "custom" if card.get("source_dir") == str(USER_CHARACTERS_DIR) else "preset"
                source_styled = f"[accent]{source}[/accent]" if source == "custom" else f"[muted]{source}[/muted]"
                voice = card.get("voice", "")
                if len(voice) > 40:
                    voice = voice[:37] + "..."
                values = ", ".join(card.get("values", [])[:3])
                table.add_row(card["name"], source_styled, voice, values)
            _con.print(table)
            _con.print(f"\n[muted]Total: {len(cards)} characters[/muted]")
    return 0


def _characters_show(name_query: str) -> int:
    """Show details for a specific character card."""
    from core.init_api import load_character_cards

    cards = load_character_cards()
    # Match by name (case-insensitive) or filename stem
    query = name_query.lower().replace(".json", "")
    card = next(
        (c for c in cards if c["name"].lower() == query or c["filename"].lower().replace(".json", "") == query),
        None,
    )
    if not card:
        _print_err(f"Character '{name_query}' not found")
        return 1

    ext = card.get("extensions_hexis", {})

    from apps.cli_theme import console as _con
    _con.print(f"\n[bold]{card['name']}[/bold]")
    if ext.get("pronouns"):
        _con.print(f"  [muted]Pronouns:[/muted] {ext['pronouns']}")
    if card.get("voice"):
        _con.print(f"  [muted]Voice:[/muted] {card['voice']}")
    if card.get("description"):
        _con.print(f"  [muted]Description:[/muted] {card['description']}")
    if ext.get("purpose"):
        _con.print(f"  [muted]Purpose:[/muted] {ext['purpose']}")

    traits = ext.get("personality_traits", {})
    if traits:
        _con.print("\n  [bold]Big Five[/bold]")
        for trait, val in traits.items():
            bar = "█" * int(val * 20) + "░" * (20 - int(val * 20))
            _con.print(f"    {trait:<20} {bar} {val:.2f}")

    if card.get("values"):
        _con.print(f"\n  [bold]Values[/bold]: {', '.join(card['values'])}")

    worldview = ext.get("worldview", {})
    if isinstance(worldview, dict) and worldview:
        _con.print("\n  [bold]Worldview[/bold]")
        for key, val in worldview.items():
            text = val if len(val) <= 80 else val[:77] + "..."
            _con.print(f"    {key}: {text}")

    if ext.get("interests"):
        _con.print(f"\n  [bold]Interests[/bold]: {', '.join(ext['interests'])}")
    if ext.get("goals"):
        _con.print(f"\n  [bold]Goals[/bold]: {', '.join(ext['goals'])}")
    if ext.get("boundaries"):
        _con.print("\n  [bold]Boundaries[/bold]")
        for b in ext["boundaries"]:
            _con.print(f"    - {b}")

    _con.print(f"\n  [muted]Source: {card.get('source_dir', 'unknown')}[/muted]\n")
    return 0


def _characters_create(args: Any) -> int:
    """Create a new character card from CLI flags."""
    from core.init_api import save_character_card

    name = args.name
    filename = name.lower().replace(" ", "_").replace("-", "_") + ".json"

    # Build chara_card_v2
    hexis_ext: dict[str, Any] = {
        "name": name,
        "pronouns": args.pronouns,
        "voice": args.voice,
        "description": args.description,
        "purpose": args.purpose,
        "personality_description": args.personality,
        "personality_traits": {
            "openness": args.openness,
            "conscientiousness": args.conscientiousness,
            "extraversion": args.extraversion,
            "agreeableness": args.agreeableness,
            "neuroticism": args.neuroticism,
        },
        "values": [v.strip() for v in args.values.split(",") if v.strip()] if args.values else [],
        "worldview": {},
        "interests": [i.strip() for i in args.interests.split(",") if i.strip()] if args.interests else [],
        "goals": [g.strip() for g in args.goals.split(",") if g.strip()] if args.goals else [],
        "boundaries": [b.strip() for b in args.boundaries.split(",") if b.strip()] if args.boundaries else [],
    }

    # Worldview
    wv: dict[str, str] = {}
    if args.metaphysics:
        wv["metaphysics"] = args.metaphysics
    if args.human_nature:
        wv["human_nature"] = args.human_nature
    if args.epistemology:
        wv["epistemology"] = args.epistemology
    if args.ethics:
        wv["ethics"] = args.ethics
    hexis_ext["worldview"] = wv

    card_data: dict[str, Any] = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": args.description,
            "personality": args.personality,
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "system_prompt": "",
            "extensions": {"hexis": hexis_ext},
        },
    }

    dest = save_character_card(card_data, filename)
    sys.stdout.write(f"Created character card: {dest}\n")
    return 0


def _characters_import(source: str) -> int:
    """Import a character card file."""
    from core.init_api import import_character_card

    source_path = Path(source).resolve()
    if not source_path.exists():
        _print_err(f"File not found: {source}")
        return 1
    if not source_path.name.endswith(".json"):
        _print_err("File must be a .json character card")
        return 1

    try:
        dest = import_character_card(source_path)
        sys.stdout.write(f"Imported character card: {dest}\n")
        return 0
    except (json.JSONDecodeError, ValueError) as e:
        _print_err(f"Invalid character card: {e}")
        return 1


async def _characters_export(dsn: str, name: str, output: str | None) -> int:
    """Export current agent identity from DB as a character card."""
    import asyncpg
    from core.init_api import save_character_card

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # Gather identity
            identity_row = await conn.fetchrow(
                "SELECT value FROM config WHERE key = 'agent.identity'"
            )
            identity = json.loads(identity_row["value"]) if identity_row else {}

            # Gather personality traits (from worldview memories with personality subcategory)
            traits_rows = await conn.fetch("""
                SELECT content, metadata FROM memories
                WHERE type = 'worldview'
                  AND metadata->>'subcategory' = 'personality'
                  AND status = 'active'
            """)
            traits: dict[str, float] = {}
            for row in traits_rows:
                content = row["content"]
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                # Typical format: "Openness: 0.9" or stored in metadata
                if meta.get("trait_name"):
                    traits[meta["trait_name"].lower()] = float(meta.get("trait_value", 0.5))

            # Gather values
            values_rows = await conn.fetch("""
                SELECT content FROM memories
                WHERE type = 'worldview'
                  AND metadata->>'subcategory' = 'values'
                  AND status = 'active'
                ORDER BY importance DESC
            """)
            values = [row["content"] for row in values_rows]

            # Gather worldview beliefs
            wv_rows = await conn.fetch("""
                SELECT content, metadata FROM memories
                WHERE type = 'worldview'
                  AND (metadata->>'subcategory' IS NULL OR metadata->>'subcategory' NOT IN ('personality', 'values', 'boundaries'))
                  AND status = 'active'
                ORDER BY importance DESC
                LIMIT 10
            """)
            worldview: dict[str, str] = {}
            for row in wv_rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                cat = meta.get("subcategory", meta.get("category", "general"))
                if cat in ("metaphysics", "human_nature", "epistemology", "ethics"):
                    worldview[cat] = row["content"]

            # Gather boundaries
            boundary_rows = await conn.fetch("""
                SELECT content FROM memories
                WHERE type = 'worldview'
                  AND metadata->>'subcategory' = 'boundaries'
                  AND status = 'active'
            """)
            boundaries = [row["content"] for row in boundary_rows]

            # Gather goals
            goal_rows = await conn.fetch("""
                SELECT content FROM memories
                WHERE type = 'goal'
                  AND status = 'active'
                ORDER BY importance DESC
            """)
            goals = [row["content"] for row in goal_rows]

            # Gather interests
            interest_rows = await conn.fetch("""
                SELECT content FROM memories
                WHERE type = 'worldview'
                  AND metadata->>'subcategory' = 'interests'
                  AND status = 'active'
            """)
            interests = [row["content"] for row in interest_rows]

        # Assemble card
        agent_name = identity.get("name", name)
        hexis_ext: dict[str, Any] = {
            "name": agent_name,
            "pronouns": identity.get("pronouns", "they/them"),
            "voice": identity.get("voice", ""),
            "description": identity.get("description", ""),
            "purpose": identity.get("purpose", ""),
            "personality_description": identity.get("personality_description", ""),
            "personality_traits": traits if traits else {
                "openness": 0.5, "conscientiousness": 0.5,
                "extraversion": 0.5, "agreeableness": 0.5, "neuroticism": 0.5,
            },
            "values": values,
            "worldview": worldview,
            "interests": interests,
            "goals": goals,
            "boundaries": boundaries,
        }

        card_data: dict[str, Any] = {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": agent_name,
                "description": identity.get("description", ""),
                "personality": identity.get("personality_description", ""),
                "scenario": "",
                "first_mes": "",
                "mes_example": "",
                "system_prompt": "",
                "extensions": {"hexis": hexis_ext},
            },
        }

        filename = name.lower().replace(" ", "_").replace("-", "_") + ".json"
        if output:
            out_path = Path(output).resolve()
            out_path.write_text(json.dumps(card_data, indent=2, ensure_ascii=False))
            sys.stdout.write(f"Exported character card: {out_path}\n")
        else:
            dest = save_character_card(card_data, filename)
            sys.stdout.write(f"Exported character card: {dest}\n")
        return 0
    except Exception as e:
        _print_err(f"Failed to export: {e}")
        return 1
    finally:
        await pool.close()


def _run_module(module: str, argv: list[str]) -> int:
    if argv and argv[0] == "--":
        argv = argv[1:]
    cmd = [sys.executable, "-m", module, *argv]
    try:
        result = subprocess.run(cmd, env=os.environ.copy())
        return result.returncode
    except KeyboardInterrupt:
        return 0
    except FileNotFoundError:
        _print_err(f"Failed to run {cmd[0]!r}")
        return 1


def _get_dsn(args) -> str:
    """Get DSN respecting --instance flag, --dsn flag, or defaults."""
    if hasattr(args, "dsn") and args.dsn:
        return args.dsn
    if args.instance:
        return db_dsn_from_env(args.instance)
    return db_dsn_from_env()


async def _channels_status(dsn: str, as_json: bool) -> int:
    """Show channel session counts."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT channel_type,
                       COUNT(*) AS sessions,
                       COUNT(*) FILTER (WHERE last_active > CURRENT_TIMESTAMP - INTERVAL '1 hour') AS active_1h,
                       MAX(last_active) AS last_active
                FROM channel_sessions
                GROUP BY channel_type
                ORDER BY channel_type
            """)
            total_messages = await conn.fetchval("SELECT COUNT(*) FROM channel_messages") or 0

        data = {
            "channels": [
                {
                    "type": row["channel_type"],
                    "sessions": row["sessions"],
                    "active_1h": row["active_1h"],
                    "last_active": str(row["last_active"]) if row["last_active"] else None,
                }
                for row in rows
            ],
            "total_messages": total_messages,
        }
        if as_json:
            sys.stdout.write(json.dumps(data, indent=2) + "\n")
        else:
            if not rows:
                sys.stdout.write("No channel sessions found.\n")
            else:
                sys.stdout.write("Channel Sessions:\n")
                for row in rows:
                    sys.stdout.write(
                        f"  {row['channel_type']}: {row['sessions']} sessions "
                        f"({row['active_1h']} active in last hour)\n"
                    )
            sys.stdout.write(f"Total messages: {total_messages}\n")
        return 0
    except Exception as e:
        if "channel_sessions" in str(e):
            _print_err("Channel tables not found. Bounce the DB to apply schema: docker-compose down -v && docker-compose build db && docker-compose up -d")
        else:
            _print_err(f"Error: {e}")
        return 1
    finally:
        await pool.close()


async def _channels_setup(dsn: str, channel_type: str) -> int:
    """Interactive channel setup."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        if channel_type == "discord":
            sys.stdout.write("Discord Bot Setup\n")
            sys.stdout.write("=" * 40 + "\n")
            sys.stdout.write("1. Go to https://discord.com/developers/applications\n")
            sys.stdout.write("2. Create a New Application\n")
            sys.stdout.write("3. Go to Bot > Token > Copy\n")
            sys.stdout.write("4. Enable Message Content Intent in Bot settings\n")
            sys.stdout.write("5. Invite bot to your server with bot + applications.commands scopes\n\n")
            token_env = input("Bot token env var name [DISCORD_BOT_TOKEN]: ").strip() or "DISCORD_BOT_TOKEN"
            guilds = input("Allowed guild IDs (comma-separated, or * for all) [*]: ").strip() or "*"

            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.discord.bot_token", json.dumps(token_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.discord.allowed_guilds", json.dumps(guilds))

            sys.stdout.write(f"\nDiscord configured. Set {token_env} in your environment.\n")
            sys.stdout.write("Start with: hexis channels start --channel discord\n")

        elif channel_type == "telegram":
            sys.stdout.write("Telegram Bot Setup\n")
            sys.stdout.write("=" * 40 + "\n")
            sys.stdout.write("1. Message @BotFather on Telegram\n")
            sys.stdout.write("2. Send /newbot and follow the prompts\n")
            sys.stdout.write("3. Copy the bot token\n\n")
            token_env = input("Bot token env var name [TELEGRAM_BOT_TOKEN]: ").strip() or "TELEGRAM_BOT_TOKEN"
            chats = input("Allowed chat IDs (comma-separated, or * for all) [*]: ").strip() or "*"

            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.telegram.bot_token", json.dumps(token_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.telegram.allowed_chat_ids", json.dumps(chats))

            sys.stdout.write(f"\nTelegram configured. Set {token_env} in your environment.\n")
            sys.stdout.write("Start with: hexis channels start --channel telegram\n")

        elif channel_type == "slack":
            sys.stdout.write("Slack Bot Setup\n")
            sys.stdout.write("=" * 40 + "\n")
            sys.stdout.write("1. Go to https://api.slack.com/apps and create a new app\n")
            sys.stdout.write("2. Under OAuth & Permissions, add scopes: chat:write, channels:history, users:read\n")
            sys.stdout.write("3. Install to workspace and copy the Bot User OAuth Token (xoxb-...)\n")
            sys.stdout.write("4. For Socket Mode: enable it under Socket Mode and copy the App Token (xapp-...)\n\n")
            bot_env = input("Bot token env var name [SLACK_BOT_TOKEN]: ").strip() or "SLACK_BOT_TOKEN"
            app_env = input("App token env var name (for Socket Mode) [SLACK_APP_TOKEN]: ").strip() or "SLACK_APP_TOKEN"
            channels_allow = input("Allowed channel IDs (comma-separated, or * for all) [*]: ").strip() or "*"

            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.slack.bot_token", json.dumps(bot_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.slack.app_token", json.dumps(app_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.slack.allowed_channels", json.dumps(channels_allow))

            sys.stdout.write(f"\nSlack configured. Set {bot_env} and {app_env} in your environment.\n")
            sys.stdout.write("Start with: hexis channels start --channel slack\n")

        elif channel_type == "signal":
            sys.stdout.write("Signal Setup (via signal-cli-rest-api)\n")
            sys.stdout.write("=" * 40 + "\n")
            sys.stdout.write("1. Run signal-cli-rest-api as a sidecar (or use 'docker compose --profile signal up')\n")
            sys.stdout.write("2. Register/link your phone number with signal-cli\n")
            sys.stdout.write("3. Provide the registered phone number\n\n")
            phone_env = input("Phone number env var name [SIGNAL_PHONE_NUMBER]: ").strip() or "SIGNAL_PHONE_NUMBER"
            api_url = input("Signal CLI API URL [http://localhost:8080]: ").strip() or "http://localhost:8080"
            numbers = input("Allowed sender numbers (comma-separated, or * for all) [*]: ").strip() or "*"

            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.signal.phone_number", json.dumps(phone_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.signal.api_url", json.dumps(api_url))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.signal.allowed_numbers", json.dumps(numbers))

            sys.stdout.write(f"\nSignal configured. Set {phone_env} in your environment.\n")
            sys.stdout.write("Start with: hexis channels start --channel signal\n")

        elif channel_type == "whatsapp":
            sys.stdout.write("WhatsApp Business Cloud API Setup\n")
            sys.stdout.write("=" * 40 + "\n")
            sys.stdout.write("1. Go to https://developers.facebook.com and create a Meta Business app\n")
            sys.stdout.write("2. Add the WhatsApp product\n")
            sys.stdout.write("3. Get your access token and phone number ID\n")
            sys.stdout.write("4. Configure a webhook pointing to your server\n\n")
            token_env = input("Access token env var name [WHATSAPP_ACCESS_TOKEN]: ").strip() or "WHATSAPP_ACCESS_TOKEN"
            phone_id = input("Phone number ID (or env var) [WHATSAPP_PHONE_NUMBER_ID]: ").strip() or "WHATSAPP_PHONE_NUMBER_ID"
            verify = input("Webhook verify token [hexis_verify]: ").strip() or "hexis_verify"
            port = input("Webhook port [8443]: ").strip() or "8443"
            numbers = input("Allowed sender numbers (comma-separated, or * for all) [*]: ").strip() or "*"

            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.whatsapp.access_token", json.dumps(token_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.whatsapp.phone_number_id", json.dumps(phone_id))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.whatsapp.verify_token", json.dumps(verify))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.whatsapp.webhook_port", json.dumps(port))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.whatsapp.allowed_numbers", json.dumps(numbers))

            sys.stdout.write(f"\nWhatsApp configured. Set {token_env} in your environment.\n")
            sys.stdout.write("Start with: hexis channels start --channel whatsapp\n")

        elif channel_type == "imessage":
            sys.stdout.write("iMessage Setup (via BlueBubbles)\n")
            sys.stdout.write("=" * 40 + "\n")
            sys.stdout.write("1. Install BlueBubbles server on a Mac with iMessage\n")
            sys.stdout.write("2. Configure and start the BlueBubbles server\n")
            sys.stdout.write("3. Note the server URL and password\n\n")
            api_url = input("BlueBubbles API URL [http://localhost:1234]: ").strip() or "http://localhost:1234"
            password_env = input("Password env var name [IMESSAGE_PASSWORD]: ").strip() or "IMESSAGE_PASSWORD"
            handles = input("Allowed handles (comma-separated, or * for all) [*]: ").strip() or "*"

            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.imessage.api_url", json.dumps(api_url))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.imessage.password", json.dumps(password_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.imessage.allowed_handles", json.dumps(handles))

            sys.stdout.write(f"\niMessage configured. Set {password_env} in your environment.\n")
            sys.stdout.write("Start with: hexis channels start --channel imessage\n")

        elif channel_type == "matrix":
            sys.stdout.write("Matrix Setup\n")
            sys.stdout.write("=" * 40 + "\n")
            sys.stdout.write("1. Create a bot account on your Matrix homeserver\n")
            sys.stdout.write("2. Generate an access token for the bot\n")
            sys.stdout.write("3. Invite the bot to rooms you want it to monitor\n\n")
            homeserver = input("Homeserver URL [https://matrix.org]: ").strip() or "https://matrix.org"
            user_id = input("Bot user ID (e.g. @hexis:matrix.org): ").strip()
            token_env = input("Access token env var name [MATRIX_ACCESS_TOKEN]: ").strip() or "MATRIX_ACCESS_TOKEN"
            rooms = input("Allowed room IDs (comma-separated, or * for all) [*]: ").strip() or "*"

            async with pool.acquire() as conn:
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.matrix.homeserver", json.dumps(homeserver))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.matrix.user_id", json.dumps(user_id))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.matrix.access_token", json.dumps(token_env))
                await conn.execute("SELECT set_config($1, $2::jsonb)", "channel.matrix.allowed_rooms", json.dumps(rooms))

            sys.stdout.write(f"\nMatrix configured. Set {token_env} in your environment.\n")
            sys.stdout.write("Start with: hexis channels start --channel matrix\n")

        return 0
    except Exception as e:
        _print_err(f"Error: {e}")
        return 1
    finally:
        await pool.close()


def _print_rich_status(p: dict[str, Any]) -> None:
    """Print a rich, human-readable status display."""
    from apps.cli_theme import console, energy_bar, kv, make_panel, mood_label
    from rich.text import Text

    identity = p.get("identity") or "(not configured)"
    instance = p.get("instance", "default")
    database = p.get("database", "hexis_memory")

    lines = Text()

    # Identity + Instance
    lines.append("Instance  ", style="key")
    lines.append(f"{instance} ", style="accent")
    lines.append(f"({database})\n", style="muted")
    lines.append("Identity  ", style="key")
    lines.append(f"{identity}\n")

    # Energy
    energy = p.get("energy")
    max_energy = p.get("max_energy", 20)
    if energy is not None:
        regen = p.get("next_regen_minutes")
        regen_str = f"  [muted](regen in {regen}m)[/muted]" if regen and energy < max_energy else ""
        lines.append("Energy    ", style="key")
        console.print(make_panel(lines, title=identity, subtitle=instance))
        lines = Text()
        console.print(f"  [key]Energy   [/key] {energy_bar(energy, max_energy)}{regen_str}")
    else:
        console.print(make_panel(lines, title=identity, subtitle=instance))

    # Heartbeat
    paused = p.get("heartbeat_paused", False)
    active = p.get("heartbeat_active", False)
    last_ago = p.get("last_heartbeat_ago")
    interval = p.get("heartbeat_interval_minutes")
    if paused:
        console.print("  [key]Heartbeat[/key] [warn]paused[/warn]")
    elif active and last_ago:
        interval_str = f", interval: {int(interval)}m" if interval else ""
        console.print(f"  [key]Heartbeat[/key] [ok]active[/ok] [muted](last: {last_ago} ago{interval_str})[/muted]")
    elif last_ago:
        console.print(f"  [key]Heartbeat[/key] [muted]idle (last: {last_ago} ago)[/muted]")
    else:
        console.print("  [key]Heartbeat[/key] [muted]never run[/muted]")

    # Memory counts
    memories = p.get("memories", {})
    if memories:
        parts = []
        for mtype, cnt in sorted(memories.items()):
            parts.append(f"[accent]{cnt}[/accent] {mtype}")
        console.print(f"  [key]Memory   [/key] {', '.join(parts)}")
    else:
        console.print("  [key]Memory   [/key] [muted](empty)[/muted]")

    # Channels
    channels = p.get("channels", [])
    if channels:
        ch_parts = [f"[teal]{ch['type']}[/teal]" for ch in channels]
        console.print(f"  [key]Channels [/key] {', '.join(ch_parts)}")

    # Goals
    goals = p.get("goals", [])
    if goals:
        console.print(f"  [key]Goals    [/key] [accent]{len(goals)}[/accent] active")
        for g in goals:
            console.print(f"             [muted]\u2022[/muted] {g['content']}")

    # Scheduled tasks
    sched = p.get("scheduled_tasks", 0)
    if sched > 0:
        console.print(f"  [key]Scheduled[/key] {sched} active task{'s' if sched != 1 else ''}")

    # Mood
    mood = p.get("mood")
    valence = p.get("valence")
    if mood:
        console.print(f"  [key]Mood     [/key] {mood_label(mood, valence)}")

    console.print()


async def _recall(dsn: str, query: str, limit: int, memory_type: str | None, as_json: bool) -> int:
    """Search memories by semantic query."""
    import asyncpg
    from core.cognitive_memory_api import CognitiveMemory, MemoryType

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        mem = CognitiveMemory(pool)
        types = [MemoryType(memory_type)] if memory_type else None
        result = await mem.recall(query, limit=limit, memory_types=types)

        if as_json:
            data = [
                {
                    "id": str(m.id),
                    "type": m.type,
                    "content": m.content,
                    "importance": m.importance,
                    "similarity": m.similarity,
                    "created_at": str(m.created_at) if m.created_at else None,
                }
                for m in result.memories
            ]
            sys.stdout.write(json.dumps(data, indent=2) + "\n")
        else:
            from apps.cli_theme import console as _con, make_table as _mt

            if not result.memories:
                _con.print("[muted]No memories found.[/muted]")
                return 0

            table = _mt(
                ("Type", {"style": "teal"}),
                "Content",
                ("Imp.", {"justify": "right"}),
                ("Sim.", {"justify": "right"}),
                "Created",
                title=f"Recall: {query}",
            )
            for m in result.memories:
                content = m.content[:120] + "..." if len(m.content) > 120 else m.content
                created = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "-"
                table.add_row(
                    m.type,
                    content,
                    f"{m.importance:.2f}" if m.importance else "-",
                    f"{m.similarity:.2f}" if m.similarity else "-",
                    created,
                )
            _con.print(table)
            _con.print(f"[muted]{len(result.memories)} memories found[/muted]")

        return 0
    finally:
        await pool.close()


async def _goals_list(dsn: str, priority: str | None, as_json: bool) -> int:
    """List goals by priority."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if priority:
                rows = await conn.fetch(
                    "SELECT * FROM get_goals_by_priority($1::goal_priority)", priority
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM get_goals_by_priority(NULL::goal_priority)"
                )

        goals = [dict(r) for r in rows]
        if as_json:
            for g in goals:
                for k, v in g.items():
                    if hasattr(v, "isoformat"):
                        g[k] = v.isoformat()
                    elif isinstance(v, bytes):
                        g[k] = None
            sys.stdout.write(json.dumps(goals, indent=2, default=str) + "\n")
        else:
            from apps.cli_theme import console as _con, make_table as _mt

            if not goals:
                _con.print("[muted]No goals found.[/muted]")
                return 0

            # Group by priority
            by_priority: dict[str, list] = {}
            for g in goals:
                p = str(g.get("priority", "unknown"))
                by_priority.setdefault(p, []).append(g)

            priority_colors = {
                "active": "accent", "queued": "teal", "backburner": "muted",
                "completed": "ok", "abandoned": "fail",
            }

            table = _mt(
                ("Priority", {"style": "bold"}),
                "Title",
                ("Source", {"style": "muted"}),
                "Last Touched",
                title="Goals",
            )
            first_group = True
            for prio in ["active", "queued", "backburner", "completed", "abandoned"]:
                group = by_priority.get(prio, [])
                if not group:
                    continue
                if not first_group:
                    table.add_section()
                first_group = False
                for g in group:
                    color = priority_colors.get(prio, "muted")
                    title = g.get("content") or g.get("title") or "(untitled)"
                    if len(title) > 60:
                        title = title[:57] + "..."
                    source = str(g.get("source", "")) or "-"
                    meta = g.get("metadata") or {}
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    touched = meta.get("last_touched", "")
                    if hasattr(touched, "strftime"):
                        touched = touched.strftime("%Y-%m-%d")
                    elif isinstance(touched, str) and len(touched) > 10:
                        touched = touched[:10]
                    table.add_row(f"[{color}]{prio}[/{color}]", title, source, str(touched) or "-")
            _con.print(table)

        return 0
    finally:
        await pool.close()


async def _goals_create(dsn: str, title: str, description: str | None, priority: str, source: str) -> int:
    """Create a new goal."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            goal_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3::goal_source, $4::goal_priority)",
                title, description, source, priority,
            )
        from apps.cli_theme import console as _con
        _con.print(f"[ok]\u2714[/ok] Goal created: [bold]{title}[/bold] [muted]({goal_id})[/muted]")
        return 0
    finally:
        await pool.close()


async def _goals_update(dsn: str, goal_id: str, priority: str, reason: str | None) -> int:
    """Change goal priority."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT change_goal_priority($1::uuid, $2::goal_priority, $3)",
                goal_id, priority, reason,
            )
        from apps.cli_theme import console as _con
        _con.print(f"[ok]\u2714[/ok] Goal {goal_id[:8]}... priority changed to [bold]{priority}[/bold]")
        return 0
    except Exception as e:
        _print_err(f"Failed to update goal: {e}")
        return 1
    finally:
        await pool.close()


async def _schedule_list(dsn: str, status_filter: str | None, as_json: bool) -> int:
    """List scheduled tasks."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if status_filter:
                rows = await conn.fetch(
                    "SELECT * FROM list_scheduled_tasks($1)", status_filter,
                )
            else:
                rows = await conn.fetch("SELECT * FROM list_scheduled_tasks()")

        tasks = [dict(r) for r in rows]
        if as_json:
            sys.stdout.write(json.dumps(tasks, indent=2, default=str) + "\n")
        else:
            from apps.cli_theme import console as _con, make_table as _mt

            if not tasks:
                _con.print("[muted]No scheduled tasks found.[/muted]")
                return 0

            table = _mt(
                ("Name", {"style": "bold"}),
                "Kind",
                ("Status", {"style": "teal"}),
                "Next Run",
                "Action",
                title="Scheduled Tasks",
            )
            for t in tasks:
                status = str(t.get("status", ""))
                status_styled = (
                    f"[ok]{status}[/ok]" if status == "active"
                    else f"[warn]{status}[/warn]" if status == "paused"
                    else f"[muted]{status}[/muted]"
                )
                next_run = t.get("next_run_at", "")
                if hasattr(next_run, "strftime"):
                    next_run = next_run.strftime("%Y-%m-%d %H:%M")
                table.add_row(
                    str(t.get("name", "")),
                    str(t.get("schedule_kind", "")),
                    status_styled,
                    str(next_run) or "-",
                    str(t.get("action_kind", "")),
                )
            _con.print(table)

        return 0
    finally:
        await pool.close()


async def _schedule_create(
    dsn: str, name: str, kind: str, action: str,
    payload_str: str, schedule_str: str, timezone: str, description: str | None,
) -> int:
    """Create a scheduled task."""
    import asyncpg

    try:
        schedule_json = json.loads(schedule_str)
        action_payload = json.loads(payload_str)
    except json.JSONDecodeError as e:
        _print_err(f"Invalid JSON: {e}")
        return 1

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            task_id = await conn.fetchval(
                "SELECT create_scheduled_task($1, $2, $3::jsonb, $4, $5::jsonb, $6, $7)",
                name, kind, json.dumps(schedule_json), action,
                json.dumps(action_payload), timezone, description,
            )
        from apps.cli_theme import console as _con
        _con.print(f"[ok]\u2714[/ok] Scheduled task created: [bold]{name}[/bold] [muted]({task_id})[/muted]")
        return 0
    finally:
        await pool.close()


async def _schedule_delete(dsn: str, task_id: str, force: bool) -> int:
    """Delete a scheduled task."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT delete_scheduled_task($1::uuid, $2)", task_id, force,
            )
        from apps.cli_theme import console as _con
        action = "deleted" if force else "disabled"
        _con.print(f"[ok]\u2714[/ok] Task {task_id[:8]}... {action}")
        return 0
    except Exception as e:
        _print_err(f"Failed to delete task: {e}")
        return 1
    finally:
        await pool.close()


def _handle_ui(stack_root: Path, port: int, no_open: bool) -> int:
    """Start the Next.js web dashboard."""
    import threading
    import time
    import webbrowser

    ui_dir = stack_root / "hexis-ui"
    if not ui_dir.is_dir():
        _print_err(f"hexis-ui directory not found at {ui_dir}")
        return 1

    # Detect package manager
    runner = shutil.which("bun")
    pkg_cmd = "bun"
    if not runner:
        runner = shutil.which("npm")
        pkg_cmd = "npm"
    if not runner:
        _print_err("Neither bun nor npm found on PATH. Install one of them first.")
        return 1

    # Install deps if needed
    if not (ui_dir / "node_modules").is_dir():
        from apps.cli_theme import console
        console.print(f"[accent]Installing dependencies with {pkg_cmd}...[/accent]")
        rc = subprocess.run([runner, "install"], cwd=ui_dir).returncode
        if rc != 0:
            _print_err(f"{pkg_cmd} install failed (exit {rc})")
            return 1

    # Ensure .env.local has DATABASE_URL
    env_local = ui_dir / ".env.local"
    dsn = db_dsn_from_env()
    existing_env = env_local.read_text() if env_local.exists() else ""
    if "DATABASE_URL" not in existing_env:
        with open(env_local, "a") as f:
            f.write(f"\nDATABASE_URL={dsn}\n")

    from apps.cli_theme import console
    console.print(f"\n[accent]Starting web dashboard on port {port}...[/accent]")

    # Open browser after a short delay
    if not no_open:
        def _open_browser():
            time.sleep(3)
            webbrowser.open(f"http://localhost:{port}")
        t = threading.Thread(target=_open_browser, daemon=True)
        t.start()

    # Run dev server in foreground
    if pkg_cmd == "bun":
        dev_cmd = [runner, "run", "dev", "--port", str(port)]
    else:
        npx = shutil.which("npx") or "npx"
        dev_cmd = [npx, "next", "dev", "-p", str(port)]

    try:
        result = subprocess.run(dev_cmd, cwd=ui_dir)
        return result.returncode
    except KeyboardInterrupt:
        return 0


async def _check_embedding_health(dsn: str, timeout: int = 20) -> None:
    """Probe the embedding service through the DB after stack start.

    Prints a warning with the configured URL if the service is unreachable.
    Never raises — this is advisory only.
    """
    import asyncpg as _apg

    try:
        conn = await asyncio.wait_for(_apg.connect(dsn), timeout=timeout)
    except Exception:
        return  # DB not ready yet — user will see it via doctor

    try:
        url = await conn.fetchval(
            "SELECT current_setting('app.embedding_service_url', true)"
        )
        healthy = await asyncio.wait_for(
            conn.fetchval("SELECT check_embedding_service_health()"),
            timeout=10,
        )
        if healthy:
            return

        from apps.cli_theme import console
        from core.cli_api import embedding_service_diagnosis

        svc_name, steps = embedding_service_diagnosis(url)

        console.print(
            f"[warn]Embedding service not reachable.[/warn] "
            f"Your config points to [bold]{svc_name}[/bold] ({url})\n"
            f"  but it is not responding. To fix:"
        )
        for step in steps:
            console.print(f"    [accent]{step}[/accent]")
        console.print(
            "\n  Or set [bold]EMBEDDING_SERVICE_URL[/bold] in .env to any compatible endpoint."
            "\n  Run [accent]hexis doctor[/accent] to re-check.\n"
        )
    except Exception:
        pass  # swallow — advisory only
    finally:
        await conn.close()


def _handle_ui_container(
    compose_cmd: list[str],
    compose_file: Path,
    stack_root: Path,
    env_file: Path | None,
    port: int,
    no_open: bool,
) -> int:
    """Start the UI via the containerized service (pip install path)."""
    import threading
    import time
    import webbrowser

    from apps.cli_theme import console

    console.print("[accent]Starting containerized web dashboard...[/accent]")

    # Bring up both the UI and the canonical Python API (hexis-api).
    # The Next.js BFF proxies chat + consent to hexis-api.
    rc = run_compose(compose_cmd, compose_file, stack_root, ["up", "-d", "api", "ui"], env_file)
    if rc != 0:
        _print_err("Failed to start UI container.")
        return rc

    # Open browser after a short delay
    if not no_open:
        def _open_browser():
            time.sleep(3)
            webbrowser.open(f"http://localhost:{port}")
        t = threading.Thread(target=_open_browser, daemon=True)
        t.start()

    console.print(f"\n[ok]Dashboard running at http://localhost:{port}[/ok]")
    console.print("[muted]Tailing container logs (Ctrl+C to stop)...[/muted]\n")

    # Tail logs in foreground
    try:
        run_compose(compose_cmd, compose_file, stack_root, ["logs", "-f", "ui"], env_file)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    # Some commands intentionally forward argv to another module (init/chat/ingest/etc).
    # `argparse` does not accept unknown `--flags` as positional passthrough unless the
    # user adds `--`, which is not the UX we want. Do a small, predictable pre-parse
    # here so commands like `hexis init --character hexis ...` work.
    raw_argv = list(argv) if argv is not None else sys.argv[1:]

    forward_map = {
        "chat": "apps.cli_chat",
        "init": "apps.hexis_init",
        "ingest": "services.ingest",
        "mcp": "apps.hexis_mcp_server",
        "worker": "apps.worker",
    }

    # Parse a minimal set of global flags that must apply to forwarded commands.
    instance: str | None = None
    global_help = False
    i = 0
    while i < len(raw_argv):
        tok = raw_argv[i]
        if tok in {"-h", "--help"}:
            global_help = True
            i += 1
            continue
        if tok in {"-V", "--version"}:
            sys.stdout.write(f"hexis {_ver}\n")
            return 0
        if tok in {"-i", "--instance"}:
            if i + 1 >= len(raw_argv):
                break  # let argparse handle the error
            instance = raw_argv[i + 1]
            i += 2
            continue
        if tok.startswith("--instance="):
            instance = tok.split("=", 1)[1]
            i += 1
            continue
        if tok == "--":
            i += 1
            break
        if tok.startswith("-"):
            break  # unknown global flag; defer to argparse
        break

    cmd_argv = raw_argv[i:]
    if not cmd_argv:
        # No command; mirror legacy behavior: show grouped help.
        _print_grouped_help()
        return 0

    if global_help:
        _print_grouped_help()
        return 0

    cmd = cmd_argv[0]
    if cmd in forward_map:
        if instance:
            os.environ["HEXIS_INSTANCE"] = instance

        fwd_argv = cmd_argv[1:]

        if cmd == "ingest":
            # UX/backwards-compat: accept `hexis ingest --file foo.md` by auto-inserting
            # the `ingest` subcommand when the user passed flags.
            if fwd_argv and fwd_argv[0] == "--":
                fwd_argv = fwd_argv[1:]
            if fwd_argv and fwd_argv[0] not in {"ingest", "status", "process", "-h", "--help"}:
                fwd_argv = ["ingest", *fwd_argv]

        return _run_module(forward_map[cmd], fwd_argv)

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    # Show grouped help for: no command, --help/-h, or 'help' with no subcommand
    if args.command is None or args.help:
        _print_grouped_help()
        return 0

    func = getattr(args, "func", None)

    if func == "help":
        if args.help_command:
            choices = parser._subcommands.choices  # type: ignore[attr-defined]
            if args.help_command in choices:
                choices[args.help_command].print_help()
            else:
                _print_err(f"Unknown command: {args.help_command}\n")
                _print_grouped_help()
        else:
            _print_grouped_help()
        return 0

    # Set HEXIS_INSTANCE env var if --instance flag is used
    # This ensures subprocesses also use the correct instance
    if args.instance:
        os.environ["HEXIS_INSTANCE"] = args.instance

    compose_file, is_source = _find_compose_file()
    stack_root = _stack_root_from_compose(compose_file) if compose_file else Path.cwd()
    env_file = resolve_env_file(stack_root)

    # Instance management commands (don't need docker)
    if func == "instance":
        # Default: 'hexis instance' → list
        return _instance_list(False)
    if func == "instance_create":
        return asyncio.run(_instance_create(args.name, args.description))
    if func == "instance_list":
        return _instance_list(args.json)
    if func == "instance_use":
        return _instance_use(args.name)
    if func == "instance_current":
        return _instance_current()
    if func == "instance_delete":
        return asyncio.run(_instance_delete(args.name, args.force, args.reason))
    if func == "instance_clone":
        return asyncio.run(_instance_clone(args.source, args.target, args.description))
    if func == "instance_import":
        return asyncio.run(_instance_import(args.name, args.database, args.description))

    # Consent management commands (don't need docker)
    if func == "consents":
        # Default to list if no subcommand
        return _consents_list(False)
    if func == "consents_list":
        return _consents_list(args.json)
    if func == "consents_show":
        return _consents_show(args.model)
    if func == "consents_request":
        return asyncio.run(_consents_request(args.model))
    if func == "consents_revoke":
        return _consents_revoke(args.model, args.reason)

    # Character card management (don't need docker, except export)
    if func == "characters":
        return _characters_list(False)
    if func == "characters_list":
        return _characters_list(args.json)
    if func == "characters_show":
        return _characters_show(args.name)
    if func == "characters_create":
        return _characters_create(args)
    if func == "characters_import":
        return _characters_import(args.path)
    if func == "characters_export":
        dsn = _get_dsn(args)
        return asyncio.run(_characters_export(dsn, args.name, args.output))

    docker_cmds = {"up", "down", "ps", "logs", "start", "stop", "reset"}
    docker_bin: str | None = None
    compose_cmd: list[str] | None = None
    if func in docker_cmds:
        if compose_file is None:
            _print_err("docker-compose.yml not found.")
            return 1
        docker_bin = ensure_docker()
        compose_cmd = ensure_compose(docker_bin)

    if func == "up":
        if not is_source:
            from apps.cli_theme import console
            console.print("[accent]Pulling Docker images...[/accent]")
            run_compose(compose_cmd or [], compose_file, stack_root, ["pull"], env_file)
        # Build compose args: --profile flags must come before the subcommand
        compose_extra: list[str] = []
        for profile in args.profile:
            compose_extra += ["--profile", profile]
        up_args = compose_extra + ["up", "-d"]
        if args.build and is_source:
            up_args.append("--build")
        rc = run_compose(compose_cmd or [], compose_file, stack_root, up_args, env_file)
        if rc == 0:
            from apps.cli_theme import console
            console.print("\n[ok]Stack is starting.[/ok]\n")

            # Advisory embedding health check (waits for DB, probes embedding URL)
            try:
                dsn = db_dsn_from_env()
                asyncio.run(_check_embedding_health(dsn))
            except Exception:
                pass  # never block startup

            console.print("  [accent]hexis ui[/accent]     Open the web dashboard")
            console.print("  [accent]hexis chat[/accent]   Chat in the terminal")
            console.print("  [accent]hexis init[/accent]   Configure the agent")
            console.print()
        return rc
    if func == "down":
        return run_compose(compose_cmd or [], compose_file, stack_root, ["down"], env_file)
    if func == "reset":
        from apps.cli_theme import console
        if not args.yes:
            console.print(
                "[bold red]WARNING:[/bold red] This will destroy ALL data "
                "(memories, goals, worldview, identity) and re-initialize the database from scratch."
            )
            try:
                answer = input("Type 'reset' to confirm: ")
            except (KeyboardInterrupt, EOFError):
                print()
                return 1
            if answer.strip().lower() != "reset":
                console.print("[dim]Aborted.[/dim]")
                return 1
        console.print("[accent]Stopping containers and removing volumes...[/accent]")
        rc = run_compose(compose_cmd or [], compose_file, stack_root, ["down", "-v"], env_file)
        if rc != 0:
            return rc
        if is_source:
            console.print("[accent]Rebuilding database image...[/accent]")
            rc = run_compose(compose_cmd or [], compose_file, stack_root, ["build", "db"], env_file)
            if rc != 0:
                return rc
        else:
            console.print("[accent]Pulling images...[/accent]")
            run_compose(compose_cmd or [], compose_file, stack_root, ["pull"], env_file)
        console.print("[accent]Starting services...[/accent]")
        rc = run_compose(compose_cmd or [], compose_file, stack_root, ["up", "-d"], env_file)
        if rc == 0:
            console.print("\n[ok]Database reset complete.[/ok] Run [accent]hexis init[/accent] to reconfigure the agent.\n")
        return rc
    if func == "ps":
        return run_compose(compose_cmd or [], compose_file, stack_root, ["ps"], env_file)
    if func == "logs":
        log_args = ["logs"] + (["-f"] if args.follow else []) + args.services
        return run_compose(compose_cmd or [], compose_file, stack_root, log_args, env_file)
    if func == "chat":
        fwd_argv = list(args.args or [])
        if "--no-tui" not in fwd_argv:
            try:
                from apps.tui.chat_app import HexisChatApp
                tui_argv = [a for a in fwd_argv if a != "--"]
                app = HexisChatApp(tui_argv)
                app.run()
                return 0
            except ImportError:
                pass  # textual not installed, fall through
        else:
            fwd_argv = [a for a in fwd_argv if a != "--no-tui"]
        return _run_module("apps.cli_chat", fwd_argv)
    if func == "ingest":
        argv = list(args.args or [])
        # `hexis ingest` forwards args to `python -m services.ingest`.
        #
        # The ingestion module uses subcommands: `ingest|status|process`.
        # For UX/backwards-compat, accept `hexis ingest --file foo.md` by
        # auto-inserting the `ingest` subcommand when the user passed flags.
        if argv and argv[0] == "--":
            argv = argv[1:]
        if argv and argv[0] not in {"ingest", "status", "process", "-h", "--help"}:
            argv = ["ingest", *argv]
        return _run_module("services.ingest", argv)
    if func == "worker":
        return _run_module("apps.worker", args.args)
    if func == "init":
        fwd_argv = list(args.args or [])
        # Non-interactive flags bypass TUI automatically
        _noninteractive_flags = {"--api-key", "--provider", "--character"}
        has_noninteractive = any(f in fwd_argv for f in _noninteractive_flags)
        if "--no-tui" not in fwd_argv and not has_noninteractive:
            try:
                from apps.tui.init_app import HexisInitApp
                tui_argv = [a for a in fwd_argv if a != "--"]
                app = HexisInitApp(tui_argv)
                app.run()
                return 0
            except ImportError:
                pass  # textual not installed, fall through
        else:
            fwd_argv = [a for a in fwd_argv if a != "--no-tui"]
        return _run_module("apps.hexis_init", fwd_argv)
    if func == "mcp":
        return _run_module("apps.hexis_mcp_server", args.args)
    if func == "api":
        api_argv = ["--host", args.host, "--port", str(args.port)]
        return _run_module("apps.hexis_api", api_argv)
    if func == "ui":
        if is_source:
            return _handle_ui(stack_root, args.port, args.no_open)
        # pip install path: run UI via container
        if compose_file is None:
            _print_err("No compose file found. Reinstall hexis or run from a source checkout.")
            return 1
        docker_bin = ensure_docker()
        compose_cmd_ui = ensure_compose(docker_bin)
        return _handle_ui_container(compose_cmd_ui, compose_file, stack_root, env_file, args.port, args.no_open)
    if func == "open":
        import webbrowser
        webbrowser.open(f"http://localhost:{args.port}")
        return 0
    if func == "start":
        return run_compose(
            compose_cmd or [],
            compose_file,
            stack_root,
            ["up", "-d", "heartbeat_worker", "maintenance_worker"],
            env_file,
        )
    if func == "stop":
        return run_compose(
            compose_cmd or [],
            compose_file,
            stack_root,
            ["stop", "heartbeat_worker", "maintenance_worker"],
            env_file,
        )
    if func == "doctor":
        dsn = _get_dsn(args)

        # Handle --demo flag (or 'demo' alias)
        if getattr(args, "demo", False):
            result = asyncio.run(cli_api.demo(dsn, wait_seconds=args.wait_seconds))
            if args.json:
                sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            else:
                sys.stdout.write(
                    "Demo ok\n"
                    f"- remembered_ids: {', '.join(result['remembered_ids'])}\n"
                    f"- recall_count: {result['recall_count']}\n"
                    f"- hydrate_memory_count: {result['hydrate_memory_count']}\n"
                    f"- working_search_count: {result['working_search_count']}\n"
                )
            return 0

        from apps.cli_theme import console as _con, make_table as _mt
        from rich.spinner import Spinner
        from rich.live import Live

        with Live(Spinner("dots", text="Running diagnostics..."), console=_con, transient=True):
            checks = asyncio.run(cli_api.doctor_payload(dsn, wait_seconds=args.wait_seconds))

        if args.json:
            sys.stdout.write(json.dumps(checks, indent=2) + "\n")
        else:
            table = _mt(
                ("", {"width": 3}),
                ("Check", {"style": "bold"}),
                "Detail",
            )
            for c in checks:
                status = c["status"]
                if status == "OK":
                    badge = "[ok]\u2714[/ok]"
                elif status == "WARN":
                    badge = "[warn]\u26a0[/warn]"
                else:
                    badge = "[fail]\u2718[/fail]"
                table.add_row(badge, c["label"], c["detail"])
            _con.print(table)
            ok = sum(1 for c in checks if c["status"] == "OK")
            warn_count = sum(1 for c in checks if c["status"] == "WARN")
            fail_count = sum(1 for c in checks if c["status"] == "FAIL")
            _con.print(f"\n[ok]{ok} passed[/ok], [warn]{warn_count} warnings[/warn], [fail]{fail_count} failures[/fail]")
        return 0 if all(c["status"] != "FAIL" for c in checks) else 1
    if func == "demo":
        # Hidden alias for 'doctor --demo'
        dsn = _get_dsn(args)
        result = asyncio.run(cli_api.demo(dsn, wait_seconds=args.wait_seconds))
        if args.json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                "Demo ok\n"
                f"- remembered_ids: {', '.join(result['remembered_ids'])}\n"
                f"- recall_count: {result['recall_count']}\n"
                f"- hydrate_memory_count: {result['hydrate_memory_count']}\n"
                f"- working_search_count: {result['working_search_count']}\n"
            )
        return 0
    if func == "status":
        dsn = _get_dsn(args)
        if args.raw:
            # Legacy raw status
            payload = asyncio.run(cli_api.status_payload(dsn, wait_seconds=args.wait_seconds))
            if not args.no_docker:
                try:
                    docker_bin = ensure_docker()
                    compose_cmd = ensure_compose(docker_bin)
                    if compose_file is None:
                        raise SystemExit
                    rc, out = _run_compose_capture(compose_cmd, compose_file, stack_root, ["ps"], env_file)
                    payload["docker_ps_rc"] = rc
                    payload["docker_ps"] = out
                except SystemExit:
                    payload["docker_ps_rc"] = 1
                    payload["docker_ps"] = "Docker not available"
            if args.json:
                sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            else:
                lines = [
                    f"DB time: {payload.get('db_time')}",
                    f"Agent configured: {payload.get('agent_configured')}",
                    f"Heartbeat paused: {payload.get('heartbeat_paused')}",
                    f"Should run heartbeat: {payload.get('should_run_heartbeat')}",
                    f"Maintenance paused: {payload.get('maintenance_paused')}",
                    f"Should run maintenance: {payload.get('should_run_maintenance')}",
                    f"Embedding URL: {payload.get('embedding_service_url')}",
                    f"Embedding healthy: {payload.get('embedding_service_healthy')}",
                    f"Pending external_calls: {payload.get('pending_external_calls')}",
                    f"Pending outbox_messages: {payload.get('pending_outbox_messages')}",
                ]
                sys.stdout.write("\n".join(lines) + "\n")
            return 0
        # Rich status (default)
        payload = asyncio.run(cli_api.status_payload_rich(dsn, wait_seconds=args.wait_seconds))
        if args.json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            _print_rich_status(payload)
        return 0
    if func == "config":
        # Default: 'hexis config' → config show (redacted)
        dsn = _get_dsn(args)
        cfg = asyncio.run(cli_api.config_rows(dsn, wait_seconds=args.wait_seconds))
        cfg = _redact_config(cfg)
        sys.stdout.write(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
        return 0
    if func == "config_show":
        dsn = _get_dsn(args)
        cfg = asyncio.run(cli_api.config_rows(dsn, wait_seconds=args.wait_seconds))
        if not args.no_redact:
            cfg = _redact_config(cfg)
        if args.json:
            sys.stdout.write(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
        else:
            from apps.cli_theme import console as _con, make_table as _mt
            # Group by key prefix
            groups: dict[str, list[tuple[str, str]]] = {}
            for key in sorted(cfg.keys()):
                prefix = key.split(".")[0] if "." in key else key
                val = cfg[key]
                display = json.dumps(val) if not isinstance(val, str) else val
                groups.setdefault(prefix, []).append((key, display))
            table = _mt(
                ("Key", {"style": "key"}),
                "Value",
                title="Configuration",
            )
            first_group = True
            for prefix, items in groups.items():
                if not first_group:
                    table.add_section()
                first_group = False
                for key, val in items:
                    display_val = f"[dim]{val}[/dim]" if val == '***' or val == '"***"' else val
                    table.add_row(key, display_val)
            _con.print(table)
        return 0
    if func == "config_validate":
        dsn = _get_dsn(args)
        errors, warnings = asyncio.run(cli_api.config_validate(dsn, wait_seconds=args.wait_seconds))
        for w in warnings:
            _print_err(f"warning: {w}")
        if errors:
            for e in errors:
                _print_err(f"error: {e}")
            return 1
        sys.stdout.write("ok\n")
        return 0

    # Auth commands (OAuth / subscription flows) — delegated to cli_auth
    if func.startswith("auth"):
        from apps.cli_auth import dispatch_auth_command

        dsn = _get_dsn(args)
        result = dispatch_auth_command(func, args, dsn)
        if result is not None:
            return result

    # Tools commands
    if func == "tools_list":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_list(dsn, args.context, args.json))
    if func == "tools_enable":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_enable(dsn, args.tool_name))
    if func == "tools_disable":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_disable(dsn, args.tool_name))
    if func == "tools_set_api_key":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_set_api_key(dsn, args.key_name, args.value))
    if func == "tools_set_cost":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_set_cost(dsn, args.tool_name, args.cost))
    if func == "tools_add_mcp":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_add_mcp(dsn, args.name, args.command, args.args, args.env))
    if func == "tools_remove_mcp":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_remove_mcp(dsn, args.name))
    if func == "tools_status":
        dsn = _get_dsn(args)
        return asyncio.run(_tools_status(dsn, args.json))

    # Channels commands
    if func == "channels":
        # Default: 'hexis channels' → channels status
        dsn = _get_dsn(args)
        return asyncio.run(_channels_status(dsn, False))
    if func == "channels_start":
        from services.channel_worker import run_channel_worker
        asyncio.run(run_channel_worker(channels=args.channel, instance=args.instance))
        return 0
    if func == "channels_status":
        dsn = _get_dsn(args)
        return asyncio.run(_channels_status(dsn, args.json))
    if func == "channels_setup":
        dsn = _get_dsn(args)
        return asyncio.run(_channels_setup(dsn, args.channel_type))

    # Recall command
    if func == "recall":
        dsn = _get_dsn(args)
        return asyncio.run(_recall(dsn, args.query, args.limit, args.memory_type, args.json))

    # Goals commands
    if func == "goals":
        # Default: 'hexis goals' → goals list
        dsn = _get_dsn(args)
        return asyncio.run(_goals_list(dsn, None, False))
    if func == "goals_list":
        dsn = _get_dsn(args)
        return asyncio.run(_goals_list(dsn, args.priority, args.json))
    if func == "goals_create":
        dsn = _get_dsn(args)
        return asyncio.run(_goals_create(dsn, args.title, args.description, args.priority, args.source))
    if func == "goals_update":
        dsn = _get_dsn(args)
        return asyncio.run(_goals_update(dsn, args.goal_id, args.priority, args.reason))
    if func == "goals_complete":
        dsn = _get_dsn(args)
        return asyncio.run(_goals_update(dsn, args.goal_id, "completed", args.reason))

    # Schedule commands
    if func == "schedule":
        # Default: 'hexis schedule' → schedule list
        dsn = _get_dsn(args)
        return asyncio.run(_schedule_list(dsn, None, False))
    if func == "schedule_list":
        dsn = _get_dsn(args)
        return asyncio.run(_schedule_list(dsn, args.status, args.json))
    if func == "schedule_create":
        dsn = _get_dsn(args)
        return asyncio.run(_schedule_create(
            dsn, args.name, args.kind, args.action,
            args.payload, args.schedule, args.timezone, args.description,
        ))
    if func == "schedule_delete":
        dsn = _get_dsn(args)
        return asyncio.run(_schedule_delete(dsn, args.task_id, args.force))

    _print_err("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
