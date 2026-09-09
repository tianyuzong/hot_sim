from __future__ import annotations

from pathlib import Path
import trimesh

from thermoflow.models import (
    FaceSelector,
    FixedTemperatureBoundary,
    LengthUnit,
    MeshReviewRequest,
    SimulationOverrides,
    StudyConfirmationRequest,
    ThermalContactCondition,
)
from tests.test_confirmed_service import _service


def _two_shell_service(tmp_path: Path, center: float = 3):
    service, repository = _service(tmp_path)
    left = trimesh.creation.box(extents=[4, 4, 4])
    left.apply_translation([-center, 0, 0])
    right = trimesh.creation.box(extents=[4, 4, 4])
    right.apply_translation([center, 0, 0])
    payload = trimesh.util.concatenate([left, right]).export(file_type="stl")
    workpiece = service.register_stl("contact-coupon.stl", payload)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    component_regions = {
        component.component_id: next(
            region for region in workpiece.regions
            if region.kind == "component_surface" and component.component_id in region.component_ids
        )
        for component in workpiece.components
    }
    return service, repository, workpiece, component_regions


def _contact(workpiece, regions, resistance=1e-4):
    first, second = workpiece.components
    return ThermalContactCondition(
        source_component_id=first.component_id,
        target_component_id=second.component_id,
        source_region_id=regions[first.component_id].region_id,
        target_region_id=regions[second.component_id].region_id,
        contact_resistance_m2_k_w=resistance,
        max_gap_mm=2,
    )


def test_contact_is_typed_and_requires_distinct_component_regions(tmp_path):
    service, _, workpiece, regions = _two_shell_service(tmp_path)
    first = workpiece.components[0]
    invalid = _contact(workpiece, regions).model_copy(update={
        "target_component_id": first.component_id,
        "target_region_id": regions[first.component_id].region_id,
    })
    draft = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(contacts=[invalid]),
        require_confirmation=True,
    )
    assert not draft.policy.accepted
    assert any("必须不同" in error for error in draft.policy.errors)


def test_separated_components_exchange_heat_through_contact_and_conserve_energy(tmp_path):
    service, repository, workpiece, regions = _two_shell_service(tmp_path)
    study = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(
            fixed_boundaries=[
                FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=300),
                FixedTemperatureBoundary(selector=FaceSelector.X_MAX, temperature_k=500),
            ],
            enable_heat_source=False,
            enable_global_convection=False,
            contacts=[_contact(workpiece, regions)],
            target_element_size_mm=1,
        ),
        require_confirmation=True,
    )
    assert study.policy.accepted, study.policy.errors
    confirmed = service.confirm_study(
        study.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    mesh = service.generate_mesh(confirmed.study_id)
    if mesh.review_status == "pending":
        service.confirm_mesh(
            confirmed.study_id,
            MeshReviewRequest(accept_warnings=True, confirmed_by="接触热阻回归"),
        )
    completed = service.run_study(confirmed.study_id)
    assert completed.status.value == "succeeded"
    result = repository.get_result(confirmed.study_id)
    contact_mapping = next(item for item in result.boundary_mapping if item.get("kind") == "thermal_contact")
    assert contact_mapping["mapped_pairs"] > 0
    assert contact_mapping["mapped_area"]["value"] == contact_mapping["effective_contact_area"]["value"]
    assert result.boundary_power_balance["thermal_contact_transfer"].value > 0
    assert result.energy_balance_relative_error < 1e-8
    assert any("组件热接触有限体积连接" in item for item in result.assumptions)


def test_contact_with_no_nearby_faces_blocks_mesh_instead_of_connecting_remote_parts(tmp_path):
    service, _, workpiece, regions = _two_shell_service(tmp_path, center=10)
    first, second = workpiece.components
    condition = _contact(workpiece, regions).model_copy(update={"max_gap_mm": 0.01})
    study = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(
            fixed_boundaries=[FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=300)],
            enable_heat_source=False,
            enable_global_convection=False,
            contacts=[condition],
            target_element_size_mm=1,
        ),
        require_confirmation=True,
    )
    confirmed = service.confirm_study(
        study.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    mesh = service.generate_mesh(confirmed.study_id)
    assert mesh.quality_status == "blocked"
    assert any("热接触" in warning for warning in mesh.warnings)
