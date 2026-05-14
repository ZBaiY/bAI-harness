from __future__ import annotations

from pathlib import Path

import pytest

from bai.optic_category import (
    OpticCategoryError,
    build_bug_fix_structural_loop_registry,
)


def test_bug_fix_structural_loop_optic_e2e_is_valid_and_traceable() -> None:
    registry = build_bug_fix_structural_loop_registry()

    result = registry.validate_registered("BugFixStructuralLoopOptic")

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
    assert [handoff.output_type for handoff in result.handoffs] == [
        "RepositoryMapView",
        "RepositoryMapView",
        "AuditReport",
        "AuditReport",
        "RepairPlan",
        "RepositoryMapView",
        "AuditReport",
        "RepairPlan",
        "PatchDiff",
    ]
    assert result.lineage_summary == {
        "RoadmapExtractorOptic": ("RepositoryFiles",),
        "AuditorOptic": ("RepositoryMapView", "IssueClaimView"),
        "PlannerOptic": ("RepositoryMapView", "AuditReport"),
        "PatcherOptic": ("AuditReport", "RepairPlan"),
        "ReviewerOptic": (
            "RepositoryMapView",
            "AuditReport",
            "RepairPlan",
            "PatchDiff",
        ),
    }


def test_bug_fix_structural_loop_invalid_registered_composition_fails_closed() -> None:
    registry = build_bug_fix_structural_loop_registry()
    composition = registry.composition("BugFixStructuralLoopOptic")
    broken = composition.__class__(
        composition_id="BrokenBugFixStructuralLoopOptic",
        source_optics=composition.source_optics,
        handoffs=composition.handoffs[:-1],
        input_types=composition.input_types,
        output_types=composition.output_types,
        optional_output_types=composition.optional_output_types,
    )

    with pytest.raises(OpticCategoryError, match="ReviewerOptic requires input type PatchDiff"):
        registry.validate_composition(broken)


def test_optic_category_layer_has_no_execution_runtime_imports() -> None:
    source = Path("src/bai/optic_category.py").read_text()

    forbidden_imports = (
        "subprocess",
        "bai.execution.harness",
        "bai.execution.effects",
        "bai.execution.scheduler",
        "bai.execution.agent",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source


def test_bug_fix_role_names_match_design_doc() -> None:
    design = Path(
        "docs/dev/2026-05-13-patchoptic-composable-optics/"
        "optic-category-agent-workflow.md"
    )

    text = design.read_text()

    assert "RoadmapExtractorOptic" in text
    assert "AuditorOptic" in text
    assert "PlannerOptic" in text
    assert "PatcherOptic" in text
    assert "ReviewerOptic" in text
    assert "BugFixStructuralLoopOptic" in text
