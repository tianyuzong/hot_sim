from thermoflow.manual import manual_stl_template
from thermoflow.models import CadFormat, SimulationOverrides
from thermoflow.planner import apply_user_overrides

from .helpers import box_workpiece


def manual_plan():
    workpiece = box_workpiece().model_copy(update={"cad_format": CadFormat.STL})
    return manual_stl_template(workpiece).plan


def test_manual_assumptions_follow_current_sources_temperature_and_boundaries():
    original = manual_plan()
    updated = apply_user_overrides(original, SimulationOverrides(
        initial_temperature_k=310.15, duration_s=120,
        ambient_temperature_k=298.15, convection_coefficient_w_m2_k=18,
        enable_heat_source=True, heat_source_power_w=25,
        fixed_boundaries=[{"selector": "face.xmin", "temperature_k": 320}],
    ))
    text = "\n".join(updated.assumptions)
    assert "初始温度 310.15 K（37 ℃）" in text
    assert "仿真时长 120 s" in text
    assert "定温边界 1 个" in text
    assert "全局对流环境 298.15 K（25 ℃）" in text
    assert "对流系数 18 W/(m²·K)" in text
    assert "已启用 1 个局部热源，总功率 25 W" in text
    assert "未添加内部热源" not in text
    assert "初始全工件为 20 ℃" not in text
    assert "不存在预设冷热端" not in text
    assert len(updated.assumptions) == len(original.assumptions)
    assert original.heat_source is None, "Describing a modified draft must preserve the original"

    disabled = apply_user_overrides(updated, SimulationOverrides(
        enable_heat_source=False, enable_global_convection=False,
        initial_temperature_k=323.15, duration_s=3600,
    ))
    disabled_text = "\n".join(disabled.assumptions)
    assert "初始温度 323.15 K（50 ℃）" in disabled_text
    assert "仿真时长 3600 s" in disabled_text
    assert "全局对流未启用" in disabled_text
    assert "已配置 1 个局部热源，当前未启用" in disabled_text
    assert "总功率 25 W" not in disabled_text

    enabled = apply_user_overrides(disabled, SimulationOverrides(
        enable_heat_source=True, heat_source_power_w=40, enable_global_convection=True,
    ))
    assert "已启用 1 个局部热源，总功率 40 W" in "\n".join(enabled.assumptions)
    assert "全局对流环境 298.15 K（25 ℃）" in "\n".join(enabled.assumptions)


def test_manual_steady_description_does_not_claim_an_initial_state_or_duration():
    steady = apply_user_overrides(manual_plan(), SimulationOverrides(
        analysis_type="steady_state_conduction",
    ))
    assert "稳态导热，不使用初始温度和仿真时长" in "\n".join(steady.assumptions)
    assert steady.initial_temperature_k is None
    assert steady.duration_s is None


def test_freeform_assumptions_are_not_rewritten_by_manual_template_refresh():
    assumptions = [
        "用户说明：初始全工件为 20 ℃，不存在预设冷热端。",
        "用户实验尚未添加内部热源；该句是实验背景，不是当前输入。",
        "各向同性、常物性固体导热；未建模相变和流体。",
    ]
    freeform = manual_plan().model_copy(update={"assumptions": assumptions})
    updated = apply_user_overrides(freeform, SimulationOverrides(
        enable_heat_source=True, heat_source_power_w=25, initial_temperature_k=310.15,
    ))
    assert updated.assumptions == assumptions
