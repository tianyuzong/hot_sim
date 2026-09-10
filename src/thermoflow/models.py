"""Strict domain contracts shared by the API, GPT planner, and solvers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrEnum(str, Enum):
    """Python 3.10-compatible string enum with 3.11 StrEnum semantics used here."""

    def __str__(self) -> str:
        return self.value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class WorkpieceKind(StrEnum):
    BOX = "box"
    CAD_FILE = "cad_file"


class CadFormat(StrEnum):
    STEP = "step"
    BREP = "brep"
    STL = "stl"


class LengthUnit(StrEnum):
    MICROMETER = "um"
    MILLIMETER = "mm"
    METER = "m"

    @property
    def scale_to_mm(self) -> float:
        return {"um": 0.001, "mm": 1.0, "m": 1_000.0}[self.value]


class DimensionsMM(StrictModel):
    """长方体沿三个坐标轴的尺寸，单位为毫米。"""

    x: float = Field(gt=0, le=1_000_000, title="X 方向长度", description="单位：毫米")
    y: float = Field(gt=0, le=1_000_000, title="Y 方向长度", description="单位：毫米")
    z: float = Field(gt=0, le=1_000_000, title="Z 方向长度", description="单位：毫米")

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


class ProjectCreateRequest(StrictModel):
    """创建一个可容纳几何版本和研究历史的项目。"""

    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("项目名称不能为空")
        return normalized


class ProjectUpdateRequest(ProjectCreateRequest):
    """当前项目可编辑元数据。"""


class ProjectRecord(StrictModel):
    """项目聚合根；研究通过 project_id 归入同一历史。"""

    schema_version: Literal["1.0"] = "1.0"
    project_id: str = Field(pattern=r"^project-[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    workpiece_ids: list[str] = Field(default_factory=list, max_length=100)
    active_workpiece_id: str | None = None

    @model_validator(mode="after")
    def validate_active_workpiece(self) -> ProjectRecord:
        if len(set(self.workpiece_ids)) != len(self.workpiece_ids):
            raise ValueError("项目中的几何版本不能重复")
        if (
            self.active_workpiece_id is not None
            and self.active_workpiece_id not in self.workpiece_ids
        ):
            raise ValueError("当前几何版本必须属于该项目")
        return self


class BoxWorkpieceInput(StrictModel):
    """注册长方体工件所需的全部输入。"""

    name: str = Field(min_length=1, max_length=128, title="工件名称")
    dimensions_mm: DimensionsMM = Field(title="工件尺寸")
    project_id: str | None = Field(
        default=None,
        pattern=r"^project-[0-9a-f]{32}$",
        description="可选的目标项目；为空时自动创建同名项目",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("工件名称不能为空")
        return normalized


class GeometryInspection(StrictModel):
    """CadFlow 几何检查结果。"""

    available: bool = Field(description="几何是否已成功检查并可供后续处理")
    engine: str = Field(description="执行几何检查的引擎")
    engine_version: str | None = Field(default=None, description="几何引擎版本")
    summary: dict[str, Any] = Field(default_factory=dict, description="几何与拓扑摘要")
    faces: list[dict[str, Any]] = Field(default_factory=list, description="可用于边界条件的面")
    diagnostics: list[str] = Field(default_factory=list, description="检查诊断信息")


class GeometryComponent(StrictModel):
    """从 STL 断开壳体得到的稳定候选组件。"""

    component_id: str = Field(pattern=r"^component-[0-9a-f]{12}$")
    name: str = Field(min_length=1, max_length=120)
    triangle_count: int = Field(ge=1)
    vertex_count: int = Field(ge=3)
    bbox_source: list[float] = Field(min_length=6, max_length=6)
    area_source2: float = Field(gt=0)
    watertight: bool


class ComponentUpdateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("组件名称不能为空")
        return normalized


class GeometryRegion(StrictModel):
    """可被界面和仿真规格稳定引用的几何区域。"""

    region_id: str = Field(pattern=r"^region-[0-9a-f]{12}$")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["bounding_plane", "component_surface", "native_face", "surface_patch"]
    triangle_ids: list[int] = Field(default_factory=list, max_length=200_000)
    selector: str = Field(min_length=1, max_length=120)
    component_ids: list[str] = Field(default_factory=list, max_length=100)
    supported_condition_kinds: list[
        Literal["fixed_temperature", "convection", "heat_flux", "radiation", "thermal_contact"]
    ] = Field(default_factory=list, max_length=5)


class WorkpieceRecord(StrictModel):
    """已登记工件及其几何检查信息。"""

    workpiece_id: str = Field(description="平台生成的工件唯一标识")
    project_id: str | None = Field(
        default=None,
        pattern=r"^project-[0-9a-f]{32}$",
        description="所属项目；旧数据迁移前可为空",
    )
    created_at: datetime = Field(default_factory=utc_now, description="登记时间（UTC）")
    kind: WorkpieceKind = Field(description="工件类型")
    name: str = Field(description="工件名称")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="工件内容指纹")
    dimensions_mm: DimensionsMM | None = Field(default=None, description="长方体尺寸")
    cad_format: CadFormat | None = Field(default=None, description="上传的 CAD 文件格式")
    original_filename: str | None = Field(default=None, description="原始文件名")
    stored_filename: str | None = Field(default=None, description="平台内部文件名")
    size_bytes: int | None = Field(default=None, ge=0, description="文件大小（字节）")
    geometry: GeometryInspection = Field(description="几何检查结果")
    source_dimensions: DimensionsMM | None = Field(
        default=None,
        description="STL 文件坐标中的包围盒尺寸；字段名沿用尺寸结构但尚未解释为毫米",
    )
    length_unit: LengthUnit | None = Field(default=None, description="用户确认的 STL 长度单位")
    unit_confirmed: bool = Field(default=True, description="STL 尺度是否已经由用户确认")
    unit_confirmed_at: datetime | None = None
    components: list[GeometryComponent] = Field(default_factory=list)
    regions: list[GeometryRegion] = Field(default_factory=list)


class WorkpieceUnitConfirmation(StrictModel):
    unit: LengthUnit


class FaceSelector(StrEnum):
    X_MIN = "face.xmin"
    X_MAX = "face.xmax"
    Y_MIN = "face.ymin"
    Y_MAX = "face.ymax"
    Z_MIN = "face.zmin"
    Z_MAX = "face.zmax"

    @property
    def axis(self) -> str:
        return self.value[5]

    @property
    def side(self) -> str:
        return self.value[6:]


class ThermalMaterial(StrictModel):
    """GPT 选择的均质各向同性材料参数。"""

    name: str = Field(min_length=1, max_length=100, description="材料名称")
    thermal_conductivity_w_m_k: float = Field(
        gt=0.01, le=5_000, description="导热系数，单位：W/(m·K)"
    )
    density_kg_m3: float = Field(gt=1, le=30_000, description="密度，单位：kg/m³")
    specific_heat_j_kg_k: float = Field(gt=1, le=20_000, description="比热容，单位：J/(kg·K)")
    emissivity: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="总半球发射率；未提供可靠数据时保持为空",
    )
    source_basis: str = Field(
        min_length=1,
        max_length=300,
        description="参数取值依据的简要说明，不包含隐藏推理过程",
    )
    source_type: Literal["database", "user", "suggestion"] = "suggestion"
    source_reference: str | None = Field(default=None, max_length=300)
    source_version: str | None = Field(default=None, max_length=120)
    source_citation: str | None = Field(default=None, max_length=500)
    valid_temperature_min_k: float | None = Field(default=None, ge=0)
    valid_temperature_max_k: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_temperature_range(self) -> ThermalMaterial:
        if (
            self.valid_temperature_min_k is not None
            and self.valid_temperature_max_k is not None
            and self.valid_temperature_min_k >= self.valid_temperature_max_k
        ):
            raise ValueError("材料适用温度下限必须低于上限")
        return self


class ComponentMaterialAssignment(StrictModel):
    component_id: str = Field(min_length=1, max_length=80)
    material_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    material: ThermalMaterial


class MaterialCatalogEntry(StrictModel):
    material_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    category: str = Field(min_length=1, max_length=80)
    catalog_version: str = Field(min_length=1, max_length=120)
    citation: str = Field(min_length=1, max_length=500)
    material: ThermalMaterial


class FixedTemperatureBoundary(StrictModel):
    """施加在一个外表面上的固定温度边界条件。"""

    kind: Literal["fixed_temperature"] = "fixed_temperature"
    selector: FaceSelector = Field(default=FaceSelector.X_MIN, description="未指定区域时使用的包围盒面")
    region_id: str | None = Field(default=None, pattern=r"^region-[0-9a-f]{12}$")
    temperature_k: float = Field(ge=1, le=5_000, description="绝对温度，单位：K")


class Point3DMM(StrictModel):
    """工件坐标系中的三维点，单位为毫米。"""

    x: float = Field(ge=-1_000_000, le=1_000_000, description="X 坐标，单位：毫米")
    y: float = Field(ge=-1_000_000, le=1_000_000, description="Y 坐标，单位：毫米")
    z: float = Field(ge=-1_000_000, le=1_000_000, description="Z 坐标，单位：毫米")

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


class VolumetricHeatSource(StrictModel):
    """映射到体素域中的点、线、矩形面或长方体总功率热源。"""

    kind: Literal["volumetric_power"] = "volumetric_power"
    source_id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(default="热源", min_length=1, max_length=80)
    shape: Literal["point", "line", "surface", "volume"] = Field(
        default="point", description="热源几何类型"
    )
    placement: Literal["surface", "embedded"] = Field(
        default="embedded", description="贴附重建表面或嵌入重建体素域"
    )
    center_mm: Point3DMM = Field(description="热源中心在 STL 坐标系中的位置")
    total_power_w: float = Field(gt=0, le=10_000_000, description="热源总功率，单位：W")
    radius_mm: float = Field(gt=0, le=1_000_000, description="点半径或线半径，单位：毫米")
    embedding_depth_mm: float = Field(
        default=0.0, ge=0, le=1_000_000, description="嵌入重建体素域的目标深度，单位：毫米"
    )
    end_mm: Point3DMM | None = Field(default=None, description="线热源终点")
    surface_normal_axis: Literal["x", "y", "z"] | None = Field(
        default=None, description="矩形面热源的法向轴"
    )
    surface_width_mm: float | None = Field(
        default=None, gt=0, le=1_000_000, description="矩形面热源宽度，单位：毫米"
    )
    surface_height_mm: float | None = Field(
        default=None, gt=0, le=1_000_000, description="矩形面热源高度，单位：毫米"
    )
    surface_thickness_mm: float | None = Field(
        default=None, gt=0, le=1_000_000, description="矩形面热源映射厚度，单位：毫米"
    )
    volume_width_mm: float | None = Field(
        default=None, gt=0, le=1_000_000, description="长方体热源 X 向尺寸，单位：毫米"
    )
    volume_height_mm: float | None = Field(
        default=None, gt=0, le=1_000_000, description="长方体热源 Y 向尺寸，单位：毫米"
    )
    volume_depth_mm: float | None = Field(
        default=None, gt=0, le=1_000_000, description="长方体热源 Z 向尺寸，单位：毫米"
    )


class ConvectionBoundary(StrictModel):
    """施加于未定温外表面的环境对流散热条件。"""

    kind: Literal["convection"] = "convection"
    ambient_temperature_k: float = Field(ge=1, le=5_000, description="环境温度，单位：K")
    heat_transfer_coefficient_w_m2_k: float = Field(
        gt=0,
        le=1_000_000,
        description="对流换热系数，单位：W/(m²·K)",
    )


class SurfaceHeatFluxBoundary(StrictModel):
    kind: Literal["heat_flux"] = "heat_flux"
    region_id: str = Field(pattern=r"^region-[0-9a-f]{12}$")
    heat_flux_w_m2: float = Field(ge=-1e9, le=1e9, description="表面热流密度，W/m²，正值流入固体")


class SurfaceConvectionBoundary(StrictModel):
    kind: Literal["convection"] = "convection"
    region_id: str = Field(pattern=r"^region-[0-9a-f]{12}$")
    ambient_temperature_k: float = Field(ge=1, le=5_000)
    heat_transfer_coefficient_w_m2_k: float = Field(gt=0, le=1_000_000)


class SurfaceRadiationBoundary(StrictModel):
    kind: Literal["radiation"] = "radiation"
    region_id: str = Field(pattern=r"^region-[0-9a-f]{12}$")
    radiation_temperature_k: float = Field(ge=1, le=5_000)
    emissivity: float = Field(gt=0, le=1)
    emissivity_source: str = Field(min_length=1, max_length=300, description="发射率来源，由用户确认")

    @field_validator("emissivity_source")
    @classmethod
    def require_source(cls, value):
        if not value.strip():
            raise ValueError("必须填写发射率来源")
        return value.strip()


class ThermalContactCondition(StrictModel):
    """用户确认的两个组件表面之间的法向热接触传热条件。"""

    kind: Literal["thermal_contact"] = "thermal_contact"
    source_component_id: str = Field(min_length=1, max_length=80)
    target_component_id: str = Field(min_length=1, max_length=80)
    source_region_id: str = Field(pattern=r"^region-[0-9a-f]{12}$")
    target_region_id: str = Field(pattern=r"^region-[0-9a-f]{12}$")
    contact_resistance_m2_k_w: float = Field(
        gt=0,
        le=1e8,
        description="单位面积接触热阻，单位：m²·K/W",
    )
    contact_area_m2: float | None = Field(
        default=None,
        gt=0,
        le=1e6,
        description="有效接触面积；为空时由映射面自动估算，单位：m²",
    )
    max_gap_mm: float | None = Field(
        default=None,
        gt=0,
        le=1e6,
        description="允许的几何间隙上限；为空时使用一个体素尺度，单位：mm",
    )


SurfaceThermalBoundary = SurfaceHeatFluxBoundary | SurfaceConvectionBoundary | SurfaceRadiationBoundary


class MeshPlan(StrictModel):
    """GPT 选择的离散化参数。"""

    target_element_size_mm: float = Field(gt=0, le=100_000, description="目标单元尺寸，单位：毫米")
    max_axis_intervals: int = Field(ge=2, le=100, description="单个坐标轴的最大区间数")
    refinement_passes: int = Field(ge=0, le=3, description="网格细化次数")


class SolverPlan(StrictModel):
    """GPT 选择的已注册求解器及其数值参数。"""

    backend: Literal["analytic_box_v1", "voxel_stl_v1"]
    relative_tolerance: float = Field(ge=1e-12, le=1e-3, description="相对收敛容差")
    max_iterations: int = Field(ge=10, le=100_000, description="最大迭代次数")


class Quantity(StrictModel):
    value: float
    unit: str = Field(min_length=1, max_length=32)


class EngineeringCriterion(StrictModel):
    metric: Literal["max_temperature", "min_temperature", "energy_balance_error"]
    operator: Literal["less_than", "less_or_equal", "greater_than", "greater_or_equal"]
    target: Quantity


class ConfirmationRecord(StrictModel):
    status: Literal["draft", "needs_input", "confirmed"] = "draft"
    confirmed_by: str | None = Field(default=None, max_length=120)
    confirmed_at: datetime | None = None


class SimulationPlan(StrictModel):
    """GPT 输出并由平台校验的完整热仿真方案。"""

    schema_version: Literal["1.0"] = "1.0"
    study_name: str = Field(min_length=1, max_length=120, description="仿真研究名称")
    analysis_type: Literal["steady_state_conduction", "transient_conduction"] = "steady_state_conduction"
    initial_temperature_k: float | None = Field(default=None, ge=1, le=5_000)
    duration_s: float | None = Field(default=None, gt=0, le=31_536_000)
    time_step_s: float | None = Field(default=None, gt=0, le=31_536_000)
    material: ThermalMaterial = Field(description="材料及其热物性")
    boundaries: list[FixedTemperatureBoundary] = Field(
        min_length=0, max_length=24, description="固定温度边界，可引用已保存的表面区域"
    )
    surface_conditions: list[SurfaceThermalBoundary] = Field(default_factory=list, max_length=48)
    contacts: list[ThermalContactCondition] = Field(default_factory=list, max_length=100)
    heat_source_enabled: bool = True
    global_convection_enabled: bool = True
    heat_source: VolumetricHeatSource | None = Field(
        default=None,
        description="STL 研究中的局部体积功率热源；旧方案可为空",
    )
    heat_sources: list[VolumetricHeatSource] = Field(
        default_factory=list,
        max_length=16,
        description="局部热源列表；为空时兼容使用旧版 heat_source 字段",
    )
    convection: ConvectionBoundary | None = Field(
        default=None,
        description="未施加固定温度的外表面对环境的散热条件",
    )
    mesh: MeshPlan = Field(description="网格方案")
    solver: SolverPlan = Field(description="求解器方案")
    assumptions: list[str] = Field(min_length=1, max_length=12, description="建模假设")
    decision_summary: str = Field(min_length=1, max_length=800, description="工程决策摘要")
    confidence: float = Field(ge=0, le=1, description="GPT 对方案合理性的置信度")
    purpose: str = Field(default="", max_length=1_000)
    analyses: list[Literal["steady_thermal", "transient_thermal"]] = Field(default_factory=lambda: ["steady_thermal"])
    component_materials: list[ComponentMaterialAssignment] = Field(default_factory=list)
    criteria: list[EngineeringCriterion] = Field(default_factory=list, max_length=12)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    unsupported_physics: list[str] = Field(default_factory=list, max_length=20)
    confirmation: ConfirmationRecord = Field(default_factory=ConfirmationRecord)


class SimulationProjectSnapshot(StrictModel):
    project_id: str = Field(pattern=r"^project-[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=128)


class SimulationComponentSnapshot(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    region_refs: list[str] = Field(default_factory=list, max_length=300)


class SimulationRegionSnapshot(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["bounding_plane", "component_surface", "native_face", "surface_patch"]
    triangle_ids: list[int] = Field(default_factory=list, max_length=200_000)
    selector: str = Field(min_length=1, max_length=120)
    component_refs: list[str] = Field(default_factory=list, max_length=100)
    supported_condition_kinds: list[
        Literal["fixed_temperature", "convection", "heat_flux", "radiation", "thermal_contact"]
    ] = Field(default_factory=list, max_length=5)


class QuantityVector3(StrictModel):
    x: Quantity
    y: Quantity
    z: Quantity


class SimulationGeometrySnapshot(StrictModel):
    asset_id: str = Field(min_length=1, max_length=80)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: LengthUnit
    dimensions: QuantityVector3
    components: list[SimulationComponentSnapshot] = Field(min_length=1, max_length=100)
    regions: list[SimulationRegionSnapshot] = Field(default_factory=list, max_length=300)


class QuantityRange(StrictModel):
    minimum: Quantity | None = None
    maximum: Quantity | None = None


class MaterialSourceSnapshot(StrictModel):
    type: Literal["database", "user", "suggestion"]
    basis: str = Field(min_length=1, max_length=300)
    reference: str | None = Field(default=None, max_length=300)
    version: str | None = Field(default=None, max_length=120)
    citation: str | None = Field(default=None, max_length=500)


class SimulationMaterialSnapshot(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    thermal_conductivity: Quantity
    density: Quantity
    specific_heat_capacity: Quantity
    emissivity: float | None = Field(default=None, ge=0, le=1)
    valid_temperature_range: QuantityRange
    source: MaterialSourceSnapshot


class SimulationMaterialAssignment(StrictModel):
    component_id: str = Field(min_length=1, max_length=80)
    material_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    material: SimulationMaterialSnapshot


class SimulationMaterialsSpec(StrictModel):
    assignments: list[SimulationMaterialAssignment] = Field(min_length=1, max_length=100)


class SimulationPhysicsSpec(StrictModel):
    analyses: list[Literal["steady_thermal", "transient_thermal"]] = Field(min_length=1)
    model: Literal["steady_state_conduction", "transient_conduction"] = "steady_state_conduction"
    unsupported: list[str] = Field(default_factory=list, max_length=20)


class SimulationEnvironmentSpec(StrictModel):
    ambient_temperature: Quantity | None = None
    medium: str | None = Field(default=None, max_length=120)


class SimulationScenarioSpec(StrictModel):
    purpose: str = Field(default="", max_length=1_000)
    environment: SimulationEnvironmentSpec
    duration: Quantity | None = None


class SimulationPoint3D(StrictModel):
    x: Quantity
    y: Quantity
    z: Quantity


class SimulationFixedTemperatureCondition(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["fixed_temperature"] = "fixed_temperature"
    target_refs: list[str] = Field(min_length=1, max_length=100)
    temperature: Quantity


class SimulationHeatSourceCondition(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["volumetric_power"] = "volumetric_power"
    target_refs: list[str] = Field(min_length=1, max_length=100)
    shape: Literal["point", "line", "surface", "volume"]
    placement: Literal["surface", "embedded"]
    center: SimulationPoint3D
    total_power: Quantity
    radius: Quantity
    embedding_depth: Quantity
    end: SimulationPoint3D | None = None
    surface_normal_axis: Literal["x", "y", "z"] | None = None
    surface_width: Quantity | None = None
    surface_height: Quantity | None = None
    surface_thickness: Quantity | None = None
    volume_width: Quantity | None = None
    volume_height: Quantity | None = None
    volume_depth: Quantity | None = None


class SimulationConvectionCondition(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["convection"] = "convection"
    target_refs: list[str] = Field(min_length=1, max_length=100)
    ambient_temperature: Quantity
    heat_transfer_coefficient: Quantity


class SimulationSurfaceFluxCondition(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["heat_flux"] = "heat_flux"
    target_refs: list[str] = Field(min_length=1, max_length=100)
    inward_heat_flux: Quantity


class SimulationRadiationCondition(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["radiation"] = "radiation"
    target_refs: list[str] = Field(min_length=1, max_length=100)
    radiation_temperature: Quantity
    emissivity: Quantity
    emissivity_source: str = Field(min_length=1, max_length=300)
    model: Literal["diffuse_gray_to_large_isothermal_surroundings"] = "diffuse_gray_to_large_isothermal_surroundings"


class SimulationThermalContactCondition(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["thermal_contact"] = "thermal_contact"
    source_component_ref: str = Field(min_length=1, max_length=80)
    target_component_ref: str = Field(min_length=1, max_length=80)
    source_region_ref: str = Field(min_length=1, max_length=80)
    target_region_ref: str = Field(min_length=1, max_length=80)
    contact_resistance: Quantity
    contact_area: Quantity | None = None
    max_gap: Quantity | None = None


SimulationThermalCondition = (
    SimulationFixedTemperatureCondition
    | SimulationHeatSourceCondition
    | SimulationConvectionCondition
    | SimulationSurfaceFluxCondition
    | SimulationRadiationCondition
)


class SimulationInitialTemperatureCondition(StrictModel):
    kind: Literal["initial_temperature"] = "initial_temperature"
    target_refs: list[str] = Field(min_length=1)
    temperature: Quantity


class SimulationConditionsSpec(StrictModel):
    initial: list[SimulationInitialTemperatureCondition] = Field(default_factory=list)
    thermal: list[SimulationThermalCondition] = Field(min_length=0, max_length=100)
    mechanical: list[dict[str, Any]] = Field(default_factory=list)
    contacts: list[SimulationThermalContactCondition] = Field(default_factory=list, max_length=100)


class SimulationMeshSpec(StrictModel):
    global_size: Quantity
    max_axis_intervals: int = Field(ge=2, le=100)
    refinement_passes: int = Field(ge=0, le=3)
    local_refinements: list[dict[str, Any]] = Field(default_factory=list)


class SimulationSolverSpec(StrictModel):
    type: Literal["analytic_box_v1", "voxel_stl_v1"]
    relative_tolerance: Quantity
    max_iterations: int = Field(ge=10, le=100_000)
    time_step: Quantity | None = None
    time_integration: Literal["backward_euler"] | None = None


class SimulationSpec(StrictModel):
    """用户确认后跨 Agent、表单、网格、求解器和报告共享的版本化契约。"""

    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4"] = "1.0"
    project: SimulationProjectSnapshot
    geometry: SimulationGeometrySnapshot
    materials: SimulationMaterialsSpec
    physics: SimulationPhysicsSpec
    scenario: SimulationScenarioSpec
    conditions: SimulationConditionsSpec
    mesh: SimulationMeshSpec
    solver: SimulationSolverSpec
    criteria: list[EngineeringCriterion] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(min_length=1, max_length=12)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    confirmation: ConfirmationRecord
    source_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    geometry_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlannerProvenance(StrictModel):
    """仿真方案的模型与提示词来源。"""

    provider: str
    model: str
    response_id: str | None = None
    prompt_version: str
    attempts: int = Field(ge=1, le=3)


class PolicyIssue(StrictModel):
    """A policy diagnostic that can point the editor to affected inputs."""

    code: str
    severity: Literal["error", "warning"]
    message: str
    suggestion: str = ""
    fields: list[str] = Field(default_factory=list)


class PolicyReport(StrictModel):
    """确定性安全策略的校验结果。"""

    accepted: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[PolicyIssue] = Field(default_factory=list)
    derived: dict[str, Any] = Field(default_factory=dict)


class StudyStatus(StrEnum):
    NEEDS_INPUT = "needs_input"
    READY = "ready"
    PLANNED = "planned"
    REJECTED = "rejected"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MeshStatus(StrEnum):
    """A study's persisted pre-solve mesh lifecycle."""

    NOT_GENERATED = "not_generated"
    GENERATING = "generating"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


class AgentRunStatus(StrEnum):
    """受控仿真 Agent 的运行状态。"""

    RUNNING = "running"
    GOAL_MET = "goal_met"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class SimulationOverrides(StrictModel):
    fixed_boundaries: list[FixedTemperatureBoundary] | None = Field(default=None, min_length=0, max_length=24)
    surface_conditions: list[SurfaceThermalBoundary] | None = Field(default=None, max_length=48)
    contacts: list[ThermalContactCondition] | None = Field(default=None, max_length=100)
    enable_heat_source: bool | None = None
    enable_global_convection: bool | None = None
    heat_sources: list[VolumetricHeatSource] | None = Field(default=None, max_length=16)
    analysis_type: Literal["steady_state_conduction", "transient_conduction"] | None = None
    initial_temperature_k: float | None = Field(default=None, ge=1, le=5_000)
    duration_s: float | None = Field(default=None, gt=0, le=31_536_000)
    time_step_s: float | None = Field(default=None, gt=0, le=31_536_000)
    """用户可选的稳态导热参数；空字段继续使用规划器的选择。"""

    material_name: str | None = Field(default=None, min_length=1, max_length=100)
    thermal_conductivity_w_m_k: float | None = Field(default=None, gt=0.01, le=5_000)
    density_kg_m3: float | None = Field(default=None, gt=1, le=30_000)
    specific_heat_j_kg_k: float | None = Field(default=None, gt=1, le=20_000)
    heat_axis: Literal["x", "y", "z"] | None = None
    min_face_temperature_k: float | None = Field(default=None, ge=1, le=5_000)
    max_face_temperature_k: float | None = Field(default=None, ge=1, le=5_000)
    heat_source_x_mm: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    heat_source_y_mm: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    heat_source_z_mm: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    heat_source_shape: Literal["point", "line", "surface", "volume"] | None = None
    heat_source_placement: Literal["surface", "embedded"] | None = None
    heat_source_embedding_depth_mm: float | None = Field(default=None, ge=0, le=1_000_000)
    heat_source_power_w: float | None = Field(default=None, gt=0, le=10_000_000)
    heat_source_radius_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    heat_source_end_x_mm: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    heat_source_end_y_mm: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    heat_source_end_z_mm: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    heat_source_surface_axis: Literal["x", "y", "z"] | None = None
    heat_source_surface_width_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    heat_source_surface_height_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    heat_source_surface_thickness_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    heat_source_volume_width_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    heat_source_volume_height_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    heat_source_volume_depth_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    ambient_temperature_k: float | None = Field(default=None, ge=1, le=5_000)
    convection_coefficient_w_m2_k: float | None = Field(default=None, gt=0, le=1_000_000)
    target_element_size_mm: float | None = Field(default=None, gt=0, le=100_000)
    max_axis_intervals: int | None = Field(default=None, ge=2, le=100)
    relative_tolerance: float | None = Field(default=None, ge=1e-12, le=1e-3)
    max_iterations: int | None = Field(default=None, ge=10, le=100_000)
    component_materials: list[ComponentMaterialAssignment] | None = None
    criteria: list[EngineeringCriterion] | None = None

    @field_validator("material_name")
    @classmethod
    def normalize_material_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("材料名称不能为空")
        return normalized


class ModelingMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)
    created_at: datetime = Field(default_factory=utc_now)


class ModelingChange(StrictModel):
    field: str
    label: str
    before: str
    after: str


class ModelingProposal(StrictModel):
    base_revision: int = Field(ge=0)
    geometry_sha256: str
    plan: SimulationPlan
    changes: list[ModelingChange]
    validation_errors: list[str] = Field(default_factory=list)


class ModelingSession(StrictModel):
    messages: list[ModelingMessage] = Field(default_factory=list, max_length=40)
    proposal: ModelingProposal | None = None
    undo_plan: SimulationPlan | None = None
    suggested_fields: list[str] = Field(default_factory=list, max_length=40)
    undo_suggested_fields: list[str] = Field(default_factory=list, max_length=40)
    history_retained: bool = True


class DraftUpdateRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    overrides: SimulationOverrides | None = None
    purpose: str | None = Field(default=None, max_length=1_000)


class ModelingMessageRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=4_000)
    session_history: list[ModelingMessage] = Field(default_factory=list, max_length=40)

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入需要补充或修改的建模条件")
        return value.strip()


class ModelingDecisionRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    action: Literal["apply", "dismiss", "undo"]


class StudyRecord(StrictModel):
    """仿真研究、自动补齐方案、校验报告和运行状态。"""

    study_id: str
    draft_revision: int = Field(default=0, ge=0)
    modeling: ModelingSession = Field(default_factory=ModelingSession)
    active_task_id: str | None = None
    source_study_id: str | None = Field(
        default=None,
        description="复制或 Agent 变体的来源研究；初始研究为空",
    )
    workpiece_id: str
    project_id: str | None = Field(
        default=None,
        pattern=r"^project-[0-9a-f]{32}$",
        description="所属项目；旧研究迁移前可为空",
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    status: StudyStatus
    overrides: SimulationOverrides | None = None
    plan: SimulationPlan | None = None
    simulation_spec: SimulationSpec | None = None
    planner: PlannerProvenance | None = None
    policy: PolicyReport | None = None
    failure: str | None = None
    purpose: str = ""
    confirmation: ConfirmationRecord = Field(default_factory=ConfirmationRecord)
    input_snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mesh_status: MeshStatus = MeshStatus.NOT_GENERATED
    mesh_snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mesh_failure: str | None = None
    evaluation_status: Literal[
        "not_evaluated", "meets_criteria", "violates_criteria", "indeterminate"
    ] = "not_evaluated"
    evaluation_summary: list[str] = Field(default_factory=list)


class ProjectWorkspace(StrictModel):
    project: ProjectRecord
    workpieces: list[WorkpieceRecord]
    studies: list[StudyRecord]


class StudyCreateRequest(StrictModel):
    """创建研究时提供工件，并可锁定部分仿真参数。"""

    workpiece_id: str = Field(
        min_length=1,
        max_length=80,
        description="已登记工件的唯一标识",
    )
    overrides: SimulationOverrides | None = Field(
        default=None,
        description="可选的用户参数；未指定项继续由 GPT 决定",
    )
    purpose: str = Field(default="", max_length=1_000)
    planning_mode: Literal["ai", "manual"] = "ai"
    require_confirmation: Literal[True] = True


class StudyCopyRequest(StrictModel):
    """复制研究时可调整名称、用途和结构化参数，但不能直接求解。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    purpose: str | None = Field(default=None, max_length=1_000)
    overrides: SimulationOverrides | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("研究名称不能为空")
        return normalized


class StudyConfirmationRequest(StrictModel):
    expected_revision: int | None = Field(default=None, ge=0)
    overrides: SimulationOverrides | None = None
    purpose: str | None = Field(default=None, max_length=1_000)
    materials_confirmed: bool = Field(
        default=False,
        description="确认人已逐组件核对并明确接受当前材料及热物性。",
    )
    confirmed_by: str = Field(default="当前用户", min_length=1, max_length=120)


class TaskCreateRequest(StrictModel):
    operation: Literal["mesh", "solve", "apply_and_solve"]
    timeout_seconds: int | None = Field(default=None, ge=1, le=7_200)


class TaskRecord(StrictModel):
    task_id: str = Field(pattern=r"^task-[0-9a-f]{32}$")
    study_id: str
    project_id: str | None = None
    study_name: str
    operation: Literal["mesh", "solve", "apply_and_solve"]
    status: Literal["queued", "running", "cancelling", "succeeded", "needs_review", "cancelled", "failed", "timed_out", "interrupted"] = "queued"
    stage: Literal["queued", "validating", "geometry", "meshing", "quality", "assembly", "solving", "fields", "saving", "finished"] = "queued"
    progress: float | None = Field(default=None, ge=0, le=100)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    timeout_seconds: int = Field(ge=1, le=7_200)
    input_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cancellable: bool = True
    message: str = "等待计算资源"
    diagnostic_code: str = Field(pattern=r"^TF-[A-F0-9]{10}$")
    failure_reason: Literal["invalid_input", "resource_exhausted", "solver_error", "worker_lost", "cancelled", "timeout"] | None = None


class ArtifactRef(StrictModel):
    """仿真生成文件的索引和内容指纹。"""

    name: str
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class MeshReviewRequest(StrictModel):
    accept_warnings: Literal[True]
    confirmed_by: str = Field(default="当前用户", min_length=1, max_length=120)


class MeshQualityMetrics(StrictModel):
    """Deterministic quality metrics for the generated voxel mesh."""

    element_type: Literal["voxel_hexahedron"] = "voxel_hexahedron"
    cell_volume_mm3: float = Field(gt=0)
    maximum_aspect_ratio: float = Field(ge=1)
    maximum_skewness: float = Field(ge=0, le=1)
    minimum_orthogonality: float = Field(ge=0, le=1)
    occupied_layers: dict[str, int]
    connected_regions: int = Field(ge=1)
    expected_components: int = Field(ge=1)
    volume_deviation_percent: float | None = Field(default=None, ge=0)


class MeshRecord(StrictModel):
    boundary_mapping: list[dict[str, Any]] = Field(default_factory=list)
    """Persisted mesh preview generated from a specific study input snapshot."""

    schema_version: Literal["1.0"] = "1.0"
    study_id: str
    workpiece_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    generator: Literal["voxel_mesher_v1"] = "voxel_mesher_v1"
    generator_version: Literal["1.0"] = "1.0"
    plan_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    geometry_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pitch_mm: float = Field(gt=0)
    domain_mode: Literal["enclosed_volume", "reconstructed_open_surface"]
    grid: dict[str, int]
    active_cells: int = Field(ge=1)
    surface_cells: int = Field(ge=1)
    quality_status: Literal["passed", "warning", "blocked"]
    review_status: Literal["not_required", "pending", "accepted"]
    reviewed_by: str | None = Field(default=None, max_length=120)
    reviewed_at: datetime | None = None
    quality: MeshQualityMetrics
    warnings: list[str] = Field(default_factory=list, max_length=20)
    notices: list[str] = Field(default_factory=list, max_length=20)
    cell_samples_mm: list[tuple[float, float, float]] = Field(
        min_length=1,
        max_length=4_000,
        description="真实表面体素单元中心的 X、Y、Z 坐标，单位 mm",
    )
    artifacts: list[ArtifactRef]


class TemperatureFieldPreview(StrictModel):
    """用于网页温度云图与剖切视图的体素降采样。"""

    sampling: Literal[
        "surface_voxel_centers", "surface_and_volume_voxel_centers"
    ] = "surface_and_volume_voxel_centers"
    pitch_mm: float = Field(gt=0)
    total_surface_cells: int = Field(ge=1)
    samples: list[tuple[float, float, float, float]] = Field(
        min_length=1,
        max_length=4_000,
        description="表面体素中心的 X、Y、Z 毫米坐标和温度 K",
    )
    total_volume_cells: int | None = Field(default=None, ge=1)
    volume_samples: list[tuple[float, float, float, float]] = Field(
        default_factory=list,
        max_length=8_000,
        description="工件内部体素中心的 X、Y、Z 毫米坐标和温度 K",
    )


class HeatFluxFieldPreview(StrictModel):
    """用于网页热流矢量视图的表面体素降采样。"""

    sampling: Literal["surface_voxel_centers"] = "surface_voxel_centers"
    pitch_mm: float = Field(gt=0)
    total_surface_cells: int = Field(ge=1)
    minimum_magnitude_w_m2: float = Field(ge=0)
    maximum_magnitude_w_m2: float = Field(ge=0)
    samples: list[tuple[float, float, float, float, float, float, float]] = Field(
        min_length=1,
        max_length=1_200,
        description="表面体素中心坐标、热流 X/Y/Z 分量和热流密度幅值，单位 mm 与 W/m²",
    )


class ThermalTimeStep(StrictModel):
    boundary_power_balance: dict[str, Quantity] = Field(default_factory=dict)
    energy_balance_reference_power: Quantity | None = None
    index: int = Field(ge=0)
    time_s: float = Field(ge=0)
    temperature_min_k: float
    temperature_max_k: float
    minimum_position_mm: tuple[float, float, float]
    maximum_position_mm: tuple[float, float, float]
    energy_balance_relative_error: float | None = Field(default=None, ge=0)
    stored_energy_change_j: float | None = None
    linear_residual_relative: float | None = Field(default=None, ge=0)
    field_artifact: str
    vtk_artifact: str


class ThermalTimeFrame(StrictModel):
    study_id: str
    step: ThermalTimeStep
    temperature_field_preview: TemperatureFieldPreview
    heat_flux_field_preview: HeatFluxFieldPreview


class SimulationResult(StrictModel):
    boundary_mapping: list[dict[str, Any]] = Field(default_factory=list)
    """热仿真的结构化数值结果。"""

    schema_version: Literal["1.0"] = "1.0"
    study_id: str
    workpiece_id: str
    solver_backend: str
    compute_backend: str = "cpu-scipy"
    compute_device: str | None = None
    compute_fallback_reason: str | None = None
    completed_at: datetime = Field(default_factory=utc_now)
    solver_version: str = "1.0"
    analysis_type: Literal["steady_state_conduction", "transient_conduction"] = "steady_state_conduction"
    time_steps: list[ThermalTimeStep] = Field(default_factory=list, max_length=201)
    time_s: float | None = None
    temperature_min_over_time_k: float | None = None
    temperature_max_over_time_k: float | None = None
    maximum_energy_balance_error_over_time: float | None = None
    temperature_min_k: float
    temperature_max_k: float
    heat_rate_w: float
    heat_source_power_w: float | None = None
    heat_source_cells: int | None = Field(default=None, ge=1)
    heat_source_mapping: dict[str, Any] | None = None
    heat_source_mappings: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    temperature_field_preview: TemperatureFieldPreview | None = None
    heat_flux_field_preview: HeatFluxFieldPreview | None = None
    heat_flux_w_m2: float
    thermal_resistance_k_w: float | None
    boundary_power_balance: dict[str, Quantity] = Field(default_factory=dict)
    nonlinear_convergence: list[dict[str, Any]] = Field(default_factory=list)
    energy_balance_reference_power: Quantity | None = None
    energy_balance_relative_error: float = Field(ge=0)
    grid: dict[str, int]
    artifacts: list[ArtifactRef]
    assumptions: list[str]
    evaluation_status: Literal[
        "not_evaluated", "meets_criteria", "violates_criteria", "indeterminate"
    ] = "not_evaluated"
    evaluation_summary: list[str] = Field(default_factory=list)


class StudyComparisonRequest(StrictModel):
    study_ids: list[str] = Field(min_length=2, max_length=4)
    baseline_study_id: str | None = None
    quantity: Literal["temperature"] = "temperature"

    @model_validator(mode="after")
    def validate_studies(self) -> StudyComparisonRequest:
        if len(set(self.study_ids)) != len(self.study_ids):
            raise ValueError("比较研究不能重复")
        if self.baseline_study_id is not None and self.baseline_study_id not in self.study_ids:
            raise ValueError("基准研究必须包含在待比较研究中")
        return self


class ComparisonScale(StrictModel):
    minimum: Quantity
    maximum: Quantity


class ComparisonStudySummary(StrictModel):
    study_id: str
    study_name: str
    workpiece_id: str
    workpiece_name: str
    completed_at: datetime
    evaluation_status: Literal[
        "not_evaluated", "meets_criteria", "violates_criteria", "indeterminate"
    ]
    temperature_min: Quantity
    temperature_max: Quantity
    hotspot: SimulationPoint3D | None = None
    heat_rate: Quantity
    thermal_resistance: Quantity | None
    energy_balance_relative_error: Quantity


class TemperatureDifferenceField(StrictModel):
    """候选研究减去基准研究的真实同坐标采样差值。"""

    sampling: Literal["matching_voxel_centers"] = "matching_voxel_centers"
    pitch: Quantity
    minimum_delta: Quantity
    maximum_delta: Quantity
    surface_samples: list[tuple[float, float, float, float]] = Field(
        min_length=1,
        max_length=4_000,
        description="表面体素中心 X/Y/Z 毫米坐标和温差 K",
    )
    volume_samples: list[tuple[float, float, float, float]] = Field(
        default_factory=list,
        max_length=8_000,
        description="体体素中心 X/Y/Z 毫米坐标和温差 K",
    )


class StudyDifferenceSummary(StrictModel):
    candidate_study_id: str
    candidate_study_name: str
    temperature_min_delta: Quantity
    temperature_max_delta: Quantity
    heat_rate_delta: Quantity
    thermal_resistance_delta: Quantity | None
    field: TemperatureDifferenceField | None = None
    field_unavailable_reason: str | None = None


class StudyComparisonResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: str = Field(pattern=r"^project-[0-9a-f]{32}$")
    quantity: Literal["temperature"] = "temperature"
    unit: Literal["K"] = "K"
    baseline_study_id: str
    common_scale: ComparisonScale
    studies: list[ComparisonStudySummary] = Field(min_length=2, max_length=4)
    differences: list[StudyDifferenceSummary] = Field(min_length=1, max_length=3)


class AgentGoalRequest(StrictModel):
    """工程师交给仿真 Agent 的目标与权限边界。"""

    instruction: str = Field(
        min_length=1,
        max_length=1_000,
        description="自然语言目标；明确数值字段优先于从文本中解析的约束",
    )
    target_max_temperature_k: float | None = Field(default=None, ge=1, le=1_000_000)
    target_min_temperature_k: float | None = Field(default=None, ge=1, le=1_000_000)
    temperature_tolerance_k: float = Field(default=1.0, gt=0, le=100)
    max_energy_balance_relative_error: float = Field(default=1e-5, gt=0, le=0.1)
    max_rounds: int = Field(default=3, ge=1, le=5)
    allow_power_adjustment: bool = True
    allow_mesh_refinement: bool = True

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent 目标不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_temperature_window(self) -> AgentGoalRequest:
        if (
            self.target_min_temperature_k is not None
            and self.target_max_temperature_k is not None
            and self.target_min_temperature_k > self.target_max_temperature_k
        ):
            raise ValueError("最低目标温度不能高于最高目标温度")
        return self


class ResolvedAgentGoal(StrictModel):
    """合并显式输入与自然语言解析后的可判定目标。"""

    target_max_temperature_k: float | None = None
    target_min_temperature_k: float | None = None
    temperature_tolerance_k: float
    max_energy_balance_relative_error: float
    max_rounds: int
    allow_power_adjustment: bool
    allow_mesh_refinement: bool
    parsed_from_instruction: list[str] = Field(default_factory=list)


class AgentStep(StrictModel):
    """一个可审计的 Agent 观察、判断或工具调用。"""

    sequence: int = Field(ge=1)
    phase: Literal["observe", "evaluate", "decide", "act", "finish"]
    tool: Literal[
        "inspect_geometry",
        "inspect_result",
        "evaluate_constraints",
        "adjust_parameters",
        "run_solver",
        "finish",
    ]
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=800)
    study_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    changes: dict[str, Any] = Field(default_factory=dict)


class AgentRunRecord(StrictModel):
    """从一个基准研究出发的有界仿真优化轨迹。"""

    run_id: str
    base_study_id: str
    selected_study_id: str
    workpiece_id: str
    project_id: str | None = Field(
        default=None,
        pattern=r"^project-[0-9a-f]{32}$",
        description="所属项目；旧 Agent 记录迁移前可为空",
    )
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: AgentRunStatus
    goal: AgentGoalRequest
    resolved_goal: ResolvedAgentGoal
    provider: str
    model: str
    prompt_version: str
    response_ids: list[str] = Field(default_factory=list)
    rounds_completed: int = Field(default=0, ge=0, le=5)
    candidate_study_ids: list[str] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list, max_length=40)
    summary: str | None = Field(default=None, max_length=1_000)
    failure: str | None = None


class AgentCitation(StrictModel):
    """A user-visible, server-validated source attached to an Agent answer."""

    citation_id: str = Field(min_length=1, max_length=100)
    source_type: Literal["thermoflow_doc", "study_context", "general_knowledge"]
    title: str = Field(min_length=1, max_length=200)
    section: str | None = Field(default=None, max_length=200)
    label: str = Field(min_length=1, max_length=300)


class AgentQuestionOption(StrictModel):
    option_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class AgentQuestion(StrictModel):
    """Typed UI question; values are interpreted only by the server registry."""

    question_id: str = Field(min_length=1, max_length=120)
    group: str = Field(min_length=1, max_length=80)
    type: Literal["single_choice", "multi_choice", "number", "text", "file_upload", "region_picker"]
    prompt: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=500)
    required: bool = True
    unit: str | None = Field(default=None, max_length=32)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: list[AgentQuestionOption] = Field(default_factory=list, max_length=32)


class AgentQuestionAnswer(StrictModel):
    """Answer envelope; the question definition determines which value is legal."""

    question_id: str = Field(min_length=1, max_length=120)
    option_ids: list[str] = Field(default_factory=list, max_length=32)
    number_value: float | None = None
    text_value: str | None = Field(default=None, max_length=4_000)
    region_ids: list[str] = Field(default_factory=list, max_length=24)


class AgentComponentMaterialSelection(StrictModel):
    """A catalog material selected by the modeling model for one known component."""

    component_id: str = Field(min_length=1, max_length=80)
    material_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")


class AgentModelingTurn(StrictModel):
    """A schema-constrained LLM turn for the conversational modeling workflow."""

    answer: str = Field(min_length=1, max_length=4_000)
    overrides: SimulationOverrides | None = None
    catalog_material_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{1,79}$",
    )
    component_materials: list[AgentComponentMaterialSelection] = Field(
        default_factory=list,
        max_length=100,
    )
    questions: list[AgentQuestion] = Field(default_factory=list, max_length=12)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    ready_for_review: bool = False


class AgentSessionRecord(StrictModel):
    """Persistent read-only Q&A and controlled modeling session."""

    session_id: str = Field(pattern=r"^assistant-[0-9a-f]{32}$")
    revision: int = Field(default=0, ge=0)
    mode: Literal["qa", "modeling", "result_explanation", "launching"] = "qa"
    status: Literal[
        "awaiting_goal", "awaiting_geometry", "awaiting_unit", "clarifying",
        "ready_for_review", "queued", "running", "needs_mesh_review", "completed", "failed",
    ] = "awaiting_goal"
    project_id: str | None = Field(default=None, pattern=r"^project-[0-9a-f]{32}$")
    workpiece_id: str | None = None
    study_id: str | None = None
    study_revision: int | None = Field(default=None, ge=0)
    task_id: str | None = None
    goal: str = Field(default="", max_length=4_000)
    messages: list[ModelingMessage] = Field(default_factory=list, max_length=40)
    questions: list[AgentQuestion] = Field(default_factory=list, max_length=12)
    citations: list[AgentCitation] = Field(default_factory=list, max_length=8)
    answer: str | None = Field(default=None, max_length=4_000)
    readiness: list[str] = Field(default_factory=list, max_length=20)
    input_provenance: dict[str, str] = Field(default_factory=dict, max_length=200)
    updated_at: datetime = Field(default_factory=utc_now)
    failure: str | None = Field(default=None, max_length=500)


class AgentSessionCreateRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4_000)
    project_id: str | None = Field(default=None, pattern=r"^project-[0-9a-f]{32}$")
    workpiece_id: str | None = None
    study_id: str | None = None

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入问题或仿真需求")
        return value


class AgentTurnRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    message: str | None = Field(default=None, max_length=4_000)
    answers: list[AgentQuestionAnswer] = Field(default_factory=list, max_length=12)
    retry: bool = False

    @model_validator(mode="after")
    def require_turn_content(self) -> AgentTurnRequest:
        if not (self.message and self.message.strip()) and not self.answers and not self.retry:
            raise ValueError("请输入问题、补充描述或回答当前选项")
        if self.message is not None:
            self.message = self.message.strip()
        return self


class AgentWorkpieceRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    workpiece_id: str = Field(min_length=1, max_length=80)


class AgentLaunchRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    expected_study_revision: int = Field(ge=0)
    summary_confirmed: bool = False
    materials_confirmed: bool = False
    form_reviewed: bool = False
    confirmed_by: str = Field(default="当前用户", min_length=1, max_length=120)


class StlSimulationResponse(StrictModel):
    """单次 STL 上传、参数自动补齐与同步求解的完整响应。"""

    workpiece: WorkpieceRecord
    study: StudyRecord
    result: SimulationResult
