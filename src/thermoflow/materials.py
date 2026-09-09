"""Small, versioned starter material catalog used by the first thermal workflow."""

from __future__ import annotations

from .models import MaterialCatalogEntry, ThermalMaterial


CATALOG_VERSION = "thermoflow-starter-2026.09"
CATALOG_NOTE = (
    "ThermoFlow 首期演示材料集的室温典型值；生产评估前应核对牌号、状态、批次和温度范围。"
)
CATALOG_CITATION = (
    "ThermoFlow Starter Material Data Sheet, revision 2026.09; "
    "demonstration values requiring engineering verification."
)


MATERIAL_CATALOG = [
    MaterialCatalogEntry(
        material_id="al-6061-t6",
        category="铝合金",
        catalog_version=CATALOG_VERSION,
        citation=CATALOG_CITATION,
        material=ThermalMaterial(
            name="铝合金 6061-T6",
            thermal_conductivity_w_m_k=167.0,
            density_kg_m3=2700.0,
            specific_heat_j_kg_k=896.0,
            source_basis=CATALOG_NOTE,
            source_type="database",
            source_reference=CATALOG_VERSION,
            source_version=CATALOG_VERSION,
            source_citation=CATALOG_CITATION,
            valid_temperature_min_k=273.15,
            valid_temperature_max_k=473.15,
        ),
    ),
    MaterialCatalogEntry(
        material_id="copper-c110",
        category="铜合金",
        catalog_version=CATALOG_VERSION,
        citation=CATALOG_CITATION,
        material=ThermalMaterial(
            name="C110 紫铜",
            thermal_conductivity_w_m_k=391.0,
            density_kg_m3=8940.0,
            specific_heat_j_kg_k=385.0,
            source_basis=CATALOG_NOTE,
            source_type="database",
            source_reference=CATALOG_VERSION,
            source_version=CATALOG_VERSION,
            source_citation=CATALOG_CITATION,
            valid_temperature_min_k=273.15,
            valid_temperature_max_k=473.15,
        ),
    ),
    MaterialCatalogEntry(
        material_id="steel-304",
        category="不锈钢",
        catalog_version=CATALOG_VERSION,
        citation=CATALOG_CITATION,
        material=ThermalMaterial(
            name="304 不锈钢",
            thermal_conductivity_w_m_k=16.2,
            density_kg_m3=8000.0,
            specific_heat_j_kg_k=500.0,
            source_basis=CATALOG_NOTE,
            source_type="database",
            source_reference=CATALOG_VERSION,
            source_version=CATALOG_VERSION,
            source_citation=CATALOG_CITATION,
            valid_temperature_min_k=273.15,
            valid_temperature_max_k=673.15,
        ),
    ),
    MaterialCatalogEntry(
        material_id="inconel-718",
        category="高温合金",
        catalog_version=CATALOG_VERSION,
        citation=CATALOG_CITATION,
        material=ThermalMaterial(
            name="Inconel 718",
            thermal_conductivity_w_m_k=11.4,
            density_kg_m3=8190.0,
            specific_heat_j_kg_k=435.0,
            source_basis=CATALOG_NOTE,
            source_type="database",
            source_reference=CATALOG_VERSION,
            source_version=CATALOG_VERSION,
            source_citation=CATALOG_CITATION,
            valid_temperature_min_k=273.15,
            valid_temperature_max_k=923.15,
        ),
    ),
]


def list_materials() -> list[MaterialCatalogEntry]:
    return [entry.model_copy(deep=True) for entry in MATERIAL_CATALOG]
