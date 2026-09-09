from types import SimpleNamespace

import httpx
import openai
import pytest

from thermoflow.agent import OpenAIAgentPolicy, SimulationAgent
from thermoflow.models import AgentGoalRequest, BoxWorkpieceInput, DimensionsMM
from thermoflow.planner import OpenAIPlanner, PlannerUnavailableError

from .helpers import box_workpiece
from .test_agent import _service
from .test_confirmed_workflow import _client


def provider_error(kind):
    request = httpx.Request("POST", "https://provider.invalid/responses")
    if kind == "timeout":
        return openai.APITimeoutError(request=request)
    if kind == "connection":
        return openai.APIConnectionError(request=request, message="secret sk-example /private/path")
    status, error_type = {
        "auth": (401, openai.AuthenticationError),
        "rate": (429, openai.RateLimitError),
        "server": (503, openai.InternalServerError),
    }[kind]
    return error_type("secret sk-example /private/path", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize("consumer", ["planner", "optimizer"])
@pytest.mark.parametrize("kind, expected", [
    ("timeout", "超时"), ("connection", "连接"), ("auth", "认证"),
    ("rate", "限流"), ("server", "服务"), ("empty", "结构化"),
])
def test_model_failures_are_actionable_and_do_not_expose_provider_text(monkeypatch, consumer, kind, expected):
    def parse(**kwargs):
        if kind == "empty":
            return SimpleNamespace(output_parsed=None)
        raise provider_error(kind)

    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    with pytest.raises(PlannerUnavailableError) as caught:
        if consumer == "planner":
            OpenAIPlanner("configured-model", api_key="test-only").plan(box_workpiece())
        else:
            OpenAIAgentPolicy("configured-model", api_key="test-only").decide({}, {})
    assert expected in str(caught.value)
    assert not any(secret in str(caught.value) for secret in ("secret", "sk-example", "/private", "provider.invalid"))


def test_openai_optimizer_sets_bounded_transport_options(monkeypatch):
    options = {}

    def client(**kwargs):
        options.update(kwargs)
        return SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs: SimpleNamespace(
            output_parsed=SimpleNamespace(action="finish", summary="结束"), id="test-response")))

    monkeypatch.setattr(openai, "OpenAI", client)
    assert OpenAIAgentPolicy("configured-model", api_key="test-only").decide({}, {}).decision.action == "finish"
    assert options["timeout"] == 45.0
    assert options["max_retries"] == 2


def test_creation_maps_provider_outage_to_503_without_saving_a_study(tmp_path, monkeypatch):
    client = _client(tmp_path)
    part = client.post("/v1/workpieces/boxes", json={"name": "test", "dimensions_mm": {"x": 10, "y": 6, "z": 4}}).json()

    def parse(**kwargs):
        raise provider_error("timeout")

    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    client.app.state.service.planner = OpenAIPlanner("configured-model", api_key="test-only")
    response = client.post("/v1/studies", json={"workpiece_id": part["workpiece_id"]})
    assert response.status_code == 503
    assert "超时" in response.json()["detail"]
    assert client.get("/v1/studies").json() == []


def test_optimizer_failure_record_preserves_baseline_and_hides_raw_errors(tmp_path):
    service, repo = _service(tmp_path)
    part = service.register_box(BoxWorkpieceInput(name="test", dimensions_mm=DimensionsMM(x=10, y=6, z=4)))
    baseline = service.create_study(part.workpiece_id)
    baseline = service.run_study(baseline.study_id)

    class BrokenPolicy:
        provider = "test"
        model = "test"

        def decide(self, context, changes):
            raise RuntimeError("secret sk-example /private/path")

    run = SimulationAgent(repository=repo, service=service, policy=BrokenPolicy()).run(
        baseline.study_id, AgentGoalRequest(instruction="降低最高温度", target_max_temperature_k=300))
    assert run.status == "failed"
    assert run.candidate_study_ids == []
    assert repo.get_study(baseline.study_id) == baseline
    assert not any(secret in repo.get_agent_run(run.run_id).model_dump_json()
                   for secret in ("secret", "sk-example", "/private"))
