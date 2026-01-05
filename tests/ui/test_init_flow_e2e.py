import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Generator

import asyncpg
import pytest
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from tests.utils import _db_dsn


pytestmark = pytest.mark.ui


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError(f"Port {port} did not open within {timeout}s")


@pytest.fixture(scope="module")
def ui_test_db() -> Generator[str, None, None]:
    temp_db = f"tmp_ui_{uuid.uuid4().hex}"
    admin_db = os.getenv("POSTGRES_ADMIN_DB", "postgres")
    schema_path = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    async def _setup() -> None:
        admin_conn = await asyncpg.connect(_db_dsn(admin_db))
        try:
            await admin_conn.execute(f'CREATE DATABASE "{temp_db}"')
        finally:
            await admin_conn.close()

        test_conn = await asyncpg.connect(_db_dsn(temp_db))
        try:
            await test_conn.execute(schema_sql)
        finally:
            await test_conn.close()

    async def _teardown() -> None:
        admin_conn = await asyncpg.connect(_db_dsn(admin_db))
        try:
            await admin_conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                temp_db,
            )
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{temp_db}"')
        finally:
            await admin_conn.close()

    try:
        asyncio.run(_setup())
    except Exception as exc:
        pytest.skip(f"Postgres unavailable for UI e2e tests: {exc}")
    yield temp_db
    try:
        asyncio.run(_teardown())
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_agent_state(ui_test_db: str) -> Generator[None, None, None]:
    keys = [
        "agent.is_configured",
        "agent.consent_status",
        "agent.consent_signature",
        "agent.consent_log_id",
        "agent.consent_recorded_at",
        "agent.consent_memory_ids",
        "agent.init_profile",
        "agent.mode",
        "agent.objectives",
        "agent.guardrails",
        "agent.initial_message",
        "agent.tools",
        "user.contact",
        "llm.heartbeat",
        "llm.chat",
    ]

    async def _reset() -> None:
        conn = await asyncpg.connect(_db_dsn(ui_test_db))
        try:
            await conn.execute("DELETE FROM config WHERE key = ANY($1::text[])", keys)
            await conn.execute("DELETE FROM consent_log")
            await conn.execute("UPDATE heartbeat_state SET is_paused = TRUE WHERE id = 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_reset())
    except Exception:
        pass
    yield
    try:
        asyncio.run(_reset())
    except Exception:
        pass


@pytest.fixture(scope="module")
def reflex_server(ui_test_db: str) -> Generator[str, None, None]:
    port = _find_free_port()
    backend_port = _find_free_port()
    env = os.environ.copy()
    env["POSTGRES_DB"] = ui_test_db
    env["FRONTEND_PORT"] = str(port)
    env["BACKEND_PORT"] = str(backend_port)
    env["HEXIS_TEST_CONSENT_DECISION"] = "consent"
    env["HEXIS_TEST_CONSENT_SIGNATURE"] = "ui-test-consent"

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "reflex",
            "run",
            "--frontend-port",
            str(port),
            "--backend-port",
            str(backend_port),
            "--backend-host",
            "127.0.0.1",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_port(port, timeout=120)
        _wait_for_port(backend_port, timeout=120)
        yield f"http://127.0.0.1:{port}"
    except Exception as exc:
        pytest.skip(f"Reflex server did not start: {exc}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture()
def driver() -> Generator[webdriver.Chrome, None, None]:
    options = webdriver.ChromeOptions()
    if (os.getenv("HEXIS_UI_HEADLESS") or "true").lower() in {"1", "true", "yes"}:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--disable-gpu")
    try:
        browser = webdriver.Chrome(options=options)
    except WebDriverException as exc:
        pytest.skip(f"Chrome driver not available: {exc}")
    yield browser
    browser.quit()


def _wait_id(driver: webdriver.Chrome, element_id: str, timeout: float = 20.0):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.ID, element_id)))


def _wait_any_id(driver: webdriver.Chrome, element_ids: list[str], timeout: float = 20.0):
    def _predicate(_driver):
        for element_id in element_ids:
            if _driver.find_elements(By.ID, element_id):
                return True
        return False

    WebDriverWait(driver, timeout).until(_predicate)


def _click_id(driver: webdriver.Chrome, element_id: str, timeout: float = 20.0):
    element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.ID, element_id)))
    element.click()


def _type_id(driver: webdriver.Chrome, element_id: str, value: str, timeout: float = 20.0):
    element = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.ID, element_id)))
    element.clear()
    element.send_keys(value)


def _fetch_config(conn: asyncpg.Connection, key: str):
    async def _get():
        value = await conn.fetchval("SELECT get_config($1)", key)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    return _get()


def test_persona_flow_with_questions(reflex_server: str, driver: webdriver.Chrome, ui_test_db: str):
    driver.get(reflex_server)
    _wait_id(driver, "init-step-mode")
    _click_id(driver, "mode-persona")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-identity")
    _type_id(driver, "agent-name", "Luna")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-personality-questions")
    _click_id(driver, "q1-socratic")
    _click_id(driver, "q2-match")
    _click_id(driver, "q3-direct")
    _click_id(driver, "q4-advisor")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-personality-confirm")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-relationship")
    _type_id(driver, "user-name", "Alex")
    _type_id(driver, "purpose-text", "Help me think through hard problems.")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-values")
    _type_id(driver, "values-text", "Honesty\nPrivacy")
    _type_id(driver, "boundaries-text", "No external sharing.")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-autonomy")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-capabilities")
    _type_id(driver, "tools-text", "web_search")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-review")
    _click_id(driver, "init-connect-consent")
    _wait_any_id(driver, ["consent-modal", "chat-view"], timeout=30)

    async def _assert_db():
        conn = await asyncpg.connect(_db_dsn(ui_test_db))
        try:
            deadline = time.monotonic() + 20
            mode = None
            profile = None
            while time.monotonic() < deadline:
                mode = await _fetch_config(conn, "agent.mode")
                profile = await _fetch_config(conn, "agent.init_profile")
                if mode and profile:
                    break
                await asyncio.sleep(0.5)

            is_configured = await _fetch_config(conn, "agent.is_configured")
            consent_status = await _fetch_config(conn, "agent.consent_status")
            consent_log_id = await _fetch_config(conn, "agent.consent_log_id")
            heartbeat_paused = await conn.fetchval("SELECT is_paused FROM heartbeat_state WHERE id = 1")
            tools = await _fetch_config(conn, "agent.tools")
            objectives = await _fetch_config(conn, "agent.objectives")
            guardrails = await _fetch_config(conn, "agent.guardrails")

            assert mode == "persona"
            assert profile["agent"]["name"] == "Luna"
            assert profile["user"]["name"] == "Alex"
            assert profile["relationship"]["purpose"] == "Help me think through hard problems."
            assert "Honesty" in profile["values"]
            assert "No external sharing." in profile["boundaries"]

            assert is_configured is True
            assert consent_status == "consent"
            assert consent_log_id is not None
            assert heartbeat_paused is False
            assert any(tool.get("name") == "web_search" for tool in tools)
            assert "Help me think through hard problems." in objectives
            assert "No external sharing." in guardrails
        finally:
            await conn.close()

    asyncio.run(_assert_db())


def test_persona_flow_skip_questions(reflex_server: str, driver: webdriver.Chrome):
    driver.get(reflex_server)
    _wait_id(driver, "init-step-mode")
    _click_id(driver, "mode-persona")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-identity")
    _type_id(driver, "agent-name", "Nova")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-personality-questions")
    _click_id(driver, "init-skip-questions")

    _wait_id(driver, "init-step-personality-manual")
    _type_id(driver, "personality-description", "You are curious and direct.")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-relationship")


def test_raw_flow_skips_identity(reflex_server: str, driver: webdriver.Chrome):
    driver.get(reflex_server)
    _wait_id(driver, "init-step-mode")
    _click_id(driver, "mode-raw")
    _click_id(driver, "init-continue")

    _wait_id(driver, "init-step-relationship")
    assert driver.find_elements(By.ID, "init-step-identity") == []
