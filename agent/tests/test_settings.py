import ast
import json
from pathlib import Path

import pytest

from agent import main, settings
from agent.settings import AgentError

ROOT = Path(__file__).resolve().parents[2]


def test_a_missing_key_is_one_clear_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(AgentError, match="OPENROUTER_API_KEY is not set"):
        main.model()


def test_a_real_environment_variable_beats_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / ".env"
    env_file.write_text("BACKEND_URL=http://from-the-file\nMODEL_NAME=from-the-file\n")
    monkeypatch.setenv("BACKEND_URL", "http://from-the-environment")
    monkeypatch.setenv("MODEL_NAME", "x")
    monkeypatch.delenv("MODEL_NAME")
    settings.load_environment(env_file)
    assert settings.backend_url() == "http://from-the-environment"
    assert settings.model_name() == "from-the-file"


def test_the_agents_client_uses_the_configured_address(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:8001")
    assert str(main.backend().base_url) == "http://127.0.0.1:8001"
    monkeypatch.delenv("BACKEND_URL")
    assert str(main.backend().base_url) == "http://localhost:8000"


def test_the_dev_and_e2e_setups_name_the_backend_address():
    scripts = json.loads((ROOT / "package.json").read_text())["scripts"]
    assert "--port 8000" in scripts["dev:backend"]
    assert "--env-file .env" in scripts["dev:backend"]
    config = (ROOT / "playwright.config.ts").read_text()
    assert 'BACKEND_URL: "http://127.0.0.1:8001"' in config


def test_importing_the_agent_reads_no_file():
    for path in (ROOT / "agent").glob("*.py"):
        tree = ast.parse(path.read_text())
        at_import = [
            node.value.func
            for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]
        names = [getattr(f, "id", getattr(f, "attr", "")) for f in at_import]
        assert "load_dotenv" not in names, path
        assert "load_environment" not in names, path
