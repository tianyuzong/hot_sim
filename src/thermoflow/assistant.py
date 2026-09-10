"""Unified read-only knowledge assistant and controlled modeling sessions."""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Protocol

from pydantic import Field

from .codex_harness import CodexHarness, CodexHarnessError
from .materials import list_materials
from .modeling import ModelingService
from .models import (
    AgentCitation,
    AgentLaunchRequest,
    AgentModelingTurn,
    AgentQuestion,
    AgentQuestionAnswer,
    AgentQuestionOption,
    AgentSessionCreateRequest,
    AgentSessionRecord,
    AgentTurnRequest,
    AgentWorkpieceRequest,
    DraftUpdateRequest,
    FaceSelector,
    FixedTemperatureBoundary,
    LengthUnit,
    ModelingDecisionRequest,
    ModelingMessage,
    SimulationOverrides,
    StrictModel,
    StudyConfirmationRequest,
    StudyCopyRequest,
    TaskCreateRequest,
)
from .planner import PlannerUnavailableError, _openai_failure
from .policy import validate_plan
from .service import StudyService
from .settings import Settings
from .storage import FileRepository, RecordNotFoundError
from .tasks import TERMINAL_TASK_STATES, TaskManager


class KnowledgeAnswer(StrictModel):
    answer: str = Field(min_length=1, max_length=4_000)
    citation_ids: list[str] = Field(default_factory=list, max_length=8)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    citation_id: str
    title: str
    section: str
    text: str


class KnowledgeBase:
    """Small dependency-free local corpus; documents are trusted application data."""

    def __init__(self) -> None:
        self.chunks = self._load()

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.lower())

    def _load(self) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        root = resources.files("thermoflow.knowledge")
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if path.suffix != ".md":
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            title = path.stem
            section = "正文"
            body: list[str] = []
            index = 0
            for line in lines:
                if line.startswith("# "):
                    title = line[2:].strip()
                    continue
                if line.startswith("## "):
                    if body:
                        chunks.append(KnowledgeChunk(
                            citation_id=f"doc:{path.stem}:{section}", title=title,
                            section=section, text="\n".join(body).strip(),
                        ))
                        body = []
                    section = line[3:].strip()
                    continue
                if line.strip():
                    body.append(line.strip())
                index += 1
            if body:
                chunks.append(KnowledgeChunk(
                    citation_id=f"doc:{path.stem}:{section}", title=title,
                    section=section, text="\n".join(body).strip(),
                ))
        return chunks

    def search(self, query: str, limit: int = 4) -> list[KnowledgeChunk]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return self.chunks[:limit]
        scored: list[tuple[int, KnowledgeChunk]] = []
        for chunk in self.chunks:
            haystack = self._tokens(f"{chunk.title} {chunk.section} {chunk.text}")
            counts = {token: haystack.count(token) for token in set(query_tokens)}
            score = sum(min(counts[token], 3) for token in set(query_tokens))
            if query.lower() in chunk.text.lower():
                score += 5
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].citation_id))
        return [chunk for _, chunk in scored[:limit]]


class KnowledgeProvider(Protocol):
    provider: str
    model: str

    def answer(self, question: str, evidence: list[KnowledgeChunk], context: dict[str, Any]) -> KnowledgeAnswer: ...


class DeterministicKnowledgeProvider:
    provider = "local-documents"
    model = "extractive-knowledge-v1"

    def answer(self, question: str, evidence: list[KnowledgeChunk], context: dict[str, Any]) -> KnowledgeAnswer:
        context_text = ""
        result = context.get("result")
        if isinstance(result, dict):
            context_text = (
                "当前研究摘要："
                f"最高温度 {result.get('temperature_max_k', '不可用')} K，"
                f"最低温度 {result.get('temperature_min_k', '不可用')} K，"
                f"能量相对误差 {result.get('energy_balance_relative_error', '不可用')}。\n\n"
            )
        if evidence:
            text = context_text + "\n\n".join(chunk.text for chunk in evidence)[:3_600]
            return KnowledgeAnswer(
                answer=text + "\n\n以上内容来自 ThermoFlow 当前文档；具体研究仍以当前输入和求解结果为准。",
                citation_ids=[chunk.citation_id for chunk in evidence],
            )
        return KnowledgeAnswer(
            answer="当前 ThermoFlow 文档没有覆盖这个问题，且离线模式没有可用的通用知识模型。请补充更具体的热分析问题，或切换到已配置模型服务的模式。",
        )


KNOWLEDGE_INSTRUCTIONS = """
你是 ThermoFlow 工程问答助手。根据给定的可信文档片段和当前研究上下文回答用户问题。
不得执行代码、修改研究、创建任务或声称没有证据支持的安全/寿命结论。优先使用文档和当前研究事实；
如果使用通用知识，必须明确说明“通用知识，未由 ThermoFlow 求解器验证”。只能引用输入中存在的 citation_id。
回答使用简体中文，先给直接结论，再给条件、限制和来源。只返回结构化答案。
""".strip()

MODELING_TURN_PROMPT_VERSION = "assistant-modeling-turn-v1"
MODELING_TURN_INSTRUCTIONS = """
你是 ThermoFlow 的交互式热仿真建模 Agent。根据用户本轮自然语言、完整对话、当前草案、
几何上下文和材料目录，返回唯一指定的 AgentModelingTurn 结构化对象。所有面向用户的文字使用简体中文。
只处理热仿真建模，不执行求解，不声称已有结果，也不要求用户在对话中进行最终确认。

核心规则：
1. 仅将用户已经明确说出的事实写入 overrides。绝不从文件名、默认值、常识或先前助手建议推断用户事实。
2. 必须保留当前草案中与本轮用户输入无关的配置；只返回本轮可确定的覆盖字段。
3. 只询问缺失、歧义或需要用户选择的信息。绝不能再次询问用户在本轮请求或此前答案中已明确提供的参数。
4. 由你决定是否提问、问题文案、题型、选项及相关问题分组。优先将彼此相关的缺失项放在同一轮，
   但不要在同一轮提出超过 3 个问题。
5. questions 是用户界面定义：single_choice/multi_choice 必须给出选项；number 必须给出合理的
   minimum、maximum、step 和 unit；text 不应有 options。问题必须可由普通工程用户回答。
6. 用户选择或填写的回答会以自然语言加入下一轮对话；除 geometry_unit 外，不依赖 question_id
   直接修改草案。question_id 使用稳定的 ASCII 标识，例如 boundary_details 或 heat_source_location。
7. material 只能引用输入材料目录中的 catalog_material_id。用户明确给出目录材料名称时，
   立即返回对应 catalog_material_id；不要再追问物性来源、密度或比热，最终右侧表单会复核这些信息。
8. 温度写入 overrides 时使用 K，长度使用 mm；用户用 ℃ 表示时自行换算。最高温度判据写入
   overrides.criteria，目标单位为 K。
9. 仅支持当前草案和几何上下文中允许的稳态/瞬态各向同性导热、定温、局部热源、全局对流及已保存区域条件。
   未支持物理应清楚说明，不应虚构可执行参数。
10. 当 STL 单位尚未确认时，本轮只能提出一个名为 geometry_unit 的 single_choice 问题，且选项
    option_id 必须恰好为 um、mm、m 各一个。由你生成提问文案和选项标签；不得输出 overrides、
    catalog_material_id 或 ready_for_review=true。单位确认后，将从完整对话重新识别所有已给参数。
11. 当前没有工件时，说明需要选择或导入工件，不得生成草案参数。
12. 只有当没有待答问题、missing_information 为空且当前草案已满足可提交前的已知要求时，才将
    ready_for_review 设为 true，并提醒用户查看右侧表单完成最终确认。否则设为 false。
13. 当前界面暂不支持 region_picker；只有在用户尚未绑定工件时才能使用 file_upload，且 question_id 必须为 workpiece。

服务器会独立校验所有数值、材料、几何区域、草案版本和最终求解权限。不要输出代码、命令、路径、隐藏推理或求解结果。
""".strip()


class OpenAIKnowledgeProvider:
    provider = "openai"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def answer(self, question: str, evidence: list[KnowledgeChunk], context: dict[str, Any]) -> KnowledgeAnswer:
        if not self.api_key:
            return DeterministicKnowledgeProvider().answer(question, evidence, context)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise PlannerUnavailableError("尚未安装 openai Python 软件包") from exc
        payload = {
            "question": question,
            "evidence": [{"citation_id": item.citation_id, "title": item.title,
                          "section": item.section, "text": item.text} for item in evidence],
            "current_context": context,
        }
        try:
            response = OpenAI(api_key=self.api_key, timeout=45.0, max_retries=2).responses.parse(
                model=self.model, instructions=KNOWLEDGE_INSTRUCTIONS,
                input=json.dumps(payload, ensure_ascii=True, sort_keys=True),
                text_format=KnowledgeAnswer, store=False,
            )
        except Exception as exc:  # noqa: BLE001 - sanitize provider boundary
            raise _openai_failure(exc) from None
        parsed = response.output_parsed
        if parsed is None:
            raise PlannerUnavailableError("模型未返回结构化问答，未执行任何修改，请重试")
        allowed = {item.citation_id for item in evidence}
        return parsed.model_copy(update={"citation_ids": [item for item in parsed.citation_ids if item in allowed]})


class CodexKnowledgeProvider:
    provider = "codex-cli"
    model = "codex-configured"

    def __init__(self, harness: CodexHarness):
        self.harness = harness

    def answer(self, question: str, evidence: list[KnowledgeChunk], context: dict[str, Any]) -> KnowledgeAnswer:
        try:
            parsed, self.model = self.harness.generate(
                KnowledgeAnswer, instructions=KNOWLEDGE_INSTRUCTIONS,
                payload={"question": question,
                         "evidence": [{"citation_id": item.citation_id, "title": item.title,
                                       "section": item.section, "text": item.text} for item in evidence],
                         "current_context": context},
            )
        except CodexHarnessError as exc:
            raise PlannerUnavailableError(str(exc)) from None
        allowed = {item.citation_id for item in evidence}
        return parsed.model_copy(update={"citation_ids": [item for item in parsed.citation_ids if item in allowed]})


def build_knowledge_provider(settings: Settings) -> KnowledgeProvider:
    if settings.planner_mode == "codex":
        return CodexKnowledgeProvider(CodexHarness(
            executable=settings.codex_executable, codex_home=settings.codex_home,
            timeout_seconds=settings.codex_timeout_seconds,
        ))
    if settings.planner_mode == "openai":
        return OpenAIKnowledgeProvider(settings.openai_model)
    return DeterministicKnowledgeProvider()


class ModelingTurnProvider(Protocol):
    provider: str
    model: str

    def respond(
        self,
        *,
        workpiece: Any | None,
        current_plan: Any | None,
        conversation: list[ModelingMessage],
        pending_questions: list[AgentQuestion],
        user_message: str,
    ) -> AgentModelingTurn: ...


class OpenAIModelingTurnProvider:
    provider = "openai"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def respond(
        self,
        *,
        workpiece: Any | None,
        current_plan: Any | None,
        conversation: list[ModelingMessage],
        pending_questions: list[AgentQuestion],
        user_message: str,
    ) -> AgentModelingTurn:
        if not self.api_key:
            raise PlannerUnavailableError("当 THERMOFLOW_PLANNER=openai 时必须配置 OPENAI_API_KEY")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise PlannerUnavailableError("尚未安装 openai Python 软件包") from exc
        try:
            response = OpenAI(api_key=self.api_key, timeout=45.0, max_retries=2).responses.parse(
                model=self.model,
                instructions=MODELING_TURN_INSTRUCTIONS,
                input=json.dumps(
                    _modeling_turn_payload(
                        workpiece=workpiece,
                        current_plan=current_plan,
                        conversation=conversation,
                        pending_questions=pending_questions,
                        user_message=user_message,
                    ),
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                text_format=AgentModelingTurn,
                store=False,
            )
        except Exception as exc:  # noqa: BLE001 - sanitize provider failures
            raise _openai_failure(exc) from None
        parsed = response.output_parsed
        if parsed is None:
            raise PlannerUnavailableError("模型未返回结构化建模回复，未应用任何修改，请重试")
        return parsed


class CodexModelingTurnProvider:
    provider = "codex-cli"
    model = "codex-configured"

    def __init__(self, harness: CodexHarness) -> None:
        self.harness = harness

    def respond(
        self,
        *,
        workpiece: Any | None,
        current_plan: Any | None,
        conversation: list[ModelingMessage],
        pending_questions: list[AgentQuestion],
        user_message: str,
    ) -> AgentModelingTurn:
        try:
            parsed, self.model = self.harness.generate(
                AgentModelingTurn,
                instructions=MODELING_TURN_INSTRUCTIONS,
                payload=_modeling_turn_payload(
                    workpiece=workpiece,
                    current_plan=current_plan,
                    conversation=conversation,
                    pending_questions=pending_questions,
                    user_message=user_message,
                ),
            )
        except CodexHarnessError as exc:
            raise PlannerUnavailableError(str(exc)) from None
        return parsed


class DeterministicModelingTurnProvider:
    """Offline test/demo adapter. It is selected explicitly and never used as a model-failure fallback."""

    provider = "deterministic-offline"
    model = "deterministic-agent-turn-v1"

    def respond(
        self,
        *,
        workpiece: Any | None,
        current_plan: Any | None,
        conversation: list[ModelingMessage],
        pending_questions: list[AgentQuestion],
        user_message: str,
    ) -> AgentModelingTurn:
        if workpiece is None:
            return AgentModelingTurn(
                answer="请先选择已有工件或导入 STL，再继续建立仿真草案。",
                questions=[AgentQuestion(
                    question_id="workpiece",
                    group="几何",
                    type="file_upload",
                    prompt="请选择或导入要分析的工件。",
                    rationale="热边界和材料必须绑定到实际几何。",
                )],
                missing_information=["需要一个可用的工件几何"],
            )
        if not workpiece.unit_confirmed:
            return AgentModelingTurn(
                answer="需要先确认 STL 坐标单位，确认后我会继续识别你已经提供的全部建模参数。",
                questions=[AgentQuestion(
                    question_id="geometry_unit",
                    group="几何",
                    type="single_choice",
                    prompt="这个 STL 的坐标数值对应哪种长度单位？",
                    rationale="STL 不保存单位；单位会影响热源位置、网格和换热面积。",
                    options=[
                        AgentQuestionOption(option_id="um", label="微米 (μm)"),
                        AgentQuestionOption(option_id="mm", label="毫米 (mm)"),
                        AgentQuestionOption(option_id="m", label="米 (m)"),
                    ],
                )],
                missing_information=["需要确认 STL 长度单位"],
            )

        text = "\n".join(item.content for item in conversation if item.role == "user")
        overrides = _message_overrides(text, workpiece.dimensions_mm)
        questions: list[AgentQuestion] = []
        missing: list[str] = []
        material_id = next((
            entry.material_id for entry in list_materials()
            if any(alias in text.lower() for alias in {
                "al-6061-t6": ("6061", "铝合金"),
                "copper-c110": ("c110", "紫铜", "铜"),
                "steel-304": ("304", "不锈钢"),
                "inconel-718": ("inconel", "718", "高温合金"),
            }.get(entry.material_id, ()))
        ), None)
        if material_id is None:
            questions.append(AgentQuestion(
                question_id="material_selection",
                group="材料",
                type="single_choice",
                prompt="当前工件采用哪种材料？",
                rationale="材料导热系数直接影响温升。",
                options=[AgentQuestionOption(option_id=item.material_id, label=item.material.name)
                         for item in list_materials()],
            ))
            missing.append("需要确认材料")
        supplied = _message_provenance(text)
        definitions = [
            ("analysis_type", "分析类型", "请选择稳态或瞬态导热分析。", "分析类型决定是否需要时间参数。",
             [("steady_state_conduction", "稳态导热"), ("transient_conduction", "瞬态导热")]),
            ("heat_source_details", "热源", "请补充热源的位置、总功率和作用方式。", "热源定义决定能量输入和区域映射。", None),
            ("boundary_details", "边界", "请补充定温边界、对流或其他散热条件。", "稳态导热需要可执行的热边界。", None),
            ("mesh_details", "网格", "请填写目标单元尺寸。", "网格尺寸影响局部热源映射精度。", None),
        ]
        missing_keys = {
            "analysis_type": "analysis_type" not in supplied,
            "heat_source_details": "heat_source_power_w" not in supplied,
            "boundary_details": "boundary_explanation" not in supplied,
            "mesh_details": "target_element_size_mm" not in supplied,
        }
        for question_id, group, prompt, rationale, options in definitions:
            if not missing_keys[question_id] or len(questions) >= 3:
                continue
            if options:
                questions.append(AgentQuestion(
                    question_id=question_id, group=group, type="single_choice",
                    prompt=prompt, rationale=rationale,
                    options=[AgentQuestionOption(option_id=value, label=label) for value, label in options],
                ))
            else:
                questions.append(AgentQuestion(
                    question_id=question_id, group=group, type="text",
                    prompt=prompt, rationale=rationale,
                ))
            missing.append(prompt)
        ready = not questions
        return AgentModelingTurn(
            answer=(
                "我已根据当前对话更新可确定的建模参数。"
                if ready else "我已写入可以确定的参数，还需要确认以下信息。"
            ),
            overrides=overrides,
            catalog_material_id=material_id,
            questions=questions,
            missing_information=missing,
            ready_for_review=ready,
        )


def build_modeling_turn_provider(settings: Settings) -> ModelingTurnProvider:
    if settings.planner_mode == "codex":
        return CodexModelingTurnProvider(CodexHarness(
            executable=settings.codex_executable,
            codex_home=settings.codex_home,
            timeout_seconds=settings.codex_timeout_seconds,
        ))
    if settings.planner_mode == "openai":
        return OpenAIModelingTurnProvider(settings.openai_model)
    return DeterministicModelingTurnProvider()


def _modeling_turn_payload(
    *,
    workpiece: Any | None,
    current_plan: Any | None,
    conversation: list[ModelingMessage],
    pending_questions: list[AgentQuestion],
    user_message: str,
) -> dict[str, Any]:
    return {
        "user_message": user_message,
        "workpiece": (
            {
                "workpiece_id": workpiece.workpiece_id,
                "name": workpiece.name,
                "kind": workpiece.kind.value,
                "cad_format": workpiece.cad_format.value if workpiece.cad_format else None,
                "unit_confirmed": workpiece.unit_confirmed,
                "length_unit": workpiece.length_unit.value if workpiece.length_unit else None,
                "dimensions_mm": workpiece.dimensions_mm.model_dump(mode="json")
                if workpiece.dimensions_mm else None,
                "components": [
                    {"component_id": item.component_id, "name": item.name}
                    for item in workpiece.components
                ],
                "regions": [
                    {
                        "region_id": item.region_id,
                        "name": item.name,
                        "kind": item.kind,
                        "supported_condition_kinds": item.supported_condition_kinds,
                    }
                    for item in workpiece.regions
                ],
                "geometry_summary": {
                    key: value for key, value in workpiece.geometry.summary.items()
                    if key not in {"preview", "vertices", "triangles", "mesh", "fields"}
                },
            }
            if workpiece is not None else None
        ),
        "current_draft": current_plan.model_dump(mode="json") if current_plan is not None else None,
        "pending_questions": [item.model_dump(mode="json") for item in pending_questions],
        "conversation": [
            {"role": item.role, "content": item.content}
            for item in conversation[-20:]
        ],
        "material_catalog": [item.model_dump(mode="json") for item in list_materials()],
    }


def _looks_like_modeling(message: str) -> bool:
    return bool(re.search(r"仿真|模拟|导热|热源|温度场|网格|求解|计算|设置|材料|边界|启动|分析", message))


def _looks_like_question(message: str) -> bool:
    return bool(re.search(r"[?？]|为什么|什么是|如何|怎么|是否|能否|可以吗|区别|解释|多少|哪种|请问", message))


def _looks_like_action(message: str) -> bool:
    return bool(re.search(r"设置|建立|生成|创建|启动|运行|修改|调整|填写|配置|开始仿真", message))


def _is_modeling_request(message: str) -> bool:
    return _looks_like_modeling(message) and (
        _looks_like_action(message) or not _looks_like_question(message)
    )


def _message_provenance(message: str) -> dict[str, str]:
    """Identify user-supplied facts so the Agent only asks for actual omissions."""

    text = message.lower()
    marker = "user_statement"
    provenance: dict[str, str] = {}
    if any(term in text for term in ("稳态", "瞬态", "steady", "transient")):
        provenance["analysis_type"] = marker
    if any(term in text for term in ("6061", "铝合金", "铜", "紫铜", "304", "不锈钢", "inconel")):
        provenance["material"] = marker
    if re.search(
        r"(?:功率|发热|热源)[^\d]{0,12}\d+(?:\.\d+)?\s*(?:w|瓦)"
        r"|\d+(?:\.\d+)?\s*(?:w|瓦)[^\n。；，]{0,12}(?:功率|热源)",
        text,
    ):
        provenance["heat_source_power_w"] = marker
    if re.search(
        r"(?:环境|空气|流体)[^\d-]{0,12}-?\d+(?:\.\d+)?\s*(?:℃|°c|c|k)"
        r"|\d+(?:\.\d+)?\s*(?:℃|°c|c|k)[^\n。；，]{0,12}(?:环境|空气|流体)",
        text,
    ):
        provenance["ambient_temperature_k"] = marker
    if re.search(r"(?:对流|换热系数|h\s*=)[^\d]{0,12}\d+(?:\.\d+)?", text):
        provenance["convection_coefficient_w_m2_k"] = marker
    if re.search(r"(?:目标(?:网格)?单元尺寸|网格尺寸|单元尺寸)[^\d]{0,12}\d+(?:\.\d+)?\s*mm", text):
        provenance["target_element_size_mm"] = marker
    if re.search(r"(?:最高温度|峰值温度|温度判据|温度限值)[^\d-]{0,16}-?\d+(?:\.\d+)?\s*(?:℃|°c|c|k)", text):
        provenance["max_temperature_c"] = marker
    if any(term in text for term in ("定温", "边界", "对流", "绝热", "x 最小", "x 最大", "y 最小", "y 最大", "z 最小", "z 最大")):
        provenance["boundary_explanation"] = marker
    return provenance


_TEMPERATURE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(℃|°c|c|k)", re.IGNORECASE)
_POWER = re.compile(r"(-?\d+(?:\.\d+)?)\s*(w|瓦)\b", re.IGNORECASE)
_LENGTH_MM = re.compile(r"(-?\d+(?:\.\d+)?)\s*mm\b", re.IGNORECASE)
_TIME_SECONDS = re.compile(r"(-?\d+(?:\.\d+)?)\s*(s|秒)\b", re.IGNORECASE)
_FACE = re.compile(r"([xyz])\s*(?:轴)?\s*(最小|最大|min|max)(?:侧|面)?", re.IGNORECASE)
_CONVECTION_COEFFICIENT = re.compile(
    r"(?:对流(?:换热)?系数|换热系数|h\s*=)[^\d-]{0,12}(-?\d+(?:\.\d+)?)", re.IGNORECASE
)
_MESH_SIZE = re.compile(
    r"(?:目标(?:网格)?单元尺寸|网格尺寸|单元尺寸)[^\d-]{0,12}(-?\d+(?:\.\d+)?)\s*mm",
    re.IGNORECASE,
)


def _nearby_quantity(
    text: str,
    expression: re.Pattern[str],
    terms: tuple[str, ...],
) -> tuple[float, str] | None:
    for match in expression.finditer(text):
        start, end = match.span()
        context = text[max(0, start - 24):min(len(text), end + 24)]
        if any(term in context for term in terms):
            return float(match.group(1)), match.group(2).lower()
    return None


def _temperature_kelvin(quantity: tuple[float, str] | None) -> float | None:
    if quantity is None:
        return None
    value, unit = quantity
    return value + 273.15 if unit in {"℃", "°c", "c"} else value


def _message_overrides(message: str, dimensions_mm) -> SimulationOverrides | None:
    """Extract only explicit scalar and bounding-face inputs from one user message."""
    text = message.lower()
    values: dict[str, Any] = {}
    material = next((
        entry.material
        for entry in list_materials()
        if any(alias in text for alias in {
            "al-6061-t6": ("6061", "6061-t6", "铝合金"),
            "copper-c110": ("c110", "紫铜", "铜"),
            "steel-304": ("304", "不锈钢"),
            "inconel-718": ("inconel", "718", "高温合金"),
        }.get(entry.material_id, ()))
    ), None)
    if material is not None:
        values["material_name"] = material.name
    if any(term in text for term in ("稳态", "steady")):
        values["analysis_type"] = "steady_state_conduction"
    elif any(term in text for term in ("瞬态", "transient")):
        values["analysis_type"] = "transient_conduction"

    power = _nearby_quantity(text, _POWER, ("功率", "热源", "发热"))
    if power is not None:
        values.update(enable_heat_source=True, heat_source_power_w=power[0])

    ambient = _temperature_kelvin(
        _nearby_quantity(text, _TEMPERATURE, ("环境", "空气", "流体"))
    )
    coefficient_match = _CONVECTION_COEFFICIENT.search(text)
    if ambient is not None:
        values.update(enable_global_convection=True, ambient_temperature_k=ambient)
    if coefficient_match is not None:
        values.update(
            enable_global_convection=True,
            convection_coefficient_w_m2_k=float(coefficient_match.group(1)),
        )

    mesh_match = _MESH_SIZE.search(text)
    if mesh_match is not None:
        values["target_element_size_mm"] = float(mesh_match.group(1))

    criterion = _temperature_kelvin(
        _nearby_quantity(text, _TEMPERATURE, ("最高温度", "峰值温度", "温度判据", "温度限值"))
    )
    if criterion is not None:
        values["criteria"] = [{
            "metric": "max_temperature",
            "operator": "less_or_equal",
            "target": {"value": criterion, "unit": "K"},
        }]

    if values.get("analysis_type") == "transient_conduction":
        initial = _temperature_kelvin(_nearby_quantity(text, _TEMPERATURE, ("初始温度",)))
        duration = _nearby_quantity(text, _TIME_SECONDS, ("持续", "时长", "工作时间"))
        step = _nearby_quantity(text, _TIME_SECONDS, ("时间步长", "步长"))
        if initial is not None:
            values["initial_temperature_k"] = initial
        if duration is not None:
            values["duration_s"] = duration[0]
        if step is not None:
            values["time_step_s"] = step[0]

    fixed_boundaries: list[FixedTemperatureBoundary] = []
    for face in _FACE.finditer(text):
        axis, side = face.group(1).lower(), face.group(2).lower()
        suffix = "min" if side in {"最小", "min"} else "max"
        after = text[face.end():face.end() + 56]
        if not any(term in after for term in ("保持", "恒温", "定温", "固定", "温度", "为")):
            continue
        temperature_match = _TEMPERATURE.search(after)
        if temperature_match is None:
            continue
        temperature_offset = temperature_match.start()
        if any(
            0 <= after.find(term) < temperature_offset
            for term in ("热源", "功率", "发热")
        ):
            continue
        temperature = _temperature_kelvin((
            float(temperature_match.group(1)), temperature_match.group(2).lower(),
        ))
        assert temperature is not None
        fixed_boundaries.append(FixedTemperatureBoundary(
            selector=FaceSelector(f"face.{axis}{suffix}"),
            temperature_k=temperature,
        ))
    if fixed_boundaries:
        values["fixed_boundaries"] = fixed_boundaries

    source_face = next((
        face for face in _FACE.finditer(text)
        if any(term in text[face.start():face.end() + 64] for term in ("热源", "功率", "发热"))
    ), None)
    if source_face is not None and power is not None and dimensions_mm is not None:
        axis, side = source_face.group(1).lower(), source_face.group(2).lower()
        suffix = "min" if side in {"最小", "min"} else "max"
        dimensions = dict(zip(("x", "y", "z"), dimensions_mm.as_tuple(), strict=True))
        center = {key: value / 2.0 for key, value in dimensions.items()}
        center[axis] = 0.0 if suffix == "min" else dimensions[axis]
        in_plane = [key for key in "xyz" if key != axis]
        values.update({
            "heat_source_shape": "surface",
            "heat_source_placement": "surface",
            "heat_source_surface_axis": axis,
            "heat_source_surface_width_mm": dimensions[in_plane[0]],
            "heat_source_surface_height_mm": dimensions[in_plane[1]],
            "heat_source_surface_thickness_mm": values.get(
                "target_element_size_mm", min(dimensions.values()) / 8.0,
            ),
            **{f"heat_source_{key}_mm": value for key, value in center.items()},
        })
    return SimulationOverrides(**values) if values else None


class AssistantService:
    """Coordinates local Q&A, editable study drafts, and task launch."""

    def __init__(self, repository: FileRepository, service: StudyService, modeling: ModelingService,
                 task_manager: TaskManager, provider: KnowledgeProvider, *,
                 modeling_provider: ModelingTurnProvider | None = None,
                 retain_history: bool = True):
        self.repository = repository
        self.service = service
        self.modeling = modeling
        self.task_manager = task_manager
        self.provider = provider
        self.modeling_provider = modeling_provider or DeterministicModelingTurnProvider()
        self.knowledge = KnowledgeBase()
        self.retain_history = retain_history
        self.task_manager.add_completion_listener(self._on_task_completed)

    def _on_task_completed(self, task) -> None:
        """Persist the Agent conclusion even when no browser is polling the session."""
        for session in self.repository.list_agent_sessions(project_id=task.project_id):
            if session.task_id != task.task_id:
                continue
            refreshed = self._refresh(session)
            if refreshed != session:
                self._save(refreshed)

    def _save(self, record: AgentSessionRecord) -> AgentSessionRecord:
        updated = record.model_copy(update={"updated_at": datetime.now(timezone.utc)})
        persisted = updated if self.retain_history else updated.model_copy(update={"messages": []})
        self.repository.save_agent_session(persisted)
        return updated

    def _context(self, session: AgentSessionRecord) -> dict[str, Any]:
        context: dict[str, Any] = {}
        if session.workpiece_id:
            workpiece = self.repository.get_workpiece(session.workpiece_id)
            context["workpiece"] = {
                "name": workpiece.name, "kind": workpiece.kind.value,
                "dimensions_mm": workpiece.dimensions_mm.model_dump() if workpiece.dimensions_mm else None,
                "components": [{"id": item.component_id, "name": item.name} for item in workpiece.components],
                "geometry_summary": {key: value for key, value in workpiece.geometry.summary.items()
                                     if key not in {"preview", "vertices", "triangles", "mesh", "fields"}},
            }
        if session.study_id:
            study = self.repository.get_study(session.study_id)
            context["study"] = {
                "name": study.plan.study_name if study.plan else study.study_id,
                "status": study.status.value, "confirmation": study.confirmation.status,
                "policy": study.policy.model_dump(mode="json") if study.policy else None,
                "plan": study.plan.model_dump(mode="json") if study.plan else None,
                "evaluation_status": study.evaluation_status,
                "evaluation_summary": study.evaluation_summary,
            }
            if study.status.value == "succeeded":
                try:
                    result = self.repository.get_result(study.study_id)
                    context["result"] = {
                        "temperature_min_k": result.temperature_min_k,
                        "temperature_max_k": result.temperature_max_k,
                        "energy_balance_relative_error": result.energy_balance_relative_error,
                        "heat_rate_w": result.heat_rate_w,
                    }
                except RecordNotFoundError:
                    pass
        return context

    def _qa(self, session: AgentSessionRecord, message: str) -> AgentSessionRecord:
        evidence = self.knowledge.search(message)
        result = self.provider.answer(message, evidence, self._context(session))
        citations = [AgentCitation(
            citation_id=item.citation_id,
            source_type="thermoflow_doc",
            title=item.title,
            section=item.section,
            label=f"ThermoFlow 文档 · {item.title} · {item.section}",
        ) for item in evidence if item.citation_id in result.citation_ids]
        if session.study_id:
            citations.append(AgentCitation(
                citation_id=f"study:{session.study_id}",
                source_type="study_context",
                title="当前仿真研究",
                section=None,
                label="当前研究参数与结果摘要",
            ))
        if not citations and self.provider.provider != "local-documents":
            citations.append(AgentCitation(
                citation_id="general-knowledge",
                source_type="general_knowledge",
                title="模型通用知识",
                section=None,
                label="通用知识 · 未由 ThermoFlow 求解器验证",
            ))
        if not citations and result.citation_ids:
            result = result.model_copy(update={"citation_ids": []})
        return self._save(session.model_copy(update={
            "mode": "result_explanation" if session.study_id else "qa",
            "answer": result.answer,
            "citations": citations,
            "questions": [],
            "messages": [*session.messages[-38:], ModelingMessage(role="user", content=message),
                         ModelingMessage(role="assistant", content=result.answer)],
            "revision": session.revision + 1,
        }))

    @staticmethod
    def _question_error(question: AgentQuestion) -> str | None:
        if question.type in {"single_choice", "multi_choice"}:
            option_ids = [item.option_id for item in question.options]
            if not option_ids or len(option_ids) != len(set(option_ids)):
                return "选择题必须包含不重复的选项"
        elif question.options:
            return "非选择题不能包含选项"
        if question.type == "number":
            if question.unit is None or question.minimum is None or question.maximum is None or question.step is None:
                return "数值题必须提供单位、范围和步长"
            if question.minimum > question.maximum or question.step <= 0:
                return "数值题的范围或步长无效"
        if question.type == "file_upload" and question.question_id != "workpiece":
            return "当前对话界面不支持该问题类型"
        if question.type == "region_picker":
            return "当前对话界面暂不支持区域选择题"
        return None

    def _validate_turn(
        self,
        session: AgentSessionRecord,
        workpiece,
        turn: AgentModelingTurn,
    ) -> None:
        question_ids = [item.question_id for item in turn.questions]
        if len(question_ids) != len(set(question_ids)):
            raise PlannerUnavailableError("模型返回了重复问题，未应用任何修改，请重试")
        for question in turn.questions:
            error = self._question_error(question)
            if error:
                raise PlannerUnavailableError(f"模型返回的问题无效：{error}；未应用任何修改，请重试")
        if workpiece is None:
            if turn.overrides is not None or turn.catalog_material_id or turn.component_materials:
                raise PlannerUnavailableError("模型在缺少工件时返回了建模参数，未应用任何修改，请重试")
            return
        if not workpiece.unit_confirmed:
            if len(turn.questions) != 1 or turn.questions[0].question_id != "geometry_unit":
                raise PlannerUnavailableError("模型未生成 STL 单位确认问题，未应用任何修改，请重试")
            question = turn.questions[0]
            option_ids = {item.option_id for item in question.options}
            if question.type != "single_choice" or option_ids != {"um", "mm", "m"}:
                raise PlannerUnavailableError("模型返回的 STL 单位选项无效，未应用任何修改，请重试")
            if turn.overrides is not None or turn.catalog_material_id or turn.component_materials or turn.ready_for_review:
                raise PlannerUnavailableError("模型在单位未确认时试图修改草案，未应用任何修改，请重试")
            return
        if "geometry_unit" in question_ids:
            raise PlannerUnavailableError("STL 单位已确认，模型不应重复询问，未应用任何修改，请重试")
        catalog_ids = {item.material_id for item in list_materials()}
        catalog_names = {item.material.name for item in list_materials()}
        material_ids = [
            item for item in [turn.catalog_material_id, *[choice.material_id for choice in turn.component_materials]]
            if item is not None
        ]
        if any(item not in catalog_ids for item in material_ids):
            raise PlannerUnavailableError("模型引用了不存在的材料目录项，未应用任何修改，请重试")
        if turn.overrides and turn.overrides.material_name and turn.overrides.material_name not in catalog_names:
            raise PlannerUnavailableError("模型返回了目录外材料，未应用任何修改，请重试")
        component_ids = {item.component_id for item in workpiece.components}
        selected_components = [item.component_id for item in turn.component_materials]
        if len(selected_components) != len(set(selected_components)) or any(
            item not in component_ids for item in selected_components
        ):
            raise PlannerUnavailableError("模型引用了不存在或重复的组件材料，未应用任何修改，请重试")
        if turn.ready_for_review and (turn.questions or turn.missing_information):
            raise PlannerUnavailableError("模型同时声明已就绪和仍需补充的信息，未应用任何修改，请重试")

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))[:20]

    @staticmethod
    def _is_editable_study(study) -> bool:
        return (
            study.plan is not None
            and study.confirmation.status != "confirmed"
            and study.status.value in {"needs_input", "planned", "rejected"}
        )

    @staticmethod
    def _face_label(selector: FaceSelector) -> str:
        labels = {
            FaceSelector.X_MIN: "X 最小侧",
            FaceSelector.X_MAX: "X 最大侧",
            FaceSelector.Y_MIN: "Y 最小侧",
            FaceSelector.Y_MAX: "Y 最大侧",
            FaceSelector.Z_MIN: "Z 最小侧",
            FaceSelector.Z_MAX: "Z 最大侧",
        }
        return labels[selector]

    def _draft_feedback(
        self,
        session: AgentSessionRecord,
        *,
        copied_source_name: str | None = None,
    ) -> AgentSessionRecord:
        """Explain the current saved draft instead of returning a planner disclaimer."""
        if not session.study_id:
            return session
        study = self.repository.get_study(session.study_id)
        plan = study.plan
        if plan is None:
            return session
        sources = plan.heat_sources or ([plan.heat_source] if plan.heat_source else [])
        boundaries = "；".join(
            f"{self._face_label(item.selector)} {item.temperature_k:.6g} K"
            f"（{item.temperature_k - 273.15:.6g} ℃）"
            for item in plan.boundaries
        ) or "未设置"
        lines = []
        if copied_source_name:
            lines.append(f"已基于“{copied_source_name}”创建可编辑副本，原研究未被修改。")
        lines.extend([
            "已处理本轮输入，当前为未确认草案：",
            f"- 分析类型：{'稳态导热' if plan.analysis_type == 'steady_state_conduction' else '瞬态导热'}",
            f"- 材料：{plan.material.name}",
            f"- 定温边界：{boundaries}",
            (
                "- 热源："
                + (
                    f"{len(sources)} 个局部热源，总功率 "
                    f"{sum(item.total_power_w for item in sources):.6g} W"
                    if plan.heat_source_enabled and sources
                    else "未启用"
                )
            ),
            (
                "- 对流："
                + (
                    f"环境 {plan.convection.ambient_temperature_k:.6g} K"
                    f"（{plan.convection.ambient_temperature_k - 273.15:.6g} ℃），"
                    f"h={plan.convection.heat_transfer_coefficient_w_m2_k:.6g} W/(m²·K)"
                    if plan.global_convection_enabled and plan.convection
                    else "未启用"
                )
            ),
            f"- 目标单元尺寸：{plan.mesh.target_element_size_mm:.6g} mm",
        ])
        criterion = next((item for item in plan.criteria if item.metric == "max_temperature"), None)
        if criterion:
            lines.append(
                f"- 最高温度判据：不超过 {criterion.target.value:.6g} K"
                f"（{criterion.target.value - 273.15:.6g} ℃）"
            )
        if session.questions:
            lines.append("请继续在对话区确认："
                         + "；".join(item.prompt for item in session.questions))
        elif session.readiness:
            lines.append("仍需处理：" + "；".join(session.readiness))
        else:
            lines.append("参数已补齐。请查看右侧表单，核对后勾选最终确认并点击“开始仿真”。")
        answer = "\n".join(lines)[:4_000]
        return session.model_copy(update={
            "answer": answer,
            "citations": [],
            "messages": [*session.messages[-39:], ModelingMessage(role="assistant", content=answer)],
        })

    def _task_outcome(self, session: AgentSessionRecord, task, study) -> tuple[str, str, str | None]:
        if task.status == "succeeded":
            try:
                result = self.repository.get_result(session.study_id)
            except RecordNotFoundError:
                return "failed", "任务标记为完成，但结果文件不可读取。请在高级诊断中检查该任务。", "结果文件不可读取"
            conclusions = {
                "meets_criteria": "仿真已完成，当前输入下的全部工程判据满足。",
                "violates_criteria": "仿真已完成，但至少一项工程判据不满足。",
                "indeterminate": "仿真已完成，但现有判据或材料适用范围不足以给出完整工程结论。",
                "not_evaluated": "仿真已完成，但尚未设置可评估的工程判据。",
            }
            answer = (
                f"{conclusions[result.evaluation_status]}\n"
                f"最高温度：{result.temperature_max_k:.2f} K "
                f"({result.temperature_max_k - 273.15:.2f} ℃)。\n"
                + "\n".join(result.evaluation_summary[:4])
                + "\n请在右侧“结果”和“评估”页查看温度场、热点和能量平衡。"
            )
            return "completed", answer, None
        if task.status == "needs_review":
            blocked = study is not None and study.mesh_status.value == "blocked"
            answer = (
                "网格质量检查阻止了求解。当前研究已确认，为保留可追溯输入不能直接改写；"
                "请复制为可编辑修订，再检查区域映射，并同时调整目标单元尺寸和最大单轴区间数后重新确认。"
                if blocked else
                "网格已生成，但存在需要人工接受的质量风险。请在右侧“网格”页检查映射和风险后继续。"
            )
            return "needs_mesh_review", answer, task.message
        return "failed", f"仿真任务未完成：{task.message}", task.message or task.failure_reason

    def _refresh(self, session: AgentSessionRecord) -> AgentSessionRecord:
        if session.study_id:
            session = session.model_copy(update={
                "study_revision": self.repository.get_study(session.study_id).draft_revision,
            })
        if session.task_id:
            try:
                task = self.repository.get_task(session.task_id)
                study = self.repository.get_study(session.study_id) if session.study_id else None
                if task.status in TERMINAL_TASK_STATES:
                    status, answer, failure = self._task_outcome(session, task, study)
                    if session.status not in {"completed", "needs_mesh_review", "failed"}:
                        session = session.model_copy(update={
                            "status": status, "answer": answer, "failure": failure,
                            "messages": [*session.messages[-39:], ModelingMessage(role="assistant", content=answer)],
                            "revision": session.revision + 1,
                        })
                elif task.status in {"queued", "running", "cancelling"}:
                    session = session.model_copy(update={"status": task.status if task.status != "cancelling" else "running"})
                if study and study.mesh_status.value == "needs_review":
                    session = session.model_copy(update={"status": "needs_mesh_review"})
            except RecordNotFoundError:
                pass
        if session.mode in {"qa", "result_explanation"}:
            return session
        if session.status not in {"queued", "running", "needs_mesh_review", "completed", "failed"}:
            if not session.workpiece_id:
                status = "awaiting_geometry"
            else:
                workpiece = self.repository.get_workpiece(session.workpiece_id)
                if not workpiece.unit_confirmed:
                    status = "awaiting_unit"
                elif session.status == "ready_for_review":
                    status = "ready_for_review"
                else:
                    status = "clarifying"
            session = session.model_copy(update={
                "status": status,
            })
        return session

    def create(self, request: AgentSessionCreateRequest) -> AgentSessionRecord:
        study = self.repository.get_study(request.study_id) if request.study_id else None
        copied_source_name: str | None = None
        if study:
            if request.workpiece_id and request.workpiece_id != study.workpiece_id:
                raise ValueError("研究不属于指定工件")
            if request.project_id and request.project_id != study.project_id:
                raise ValueError("研究不属于指定项目")
            if _is_modeling_request(request.message) and not self._is_editable_study(study):
                copied_source_name = study.plan.study_name if study.plan else study.study_id
                study = self.service.copy_study(
                    study.study_id,
                    StudyCopyRequest(purpose=request.message),
                )
            request = request.model_copy(update={"workpiece_id": study.workpiece_id, "project_id": study.project_id})
        if request.project_id:
            self.repository.get_project(request.project_id)
        if request.workpiece_id:
            workpiece = self.repository.get_workpiece(request.workpiece_id)
            if request.project_id and workpiece.project_id != request.project_id:
                raise ValueError("工件不属于指定项目")
        session = AgentSessionRecord(
            session_id=f"assistant-{uuid.uuid4().hex}", project_id=request.project_id,
            workpiece_id=request.workpiece_id,
            study_id=study.study_id if study else None,
            study_revision=study.draft_revision if study else None,
            goal=request.message,
            mode="modeling" if _is_modeling_request(request.message) else "qa",
            input_provenance={
                **({"purpose": "user_statement"} if request.workpiece_id else {}),
                **_message_provenance(request.message),
            },
        )
        self.repository.save_agent_session(session)
        if session.mode == "qa":
            return self._qa(session, request.message)
        session = session.model_copy(update={"messages": [ModelingMessage(role="user", content=request.message)]})
        session = self._run_modeling_turn(session, request.message, append_user=False)
        if copied_source_name and session.answer:
            answer = f"已基于“{copied_source_name}”创建可编辑副本，原研究未被修改。\n{session.answer}"
            session = session.model_copy(update={
                "answer": answer,
                "messages": [*session.messages[:-1], ModelingMessage(role="assistant", content=answer)],
            })
        return self._save(session)

    def get(self, session_id: str) -> AgentSessionRecord:
        stored = self.repository.get_agent_session(session_id)
        refreshed = self._refresh(stored)
        return self._save(refreshed) if refreshed != stored else refreshed

    def list(self, project_id: str | None = None) -> list[AgentSessionRecord]:
        sessions = []
        for stored in self.repository.list_agent_sessions(project_id):
            refreshed = self._refresh(stored)
            sessions.append(self._save(refreshed) if refreshed != stored else refreshed)
        return sessions

    def bind_workpiece(self, session_id: str, request: AgentWorkpieceRequest) -> AgentSessionRecord:
        session = self.repository.get_agent_session(session_id)
        self._check_revision(session, request.expected_revision)
        workpiece = self.repository.get_workpiece(request.workpiece_id)
        if session.study_id and session.workpiece_id != workpiece.workpiece_id:
            raise ValueError("已关联研究的会话不能切换工件，请新建会话")
        if session.project_id and workpiece.project_id != session.project_id:
            raise ValueError("工件不属于当前 Agent 项目")
        session = session.model_copy(update={"workpiece_id": workpiece.workpiece_id, "project_id": workpiece.project_id,
                                             "revision": session.revision + 1, "answer": None})
        if session.mode == "modeling":
            message = "我已选择当前工件，请结合此前需求继续建立仿真草案。"
            session = self._run_modeling_turn(session, message)
        else:
            session = self._refresh(session)
        return self._save(session)

    def _ensure_study(self, session: AgentSessionRecord, purpose: str) -> AgentSessionRecord:
        if session.study_id:
            return session
        study = self.service.create_study(session.workpiece_id, purpose=purpose, require_confirmation=True)
        return session.model_copy(update={"study_id": study.study_id, "project_id": study.project_id,
                                          "study_revision": study.draft_revision,
                                          "mode": "modeling", "revision": session.revision + 1})

    def _turn_overrides(
        self,
        turn: AgentModelingTurn,
        workpiece,
        plan,
    ) -> SimulationOverrides | None:
        data = (
            {
                name: getattr(turn.overrides, name)
                for name in turn.overrides.model_fields_set
            }
            if turn.overrides is not None else {}
        )
        catalog = {entry.material_id: entry.material for entry in list_materials()}
        material_id = turn.catalog_material_id
        if material_id is None and turn.overrides and turn.overrides.material_name:
            material_id = next((
                entry_id for entry_id, material in catalog.items()
                if material.name == turn.overrides.material_name
            ), None)
        if material_id is not None:
            data["material_name"] = catalog[material_id].name
        if (
            "target_element_size_mm" in data
            and "max_axis_intervals" not in data
            and data["target_element_size_mm"] is not None
        ):
            requested_intervals = math.ceil(
                max(workpiece.dimensions_mm.as_tuple()) / data["target_element_size_mm"]
            )
            data["max_axis_intervals"] = min(100, max(plan.mesh.max_axis_intervals, requested_intervals))
        selected = {item.component_id: item.material_id for item in turn.component_materials}
        if workpiece.components and (material_id is not None or selected):
            current = {item.component_id: item for item in plan.component_materials}
            assignments = []
            for component in workpiece.components:
                selected_id = selected.get(component.component_id, material_id)
                previous = current.get(component.component_id)
                if selected_id is not None:
                    assignments.append({
                        "component_id": component.component_id,
                        "material_id": selected_id,
                        "material": catalog[selected_id],
                    })
                elif previous is not None:
                    assignments.append(previous.model_dump(mode="python"))
            data["component_materials"] = assignments
        return SimulationOverrides(**data) if data else None

    def _run_modeling_turn(
        self,
        session: AgentSessionRecord,
        user_message: str,
        *,
        append_user: bool = True,
    ) -> AgentSessionRecord:
        if append_user:
            session = session.model_copy(update={
                "messages": [*session.messages[-39:], ModelingMessage(role="user", content=user_message)],
            })
        workpiece = self.repository.get_workpiece(session.workpiece_id) if session.workpiece_id else None
        plan = self.repository.get_study(session.study_id).plan if session.study_id else None
        try:
            turn = self.modeling_provider.respond(
                workpiece=workpiece,
                current_plan=plan,
                conversation=session.messages,
                pending_questions=session.questions,
                user_message=user_message,
            )
            self._validate_turn(session, workpiece, turn)
            if workpiece is not None and workpiece.unit_confirmed:
                if not session.study_id:
                    session = self._ensure_study(session, session.goal)
                    plan = self.repository.get_study(session.study_id).plan
                assert session.study_id is not None and plan is not None
                overrides = self._turn_overrides(turn, workpiece, plan)
                if overrides is not None:
                    updated = self.modeling.update_draft(session.study_id, DraftUpdateRequest(
                        expected_revision=self.repository.get_study(session.study_id).draft_revision,
                        overrides=overrides,
                    ))
                    plan = updated.plan
                else:
                    plan = self.repository.get_study(session.study_id).plan
                assert plan is not None
                study = self.repository.get_study(session.study_id)
                policy_errors = study.policy.errors if study.policy and not study.policy.accepted else []
                readiness = self._dedupe([*turn.missing_information, *policy_errors])
                review_ready = turn.ready_for_review and not turn.questions and not readiness
            else:
                readiness = self._dedupe(turn.missing_information)
                review_ready = False
            if workpiece is None:
                status = "awaiting_geometry"
            elif not workpiece.unit_confirmed:
                status = "awaiting_unit"
            elif review_ready:
                status = "ready_for_review"
            else:
                status = "clarifying"
            answer = turn.answer
            return session.model_copy(update={
                "answer": answer,
                "citations": [],
                "questions": turn.questions,
                "readiness": readiness,
                "status": status,
                "failure": None,
                "study_revision": (
                    self.repository.get_study(session.study_id).draft_revision
                    if session.study_id else None
                ),
                "messages": [*session.messages[-39:], ModelingMessage(role="assistant", content=answer)],
                "revision": session.revision + 1,
            })
        except PlannerUnavailableError as exc:
            answer = f"{exc}。请点击“重试”继续本轮建模。"
            status = "awaiting_geometry" if workpiece is None else (
                "awaiting_unit" if not workpiece.unit_confirmed else "clarifying"
            )
            return session.model_copy(update={
                "answer": answer,
                "status": status,
                "failure": str(exc),
                "study_revision": (
                    self.repository.get_study(session.study_id).draft_revision
                    if session.study_id else None
                ),
                "messages": [*session.messages[-39:], ModelingMessage(role="assistant", content=answer)],
                "revision": session.revision + 1,
            })
        except (TypeError, ValueError):
            answer = "模型返回的建模参数无法安全应用，未修改草案。请点击“重试”重新生成本轮回复。"
            return session.model_copy(update={
                "answer": answer,
                "status": "clarifying",
                "failure": answer,
                "study_revision": (
                    self.repository.get_study(session.study_id).draft_revision
                    if session.study_id else None
                ),
                "messages": [*session.messages[-39:], ModelingMessage(role="assistant", content=answer)],
                "revision": session.revision + 1,
            })

    @staticmethod
    def _last_user_message(session: AgentSessionRecord) -> str:
        for message in reversed(session.messages):
            if message.role == "user":
                return message.content
        return session.goal or "建立热仿真研究"

    @staticmethod
    def _check_revision(session: AgentSessionRecord, expected: int) -> None:
        if session.revision != expected:
            raise ValueError(f"Agent 会话已更新：当前为第 {session.revision} 版，请重新载入")

    def _apply_answers(
        self,
        session: AgentSessionRecord,
        answers: list[AgentQuestionAnswer],
    ) -> tuple[AgentSessionRecord, str]:
        questions = {item.question_id: item for item in session.questions}
        answers_by_id = {item.question_id: item for item in answers}
        if len(answers_by_id) != len(answers):
            raise ValueError("同一 Agent 问题只能回答一次")
        unknown = set(answers_by_id) - set(questions)
        if unknown:
            raise ValueError("该 Agent 问题已失效，请重新载入会话")
        missing = [item.question_id for item in session.questions if item.required and item.question_id not in answers_by_id]
        if missing:
            raise ValueError("请先回答本轮所有必填问题")
        rendered: list[str] = []
        geometry_unit: LengthUnit | None = None
        for question_id, answer in answers_by_id.items():
            question = questions[question_id]
            option_map = {item.option_id: item.label for item in question.options}
            if question.type == "single_choice":
                if len(answer.option_ids) != 1 or answer.option_ids[0] not in option_map:
                    raise ValueError("请选择一个有效选项")
                value = option_map[answer.option_ids[0]]
                if question_id == "geometry_unit":
                    geometry_unit = LengthUnit(answer.option_ids[0])
            elif question.type == "multi_choice":
                if not answer.option_ids or any(item not in option_map for item in answer.option_ids):
                    raise ValueError("请选择有效选项")
                value = "、".join(option_map[item] for item in answer.option_ids)
            elif question.type == "number":
                number = answer.number_value
                if number is None or (question.minimum is not None and number < question.minimum) or (
                    question.maximum is not None and number > question.maximum
                ):
                    raise ValueError("数值不在问题允许的范围内")
                value = f"{number:g}{(' ' + question.unit) if question.unit else ''}"
            elif question.type == "text":
                if not answer.text_value or not answer.text_value.strip():
                    raise ValueError("请填写问题要求的说明")
                value = answer.text_value.strip()
            elif question.type == "region_picker":
                if not answer.region_ids:
                    raise ValueError("请选择至少一个区域")
                value = "、".join(answer.region_ids)
            else:
                raise ValueError("该问题需要先通过几何导入流程处理")
            rendered.append(f"对“{question.prompt}”的回答是：{value}。")
        response_message = "\n".join(rendered)
        session = session.model_copy(update={
            "questions": [],
            "messages": [*session.messages[-39:], ModelingMessage(role="user", content=response_message)],
            "input_provenance": {
                **session.input_provenance,
                **{item.question_id: "user_answer" for item in answers},
            },
        })
        if geometry_unit is not None:
            if session.workpiece_id is None:
                raise ValueError("当前会话未绑定 STL 工件")
            self.service.confirm_workpiece_unit(session.workpiece_id, geometry_unit)
        return session, response_message

    def turn(self, session_id: str, request: AgentTurnRequest) -> AgentSessionRecord:
        session = self.repository.get_agent_session(session_id)
        self._check_revision(session, request.expected_revision)
        answer_message = ""
        if request.answers:
            session, answer_message = self._apply_answers(session, request.answers)
        if request.message:
            message = request.message
            if (_looks_like_question(message) and not _looks_like_action(message)) or (
                session.mode in {"qa", "result_explanation"} and not _looks_like_action(message)
            ):
                return self._qa(session, message)
            session = session.model_copy(update={
                "mode": "modeling",
                "answer": None,
                "citations": [],
                "input_provenance": {**session.input_provenance, **_message_provenance(message)},
            })
            session = self._run_modeling_turn(session, message)
        elif answer_message:
            session = self._run_modeling_turn(session, answer_message, append_user=False)
        elif request.retry:
            session = self._run_modeling_turn(
                session,
                self._last_user_message(session),
                append_user=False,
            )
        else:
            raise ValueError("请输入问题、补充描述或回答当前选项")
        return self._save(session)

    def undo(self, session_id: str, expected_revision: int) -> AgentSessionRecord:
        session = self.repository.get_agent_session(session_id)
        self._check_revision(session, expected_revision)
        if not session.study_id:
            raise ValueError("当前没有可撤销的仿真修改")
        study = self.repository.get_study(session.study_id)
        updated = self.modeling.decide(session.study_id, ModelingDecisionRequest(
            expected_revision=study.draft_revision, action="undo",
        ))
        return self._save(self._refresh(session.model_copy(update={"revision": session.revision + 1,
                                                                    "study_id": updated.study_id})))

    def _launch_preflight(self, study) -> list[str]:
        if study.plan is None:
            raise ValueError("任务设置预检未通过：研究缺少可执行的仿真方案")
        workpiece = self.repository.get_workpiece(study.workpiece_id)
        report = validate_plan(workpiece, study.plan)
        if not report.accepted:
            details = "；".join(report.errors[:4])
            raise ValueError(f"任务设置预检未通过：{details}")

        checks = ["材料分配、边界条件、求解器和数值参数已通过静态校验"]
        if study.plan.solver.backend == "voxel_stl_v1":
            requested = study.plan.mesh.target_element_size_mm
            effective = report.derived.get("effective_pitch_mm")
            if isinstance(effective, (int, float)):
                if effective > requested + 1e-12:
                    checks.append(
                        f"目标单元尺寸为 {requested:g} mm，受最大单轴区间限制，"
                        f"实际将使用 {effective:g} mm"
                    )
                else:
                    checks.append(f"实际网格目标尺寸为 {effective:g} mm")
            checks.append("真实 STL 网格质量和区域映射将在任务的网格阶段复核")
        return checks

    def launch(self, session_id: str, request: AgentLaunchRequest) -> AgentSessionRecord:
        session = self.repository.get_agent_session(session_id)
        self._check_revision(session, request.expected_revision)
        if not request.summary_confirmed or not request.materials_confirmed or not request.form_reviewed:
            raise ValueError("请先查看右侧表单，并确认完整输入以及每个组件的材料和热物性")
        if not session.study_id:
            raise ValueError("尚未生成仿真研究")
        study = self.repository.get_study(session.study_id)
        if request.expected_study_revision != study.draft_revision:
            raise ValueError("仿真草案已更新，请重新检查摘要后确认")
        if session.task_id:
            raise ValueError("该会话已经提交仿真任务，请查看原任务")
        if session.status != "ready_for_review" or session.questions or session.readiness:
            readiness = session.readiness or ["请先完成 Agent 当前轮次的补充与确认"]
            raise ValueError("仿真输入尚未就绪：" + "；".join(readiness))
        preflight = self._launch_preflight(study)
        if study.confirmation.status != "confirmed":
            study = self.service.confirm_study(session.study_id, StudyConfirmationRequest(
                expected_revision=study.draft_revision, materials_confirmed=True,
                confirmed_by=request.confirmed_by,
            ))
        operation = "solve" if study.plan and study.plan.solver.backend == "analytic_box_v1" else "apply_and_solve"
        task = self.task_manager.submit(study.study_id, TaskCreateRequest(operation=operation))
        answer = (
            "任务设置预检已通过：" + "；".join(preflight)
            + "。已确认输入并提交后台仿真任务。Agent 将持续跟踪网格和求解状态，并在完成后给出结果结论。"
        )
        return self._save(session.model_copy(update={
            "mode": "launching", "status": "queued", "task_id": task.task_id,
            "revision": session.revision + 1, "answer": answer, "questions": [], "readiness": [],
            "messages": [*session.messages[-39:], ModelingMessage(role="assistant", content=answer)],
        }))
