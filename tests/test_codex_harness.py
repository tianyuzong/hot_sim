from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import thermoflow.codex_harness as harness_module
from thermoflow.agent import AgentPolicyDecision, CodexAgentPolicy, build_agent_policy
from thermoflow.codex_harness import CodexHarness, CodexHarnessError, _strict_schema
from thermoflow.models import ConfirmationRecord, ModelingMessage, SimulationPlan
from thermoflow.planner import (
    CodexPlanner,
    DeterministicPlanner,
    PlannerUnavailableError,
    build_planner,
)
from thermoflow.settings import Settings

from .helpers import box_workpiece


@pytest.fixture
def codex_stub(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    config = 'model = "configured-model"\nmodel_provider = "custom"\n'
    config += '[mcp_servers.external]\ncommand = "must-not-run"\n'
    config += '[model_providers.custom]\nenv_key = "PROVIDER_AUTH"\n'
    (home / "config.toml").write_text(config, encoding="utf-8")
    captured = {"response": {"action": "finish", "summary": "结束本轮"}, "returncode": 0}

    class FakeProcess:
        def __init__(self, command, **kwargs):
            captured.update(command=command, options=kwargs)
            self.returncode = captured["returncode"]
            if captured.get("missing_output"):
                return
            output = Path(command[command.index("--output-last-message") + 1])
            response = captured["response"]
            output.write_text(response if isinstance(response, str) else json.dumps(response), encoding="utf-8")
            captured["schema"] = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())

        def communicate(self, input, timeout):
            captured.update(payload=json.loads(input), timeout_seconds=timeout)
            if captured.get("timeout") is True:
                raise subprocess.TimeoutExpired("private command", timeout)
            return None, None

    monkeypatch.setattr(harness_module.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(harness_module, "_terminate_process_group", lambda process: captured.update(terminated=True))
    return CodexHarness(executable=sys.executable, codex_home=home), captured, home


def call(harness):
    return harness.generate(AgentPolicyDecision, instructions="只返回结构化结果", payload={"request": "敏感建模数据"})


def test_cli_uses_existing_configuration_stdin_and_review_only_tools(codex_stub, monkeypatch):
    harness, captured, home = codex_stub
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-server-secret")
    monkeypatch.setenv("DATABASE_PASSWORD", "unrelated-database-secret")
    monkeypatch.setenv("CODEX_THREAD_ID", "enclosing-session")
    monkeypatch.setenv("PROVIDER_AUTH", "provider-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.example")
    original = (home / "config.toml").read_bytes()
    result, model = call(harness)
    assert result.action == "finish" and model == "configured-model"
    command, options = captured["command"], captured["options"]
    assert command[1] == "exec" and command[-1] == "-"
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in command and "--ignore-rules" in command
    assert "approval_policy=\"never\"" in command
    assert "mcp_servers={}" in command
    assert "--model" not in command and "--ignore-user-config" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "敏感建模数据" not in str(command)
    for feature in harness_module._DISABLED_FEATURES:
        assert command[command.index(feature) - 1] == "--disable"
    assert options["stdout"] == subprocess.DEVNULL and options["stderr"] == subprocess.DEVNULL
    assert options["start_new_session"] and "shell" not in options
    environment = options["env"]
    assert environment["CODEX_HOME"] == str(home)
    assert environment["PROVIDER_AUTH"] == "provider-secret"
    assert environment["HTTPS_PROXY"] == "https://proxy.example"
    assert not {"OPENAI_API_KEY", "DATABASE_PASSWORD", "CODEX_THREAD_ID"} & environment.keys()
    assert captured["payload"] == {"request": "敏感建模数据"}
    assert captured["terminated"] and not Path(options["cwd"]).exists()
    assert (home / "config.toml").read_bytes() == original
    assert sorted(p.name for p in home.iterdir()) == ["config.toml"]


@pytest.mark.parametrize("response", ["not json sk-test /private/path", "```json\n{}\n```", {},
    {"action": "finish", "summary": "结束", "command": "not-allowed"}])
def test_invalid_responses_are_rejected_without_leaking_model_text(codex_stub, response):
    harness, captured, _ = codex_stub
    captured["response"] = response
    with pytest.raises(CodexHarnessError, match="结构化校验") as error:
        call(harness)
    assert "sk-test" not in str(error.value) and "/private" not in str(error.value)
    assert not Path(captured["options"]["cwd"]).exists()


@pytest.mark.parametrize("mode, message", [("missing_output", "未返回"), ("timeout", "超时"),
    ("failure", "调用未成功"), ("large", "大小限制")])
def test_cli_failure_timeout_empty_and_large_output_cleanup(codex_stub, mode, message):
    harness, captured, _ = codex_stub
    captured[mode] = True
    if mode == "failure":
        captured["returncode"] = 1
    if mode == "large":
        captured["response"] = "x" * (harness_module._MAX_RESPONSE_BYTES + 1)
    with pytest.raises(CodexHarnessError, match=message):
        call(harness)
    assert captured["terminated"] and not Path(captured["options"]["cwd"]).exists()
    captured.update(timeout=False, returncode=0, missing_output=False, response={"action": "finish", "summary": "重试"})
    assert call(harness)[0].action == "finish"


def test_missing_executable_and_invalid_configuration_are_actionable(codex_stub):
    harness, _, home = codex_stub
    harness.executable = "/nonexistent/thermoflow-test-codex"
    with pytest.raises(CodexHarnessError, match="尚未找到"):
        call(harness)
    harness.executable = sys.executable
    (home / "config.toml").write_text("invalid toml [", encoding="utf-8")
    with pytest.raises(CodexHarnessError, match="配置无法读取"):
        call(harness)


def test_calls_across_planner_and_optimization_share_admission_limit(codex_stub):
    harness, _, _ = codex_stub
    with harness_module._CALL_SLOT, pytest.raises(CodexHarnessError, match="其他请求"):
        call(harness)
    assert call(harness)[0].action == "finish"


def test_timeout_reaps_real_child_process(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    real_popen = subprocess.Popen
    children = []

    def spawn_stub(command, **kwargs):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(harness_module.subprocess, "Popen", spawn_stub)
    harness = CodexHarness(executable=sys.executable, codex_home=home, timeout_seconds=0.05)
    with pytest.raises(CodexHarnessError, match="超时"):
        call(harness)
    assert len(children) == 1 and children[0].poll() is not None


def test_strict_schema_keeps_nullable_fields_but_requires_every_key():
    schema = _strict_schema(SimulationPlan)

    def verify(node):
        if isinstance(node, dict):
            assert "default" not in node
            if "$ref" in node:
                assert set(node) == {"$ref"}
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for value in node.values():
                verify(value)
        elif isinstance(node, list):
            for value in node:
                verify(value)

    verify(schema)
    assert {"type": "null"} in schema["properties"]["initial_temperature_k"]["anyOf"]


def test_codex_planner_preserves_context_and_cannot_confirm_or_solve(codex_stub):
    harness, captured, _ = codex_stub
    part = box_workpiece()
    expected = DeterministicPlanner().plan(part).plan
    expected.confirmation = ConfirmationRecord(status="confirmed", confirmed_by="model-claim")
    captured["response"] = expected.model_dump(mode="json")
    history = [ModelingMessage(role="user", content=f"condition {i}") for i in range(40)]
    decision = CodexPlanner(harness).plan(part, ["check units"], attempt=2, user_description="环境温度 300 K",
        current_plan=expected, conversation=history)
    assert decision.plan.confirmation.status == "needs_input"
    assert decision.plan.confirmation.confirmed_by is None
    assert decision.provenance.provider == "codex-cli" and decision.provenance.model == "configured-model"
    assert decision.provenance.attempts == 2 and decision.provenance.response_id is None
    payload = captured["payload"]
    assert payload["current_draft"] == expected.model_dump(mode="json")
    assert len(payload["conversation"]) == 20
    assert payload["validation_feedback"] == ["check units"]
    assert payload["material_catalog"]
    assert "triangle_ids" not in json.dumps(payload["workpiece"])


def test_codex_optimization_cannot_invent_an_authorized_adjustment(codex_stub):
    harness, captured, _ = codex_stub
    captured["response"] = {"action": "apply_adjustment", "summary": "采用平台提供的调整"}
    policy = CodexAgentPolicy(harness)
    assert policy.decide({"test": True}, {}).decision.action == "finish"
    changes = {"heat_source_power_w": 12.0}
    choice = policy.decide({"test": True}, changes)
    assert choice.decision.action == "apply_adjustment"
    assert captured["payload"] == {"state": {"test": True}, "allowed_adjustment": changes}
    assert policy.model == "configured-model"
    captured["returncode"] = 1
    with pytest.raises(PlannerUnavailableError):
        policy.decide({}, changes)


def test_codex_factory_and_environment_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMOFLOW_PLANNER", "codex")
    monkeypatch.setenv("THERMOFLOW_CODEX_HOME", "deployment-codex")
    monkeypatch.setenv("THERMOFLOW_CODEX_EXECUTABLE", sys.executable)
    monkeypatch.setenv("THERMOFLOW_CODEX_TIMEOUT_SECONDS", "60")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings.from_env(tmp_path)
    assert settings.codex_home == tmp_path / "deployment-codex"
    assert settings.codex_timeout_seconds == 60
    assert isinstance(build_planner(settings), CodexPlanner)
    assert isinstance(build_agent_policy(settings), CodexAgentPolicy)
    monkeypatch.setenv("THERMOFLOW_CODEX_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="超时"):
        Settings.from_env(tmp_path)


def test_modeling_with_codex_keeps_apply_and_user_confirmation_separate(codex_stub, tmp_path):
    from .test_modeling import decide, message, setup_draft
    harness, captured, _ = codex_stub
    service, repo, modeling, study = setup_draft(tmp_path)
    updated = study.plan.model_copy(update={"purpose": "用户要求的温度验证"})
    captured["response"] = updated.model_dump(mode="json")
    service.planner = CodexPlanner(harness)
    proposed = message(modeling, study, "更新用途")
    assert proposed.plan == study.plan and proposed.modeling.proposal is not None
    applied = decide(modeling, proposed, "apply")
    assert applied.plan.purpose == updated.purpose
    assert applied.confirmation.status == "needs_input"
    assert applied.input_snapshot_sha256 is None
    with pytest.raises(ValueError):
        service.generate_mesh(study.study_id)
    assert repo.get_study(study.study_id).planner.provider == "codex-cli"


def test_health_and_api_failure_never_expose_codex_configuration(codex_stub, tmp_path):
    from fastapi.testclient import TestClient

    from thermoflow.api import create_app
    harness, captured, home = codex_stub
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "api-data", cadflow_repo=tmp_path / "missing",
        planner_mode="codex", compute_backend="cpu", codex_home=home)
    client = TestClient(create_app(settings, CodexPlanner(harness)))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["planner_mode"] == "codex" and health.json()["agent_mode"] == "codex-cli"
    assert health.json()["openai_model"] is None
    assert str(home) not in health.text and "PROVIDER_AUTH" not in health.text
    part = client.post("/v1/workpieces/boxes", json={"name": "Reference", "dimensions_mm": {"x": 10, "y": 6, "z": 4}}).json()
    captured["returncode"] = 1
    failure = client.post("/v1/studies", json={"workpiece_id": part["workpiece_id"], "purpose": "温度验证"})
    assert failure.status_code == 503
    assert str(home) not in failure.text and "must-not-run" not in failure.text
    assert "Codex" in failure.text


def test_python_310_tomli_fallback_reads_the_same_configuration(codex_stub, monkeypatch):
    import importlib.util
    from types import SimpleNamespace
    _, _, home = codex_stub
    parser = harness_module.tomllib
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", SimpleNamespace(loads=parser.loads))
    spec = importlib.util.spec_from_file_location("codex_harness_compat_test", harness_module.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    resolved, config = module.CodexHarness(codex_home=home)._configuration()
    assert resolved == home and config["model"] == "configured-model"


@pytest.mark.parametrize("busy", [False, True])
def test_modeling_api_preserves_safe_codex_failure_detail(codex_stub, tmp_path, busy):
    from contextlib import nullcontext

    from .test_confirmed_workflow import _client

    harness, captured, home = codex_stub
    client = _client(tmp_path)
    part = client.post("/v1/workpieces/boxes", json={"name": "test", "dimensions_mm": {"x": 10, "y": 6, "z": 4}}).json()
    study = client.post("/v1/studies", json={"workpiece_id": part["workpiece_id"]}).json()
    client.app.state.service.planner = CodexPlanner(harness)
    captured["timeout"] = True
    with harness_module._CALL_SLOT if busy else nullcontext():
        response = client.post(f"/v1/studies/{study['study_id']}/modeling/messages", json={
            "expected_revision": study["draft_revision"], "message": "调整温度判据"})
    assert response.status_code == 503
    assert ("其他请求" if busy else "超时") in response.json()["detail"]
    assert str(home) not in response.text
    assert client.get(f"/v1/studies/{study['study_id']}").json() == study
