from __future__ import annotations

from dataclasses import replace

import pytest

from bai.optic_category import (
    CompositionSpec,
    OpticCategoryError,
    OpticCategoryRegistry,
    OpticHandoff,
    OpticSpec,
    build_bug_fix_structural_loop_composition,
    build_bug_fix_structural_loop_registry,
    build_bug_fix_structural_role_optics,
)


def test_primitive_role_optics_declare_artifacts_and_bounded_surfaces() -> None:
    registry = build_bug_fix_structural_loop_registry()

    roadmap = registry.optic("RoadmapExtractorOptic")
    auditor = registry.optic("AuditorOptic")
    planner = registry.optic("PlannerOptic")
    patcher = registry.optic("PatcherOptic")
    reviewer = registry.optic("ReviewerOptic")

    assert roadmap.output_types == ("RepositoryMapView",)
    assert auditor.input_types == ("RepositoryMapView", "IssueClaimView")
    assert auditor.output_types == ("AuditReport",)
    assert planner.input_types == ("RepositoryMapView", "AuditReport")
    assert planner.output_types == ("RepairPlan",)
    assert patcher.input_types == ("AuditReport", "RepairPlan")
    assert patcher.output_types == ("PatchDiff",)
    assert reviewer.input_types == (
        "RepositoryMapView",
        "AuditReport",
        "RepairPlan",
        "PatchDiff",
    )
    assert reviewer.output_types == ("ReviewReport",)
    assert reviewer.optional_output_types == ("AgentMapDelta",)

    for optic in (roadmap, auditor, planner, patcher, reviewer):
        assert optic.read_projection
        assert optic.write_surface or optic.conditional_write_surface


def test_role_write_surfaces_fail_closed() -> None:
    registry = build_bug_fix_structural_loop_registry()

    registry.validate_write("AuditorOptic", "docs/audits/refund/reports/audit.md")
    registry.validate_write("ReviewerOptic", "docs/agents/runtime-map.md", output_type="AgentMapDelta")

    with pytest.raises(OpticCategoryError, match="write target outside"):
        registry.validate_write("AuditorOptic", "src/bai/core/policy.py")

    with pytest.raises(OpticCategoryError, match="write target outside"):
        registry.validate_write("PatcherOptic", "docs/agents/runtime-map.md")

    with pytest.raises(OpticCategoryError, match="write target outside"):
        registry.validate_write("ReviewerOptic", "docs/agents/runtime-map.md")

    with pytest.raises(OpticCategoryError, match="write target outside"):
        registry.validate_write("PatcherOptic", "README.md")

    with pytest.raises(OpticCategoryError, match="invalid write target"):
        registry.validate_write("AuditorOptic", "docs/audits/refund/reports/../plan/repair.md")

    with pytest.raises(OpticCategoryError, match="invalid write target"):
        registry.validate_write("AuditorOptic", "/docs/audits/refund/reports/audit.md")


def test_valid_bug_fix_structural_loop_composition_records_handoffs_and_lineage() -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = build_bug_fix_structural_loop_composition()

    result = registry.validate_composition(composition)

    assert result.composition_id == "BugFixStructuralLoopOptic"
    assert result.input_types == ("RepositoryFiles", "IssueClaimView")
    assert result.output_types == ("ReviewReport",)
    assert result.optional_output_types == ("PatchDiff", "AgentMapDelta")
    assert result.source_optics == (
        "RoadmapExtractorOptic",
        "AuditorOptic",
        "PlannerOptic",
        "PatcherOptic",
        "ReviewerOptic",
    )
    assert result.handoffs == (
        OpticHandoff(
            from_optic="RoadmapExtractorOptic",
            output_type="RepositoryMapView",
            to_optic="AuditorOptic",
            input_type="RepositoryMapView",
        ),
        OpticHandoff(
            from_optic="RoadmapExtractorOptic",
            output_type="RepositoryMapView",
            to_optic="PlannerOptic",
            input_type="RepositoryMapView",
        ),
        OpticHandoff(
            from_optic="AuditorOptic",
            output_type="AuditReport",
            to_optic="PlannerOptic",
            input_type="AuditReport",
        ),
        OpticHandoff(
            from_optic="AuditorOptic",
            output_type="AuditReport",
            to_optic="PatcherOptic",
            input_type="AuditReport",
        ),
        OpticHandoff(
            from_optic="PlannerOptic",
            output_type="RepairPlan",
            to_optic="PatcherOptic",
            input_type="RepairPlan",
        ),
        OpticHandoff(
            from_optic="RoadmapExtractorOptic",
            output_type="RepositoryMapView",
            to_optic="ReviewerOptic",
            input_type="RepositoryMapView",
        ),
        OpticHandoff(
            from_optic="AuditorOptic",
            output_type="AuditReport",
            to_optic="ReviewerOptic",
            input_type="AuditReport",
        ),
        OpticHandoff(
            from_optic="PlannerOptic",
            output_type="RepairPlan",
            to_optic="ReviewerOptic",
            input_type="RepairPlan",
        ),
        OpticHandoff(
            from_optic="PatcherOptic",
            output_type="PatchDiff",
            to_optic="ReviewerOptic",
            input_type="PatchDiff",
        ),
    )
    assert result.lineage_summary["PlannerOptic"] == ("RepositoryMapView", "AuditReport")
    assert result.lineage_summary["ReviewerOptic"] == (
        "RepositoryMapView",
        "AuditReport",
        "RepairPlan",
        "PatchDiff",
    )


def test_composition_validation_fails_closed_for_missing_required_handoffs() -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = build_bug_fix_structural_loop_composition()

    missing_audit_for_planner = replace(
        composition,
        handoffs=tuple(
            handoff
            for handoff in composition.handoffs
            if not (
                handoff.from_optic == "AuditorOptic"
                and handoff.to_optic == "PlannerOptic"
                and handoff.output_type == "AuditReport"
            )
        ),
    )
    with pytest.raises(OpticCategoryError, match="PlannerOptic requires input type AuditReport"):
        registry.validate_composition(missing_audit_for_planner)

    missing_plan_for_patcher = replace(
        composition,
        handoffs=tuple(
            handoff
            for handoff in composition.handoffs
            if not (
                handoff.from_optic == "PlannerOptic"
                and handoff.to_optic == "PatcherOptic"
                and handoff.output_type == "RepairPlan"
            )
        ),
    )
    with pytest.raises(OpticCategoryError, match="PatcherOptic requires input type RepairPlan"):
        registry.validate_composition(missing_plan_for_patcher)


def test_composition_cannot_externalize_intermediate_artifacts() -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = build_bug_fix_structural_loop_composition()
    smuggled_audit_report = replace(
        composition,
        handoffs=tuple(
            handoff
            for handoff in composition.handoffs
            if not (
                handoff.from_optic == "AuditorOptic"
                and handoff.to_optic == "PlannerOptic"
                and handoff.output_type == "AuditReport"
            )
        ),
        input_types=composition.input_types + ("AuditReport",),
    )

    with pytest.raises(OpticCategoryError, match="composition input type is produced"):
        registry.validate_composition(smuggled_audit_report)


def test_reviewer_patchdiff_input_requires_handoff_or_explicit_noop() -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = build_bug_fix_structural_loop_composition()
    without_patchdiff_for_reviewer = replace(
        composition,
        handoffs=tuple(
            handoff
            for handoff in composition.handoffs
            if not (
                handoff.from_optic == "PatcherOptic"
                and handoff.to_optic == "ReviewerOptic"
                and handoff.output_type == "PatchDiff"
            )
        ),
    )

    with pytest.raises(OpticCategoryError, match="ReviewerOptic requires input type PatchDiff"):
        registry.validate_composition(without_patchdiff_for_reviewer)

    noop_patch_review = replace(
        without_patchdiff_for_reviewer,
        noop_inputs={"ReviewerOptic": ("PatchDiff",)},
    )

    result = registry.validate_composition(noop_patch_review)

    assert result.noop_inputs["ReviewerOptic"] == ("PatchDiff",)
    assert result.lineage_summary["ReviewerOptic"] == (
        "RepositoryMapView",
        "AuditReport",
        "RepairPlan",
        "PatchDiff",
    )


def test_noop_inputs_are_limited_to_optional_composition_outputs() -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = build_bug_fix_structural_loop_composition()

    with pytest.raises(OpticCategoryError, match="noop input must be an optional output"):
        registry.validate_composition(
            replace(composition, noop_inputs={"PlannerOptic": ("AuditReport",)})
        )


def test_composition_required_and_optional_outputs_are_disjoint() -> None:
    with pytest.raises(OpticCategoryError, match="cannot also be optional"):
        CompositionSpec(
            composition_id="AmbiguousOutputOptic",
            source_optics=("RoadmapExtractorOptic",),
            handoffs=(
                OpticHandoff(
                    from_optic="RoadmapExtractorOptic",
                    output_type="RepositoryMapView",
                    to_optic="RoadmapExtractorOptic",
                    input_type="RepositoryMapView",
                ),
            ),
            input_types=("RepositoryFiles",),
            output_types=("ReviewReport",),
            optional_output_types=("ReviewReport",),
        )


def test_handoff_types_and_declared_inputs_are_validated_mechanically() -> None:
    registry = OpticCategoryRegistry(
        optics=(
            OpticSpec(
                optic_id="ProducerOptic",
                input_types=("ExternalClaim",),
                output_types=("AuditReport",),
                read_projection=("ExternalClaim",),
                write_surface=("docs/audits/<domain>/reports/",),
                lineage_required=("ExternalClaim",),
            ),
            OpticSpec(
                optic_id="ConsumerOptic",
                input_types=("RepairPlan",),
                output_types=("PatchDiff",),
                read_projection=("RepairPlan",),
                write_surface=("<repair-plan-authorized-files>",),
                lineage_required=("RepairPlan",),
            ),
        )
    )

    with pytest.raises(OpticCategoryError, match="handoff output type must match input type"):
        registry.validate_composition(
            CompositionSpec(
                composition_id="BadTypeMatch",
                source_optics=("ProducerOptic", "ConsumerOptic"),
                handoffs=(
                    OpticHandoff(
                        from_optic="ProducerOptic",
                        output_type="AuditReport",
                        to_optic="ConsumerOptic",
                        input_type="RepairPlan",
                    ),
                ),
                input_types=("ExternalClaim",),
                output_types=("PatchDiff",),
            )
        )

    with pytest.raises(OpticCategoryError, match="does not declare input type"):
        registry.validate_composition(
            CompositionSpec(
                composition_id="UndeclaredConsumerInput",
                source_optics=("ProducerOptic", "ConsumerOptic"),
                handoffs=(
                    OpticHandoff(
                        from_optic="ProducerOptic",
                        output_type="AuditReport",
                        to_optic="ConsumerOptic",
                        input_type="AuditReport",
                    ),
                ),
                input_types=("ExternalClaim", "RepairPlan"),
                output_types=("PatchDiff",),
            )
        )


def test_lineage_requirements_must_be_satisfied_by_external_or_upstream_artifacts() -> None:
    specs = list(build_bug_fix_structural_role_optics())
    specs = [
        replace(
            optic,
            lineage_required=("AuditReport", "RepairPlan", "RepositoryMapView"),
        )
        if optic.optic_id == "PatcherOptic"
        else optic
        for optic in specs
    ]
    registry = OpticCategoryRegistry(optics=tuple(specs))

    with pytest.raises(OpticCategoryError, match="lineage requirement"):
        registry.validate_composition(build_bug_fix_structural_loop_composition())
