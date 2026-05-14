"""Declarative Optic Category contracts for structured role handoffs.

This module models role optics as typed artifact transformers. The category
layer is stricter than a graph: an edge is valid only when a declared output
artifact feeds a declared input artifact, and every role's lineage and write
surface remain bounded by its own contract.

The objects here do not execute a workflow. They validate and expose the named
composition so callers can reason about handoffs before any agent, tool, test,
or mutation path is involved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


class OpticCategoryError(ValueError):
    """Raised when an optic category declaration fails closed."""


@dataclass(frozen=True, slots=True)
class OpticSpec:
    """Primitive role optic: declared inputs, outputs, visibility, and writes."""

    optic_id: str
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    read_projection: tuple[str, ...]
    write_surface: tuple[str, ...]
    lineage_required: tuple[str, ...]
    optional_output_types: tuple[str, ...] = ()
    conditional_write_surface: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    role: str = ""
    phase: str = ""

    def __post_init__(self) -> None:
        _validate_token(self.optic_id, "optic id")
        input_types = _normalize_tokens(self.input_types, label=f"{self.optic_id} inputs")
        output_types = _normalize_tokens(self.output_types, label=f"{self.optic_id} outputs")
        optional_output_types = _normalize_tokens(
            self.optional_output_types,
            label=f"{self.optic_id} optional outputs",
            allow_empty=True,
        )
        overlap = set(output_types) & set(optional_output_types)
        if overlap:
            raise OpticCategoryError(
                f"{self.optic_id} output types cannot also be optional: "
                + ", ".join(sorted(overlap))
            )
        object.__setattr__(self, "input_types", input_types)
        object.__setattr__(self, "output_types", output_types)
        object.__setattr__(self, "optional_output_types", optional_output_types)
        object.__setattr__(
            self,
            "read_projection",
            _normalize_tokens(self.read_projection, label=f"{self.optic_id} reads"),
        )
        object.__setattr__(
            self,
            "write_surface",
            _normalize_tokens(
                self.write_surface,
                label=f"{self.optic_id} writes",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "lineage_required",
            _normalize_tokens(
                self.lineage_required,
                label=f"{self.optic_id} lineage",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "conditional_write_surface",
            _freeze_surface_map(
                self.conditional_write_surface,
                declared_outputs=output_types + optional_output_types,
                label=f"{self.optic_id} conditional writes",
            ),
        )
        if self.role:
            _validate_token(self.role, f"{self.optic_id} role")
        if self.phase:
            _validate_token(self.phase, f"{self.optic_id} phase")

    @property
    def all_output_types(self) -> tuple[str, ...]:
        return self.output_types + self.optional_output_types


@dataclass(frozen=True, slots=True)
class OpticHandoff:
    """Typed edge between primitive optics in a composed optic."""

    from_optic: str
    output_type: str
    to_optic: str
    input_type: str

    def __post_init__(self) -> None:
        _validate_token(self.from_optic, "handoff source optic")
        _validate_token(self.output_type, "handoff output type")
        _validate_token(self.to_optic, "handoff target optic")
        _validate_token(self.input_type, "handoff input type")


@dataclass(frozen=True, slots=True)
class CompositionSpec:
    """Composed optic declaration saved by name, not an execution plan.

    A composed optic records primitive role order, typed handoffs, external
    inputs, exposed outputs, and any explicit no-op inputs such as a review
    path where no patch artifact exists.
    """

    composition_id: str
    source_optics: tuple[str, ...]
    handoffs: tuple[OpticHandoff, ...]
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    optional_output_types: tuple[str, ...] = ()
    noop_inputs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_token(self.composition_id, "composition id")
        object.__setattr__(
            self,
            "source_optics",
            _normalize_tokens(self.source_optics, label=f"{self.composition_id} optics"),
        )
        object.__setattr__(
            self,
            "handoffs",
            _normalize_handoffs(self.handoffs, label=f"{self.composition_id} handoffs"),
        )
        object.__setattr__(
            self,
            "input_types",
            _normalize_tokens(self.input_types, label=f"{self.composition_id} inputs"),
        )
        object.__setattr__(
            self,
            "output_types",
            _normalize_tokens(self.output_types, label=f"{self.composition_id} outputs"),
        )
        output_types = self.output_types
        object.__setattr__(
            self,
            "optional_output_types",
            _normalize_tokens(
                self.optional_output_types,
                label=f"{self.composition_id} optional outputs",
                allow_empty=True,
            ),
        )
        overlap = set(output_types) & set(self.optional_output_types)
        if overlap:
            raise OpticCategoryError(
                f"{self.composition_id} output types cannot also be optional: "
                + ", ".join(sorted(overlap))
            )
        object.__setattr__(
            self,
            "noop_inputs",
            _freeze_type_map(self.noop_inputs, label=f"{self.composition_id} noops"),
        )


@dataclass(frozen=True, slots=True)
class CompositionValidationResult:
    """Successful validation record for a composed optic."""

    composition_id: str
    source_optics: tuple[str, ...]
    handoffs: tuple[OpticHandoff, ...]
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    optional_output_types: tuple[str, ...]
    lineage_summary: Mapping[str, tuple[str, ...]]
    noop_inputs: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "lineage_summary",
            _freeze_type_map(self.lineage_summary, label=f"{self.composition_id} lineage"),
        )
        object.__setattr__(
            self,
            "noop_inputs",
            _freeze_type_map(self.noop_inputs, label=f"{self.composition_id} noops"),
        )


@dataclass(frozen=True, slots=True)
class OpticCategoryRegistry:
    """Registry for primitive optics and reusable composed optics."""

    optics: Iterable[OpticSpec]
    compositions: Iterable[CompositionSpec] = ()

    def __post_init__(self) -> None:
        optics: dict[str, OpticSpec] = {}
        for optic in tuple(self.optics):
            if optic.optic_id in optics:
                raise OpticCategoryError(f"duplicate optic id: {optic.optic_id}")
            optics[optic.optic_id] = optic
        if not optics:
            raise OpticCategoryError("optic category registry requires at least one optic")

        compositions: dict[str, CompositionSpec] = {}
        for composition in tuple(self.compositions):
            if composition.composition_id in compositions:
                raise OpticCategoryError(
                    f"duplicate composition id: {composition.composition_id}"
                )
            compositions[composition.composition_id] = composition

        object.__setattr__(self, "optics", MappingProxyType(optics))
        object.__setattr__(self, "compositions", MappingProxyType(compositions))

    def optic(self, optic_id: str) -> OpticSpec:
        try:
            return self.optics[optic_id]
        except KeyError as exc:
            raise OpticCategoryError(f"unknown optic id: {optic_id}") from exc

    def composition(self, composition_id: str) -> CompositionSpec:
        try:
            return self.compositions[composition_id]
        except KeyError as exc:
            raise OpticCategoryError(f"unknown composition id: {composition_id}") from exc

    def validate_registered(self, composition_id: str) -> CompositionValidationResult:
        return self.validate_composition(self.composition(composition_id))

    def validate_write(
        self,
        optic_id: str,
        target: str,
        *,
        output_type: str | None = None,
    ) -> None:
        optic = self.optic(optic_id)
        surfaces = list(optic.write_surface)
        if output_type is not None:
            _validate_token(output_type, "write output type")
            if output_type not in optic.all_output_types:
                raise OpticCategoryError(
                    f"{optic_id} does not declare output type for write: {output_type}"
                )
            surfaces.extend(optic.conditional_write_surface.get(output_type, ()))

        if any(_surface_matches(surface, target) for surface in surfaces):
            return
        raise OpticCategoryError(
            f"{optic_id} write target outside declared write surface: {target}"
        )

    def validate_composition(
        self, composition: CompositionSpec
    ) -> CompositionValidationResult:
        order = {optic_id: index for index, optic_id in enumerate(composition.source_optics)}
        specs = {optic_id: self.optic(optic_id) for optic_id in composition.source_optics}
        produced_all = {
            output_type
            for optic in specs.values()
            for output_type in optic.all_output_types
        }
        externalized_outputs = set(composition.input_types) & produced_all
        if externalized_outputs:
            raise OpticCategoryError(
                "composition input type is produced by source optic: "
                + ", ".join(sorted(externalized_outputs))
            )
        consumed_all = {
            input_type
            for optic in specs.values()
            for input_type in optic.input_types
        }
        unused_inputs = set(composition.input_types) - consumed_all
        if unused_inputs:
            raise OpticCategoryError(
                "undeclared visibility in composition input types: "
                + ", ".join(sorted(unused_inputs))
            )

        for optic_id in composition.noop_inputs:
            if optic_id not in specs:
                raise OpticCategoryError(f"noop references unknown optic: {optic_id}")
            for input_type in composition.noop_inputs[optic_id]:
                if input_type not in specs[optic_id].input_types:
                    raise OpticCategoryError(
                        f"{optic_id} does not declare input type for noop: {input_type}"
                    )
                if input_type not in composition.optional_output_types:
                    raise OpticCategoryError(
                        f"noop input must be an optional output: {optic_id} {input_type}"
                    )

        incoming: dict[str, set[str]] = {optic_id: set() for optic_id in specs}
        for handoff in composition.handoffs:
            if handoff.from_optic not in specs:
                raise OpticCategoryError(f"handoff source optic is not in composition: {handoff.from_optic}")
            if handoff.to_optic not in specs:
                raise OpticCategoryError(f"handoff target optic is not in composition: {handoff.to_optic}")
            if order[handoff.from_optic] >= order[handoff.to_optic]:
                raise OpticCategoryError(
                    "handoff must point from an earlier optic to a later optic: "
                    f"{handoff.from_optic} -> {handoff.to_optic}"
                )
            if handoff.output_type != handoff.input_type:
                raise OpticCategoryError(
                    "handoff output type must match input type: "
                    f"{handoff.output_type} -> {handoff.input_type}"
                )
            producer = specs[handoff.from_optic]
            consumer = specs[handoff.to_optic]
            if handoff.output_type not in producer.all_output_types:
                raise OpticCategoryError(
                    f"{handoff.from_optic} does not declare output type: "
                    f"{handoff.output_type}"
                )
            if handoff.input_type not in consumer.input_types:
                raise OpticCategoryError(
                    f"{handoff.to_optic} does not declare input type: "
                    f"{handoff.input_type}"
                )
            incoming[handoff.to_optic].add(handoff.input_type)

        lineage_summary: dict[str, tuple[str, ...]] = {}
        external_inputs = set(composition.input_types)
        for optic_id in composition.source_optics:
            optic = specs[optic_id]
            available = (
                external_inputs
                | incoming[optic_id]
                | set(composition.noop_inputs.get(optic_id, ()))
            )
            for input_type in optic.input_types:
                if input_type not in available:
                    raise OpticCategoryError(
                        f"{optic_id} requires input type {input_type}"
                    )
            for lineage_type in optic.lineage_required:
                if lineage_type not in available:
                    raise OpticCategoryError(
                        f"{optic_id} lineage requirement not satisfied: {lineage_type}"
                    )
            lineage_summary[optic_id] = tuple(optic.lineage_required)

        produced_required = {
            output_type
            for optic in specs.values()
            for output_type in optic.output_types
        }
        for output_type in composition.output_types:
            if output_type not in produced_required:
                raise OpticCategoryError(
                    f"composition output type is not produced as required: {output_type}"
                )
        noop_types = {
            input_type
            for input_types in composition.noop_inputs.values()
            for input_type in input_types
        }
        for output_type in composition.optional_output_types:
            if output_type not in produced_all and output_type not in noop_types:
                raise OpticCategoryError(
                    f"composition optional output type is not produced or no-op: "
                    f"{output_type}"
                )

        return CompositionValidationResult(
            composition_id=composition.composition_id,
            source_optics=composition.source_optics,
            handoffs=composition.handoffs,
            input_types=composition.input_types,
            output_types=composition.output_types,
            optional_output_types=composition.optional_output_types,
            lineage_summary=lineage_summary,
            noop_inputs=composition.noop_inputs,
        )


def build_bug_fix_structural_role_optics() -> tuple[OpticSpec, ...]:
    """Return primitive optics for the Audit -> Plan -> Patch -> Review loop."""

    return (
        OpticSpec(
            optic_id="RoadmapExtractorOptic",
            input_types=("RepositoryFiles",),
            output_types=("RepositoryMapView",),
            read_projection=("RepositoryFiles", "docs/agents/", "docs/PLAN.md"),
            write_surface=("docs/agents/",),
            lineage_required=("RepositoryFiles",),
            role="roadmap_extractor",
            phase="map",
        ),
        OpticSpec(
            optic_id="AuditorOptic",
            input_types=("RepositoryMapView", "IssueClaimView"),
            output_types=("AuditReport",),
            read_projection=("RepositoryMapView", "IssueClaimView", "docs/agents/"),
            write_surface=(
                "docs/audits/<domain>/scripts/",
                "docs/audits/<domain>/logs/",
                "docs/audits/<domain>/reports/",
            ),
            lineage_required=("RepositoryMapView", "IssueClaimView"),
            role="auditor",
            phase="audit",
        ),
        OpticSpec(
            optic_id="PlannerOptic",
            input_types=("RepositoryMapView", "AuditReport"),
            output_types=("RepairPlan",),
            read_projection=("RepositoryMapView", "AuditReport"),
            write_surface=("docs/audits/<domain>/plan/",),
            lineage_required=("RepositoryMapView", "AuditReport"),
            role="planner",
            phase="plan",
        ),
        OpticSpec(
            optic_id="PatcherOptic",
            input_types=("AuditReport", "RepairPlan"),
            output_types=("PatchDiff",),
            read_projection=("AuditReport", "RepairPlan"),
            write_surface=("<repair-plan-authorized-files>",),
            lineage_required=("AuditReport", "RepairPlan"),
            role="patcher",
            phase="patch",
        ),
        OpticSpec(
            optic_id="ReviewerOptic",
            input_types=(
                "RepositoryMapView",
                "AuditReport",
                "RepairPlan",
                "PatchDiff",
            ),
            output_types=("ReviewReport",),
            optional_output_types=("AgentMapDelta",),
            read_projection=(
                "RepositoryMapView",
                "AuditReport",
                "RepairPlan",
                "PatchDiff",
            ),
            write_surface=(
                "docs/audits/<domain>/reports/review.md",
                "docs/audits/<domain>/review/",
            ),
            conditional_write_surface={"AgentMapDelta": ("docs/agents/",)},
            lineage_required=(
                "RepositoryMapView",
                "AuditReport",
                "RepairPlan",
                "PatchDiff",
            ),
            role="reviewer",
            phase="review",
        ),
    )


def build_bug_fix_structural_loop_composition() -> CompositionSpec:
    """Return the named composed optic for the concrete structural bug-fix loop."""

    return CompositionSpec(
        composition_id="BugFixStructuralLoopOptic",
        source_optics=(
            "RoadmapExtractorOptic",
            "AuditorOptic",
            "PlannerOptic",
            "PatcherOptic",
            "ReviewerOptic",
        ),
        handoffs=(
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
        ),
        input_types=("RepositoryFiles", "IssueClaimView"),
        output_types=("ReviewReport",),
        optional_output_types=("PatchDiff", "AgentMapDelta"),
    )


def build_bug_fix_structural_loop_registry() -> OpticCategoryRegistry:
    """Expose the reusable BugFixStructuralLoopOptic composition by name."""

    return OpticCategoryRegistry(
        optics=build_bug_fix_structural_role_optics(),
        compositions=(build_bug_fix_structural_loop_composition(),),
    )


def _validate_token(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise OpticCategoryError(f"{label} must be a non-empty string")


def _normalize_tokens(
    values: Iterable[str],
    *,
    label: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, str):
        raise OpticCategoryError(f"{label} must be an iterable of strings")
    normalized = tuple(values)
    if not normalized and not allow_empty:
        raise OpticCategoryError(f"{label} must not be empty")
    seen: set[str] = set()
    for value in normalized:
        _validate_token(value, label)
        if value in seen:
            raise OpticCategoryError(f"{label} contains a duplicate value: {value}")
        seen.add(value)
    return normalized


def _normalize_handoffs(
    handoffs: Iterable[OpticHandoff],
    *,
    label: str,
) -> tuple[OpticHandoff, ...]:
    normalized = tuple(handoffs)
    if not normalized:
        raise OpticCategoryError(f"{label} must not be empty")
    for handoff in normalized:
        if not isinstance(handoff, OpticHandoff):
            raise OpticCategoryError(f"{label} must contain OpticHandoff values")
    return normalized


def _freeze_type_map(
    values: Mapping[str, Iterable[str]],
    *,
    label: str,
) -> Mapping[str, tuple[str, ...]]:
    frozen: dict[str, tuple[str, ...]] = {}
    for key, mapped_values in values.items():
        _validate_token(key, label)
        frozen[key] = _normalize_tokens(mapped_values, label=f"{label}: {key}")
    return MappingProxyType(frozen)


def _freeze_surface_map(
    values: Mapping[str, Iterable[str]],
    *,
    declared_outputs: tuple[str, ...],
    label: str,
) -> Mapping[str, tuple[str, ...]]:
    frozen = _freeze_type_map(values, label=label)
    for output_type in frozen:
        if output_type not in declared_outputs:
            raise OpticCategoryError(
                f"{label} references undeclared output type: {output_type}"
            )
    return frozen


def _surface_matches(surface: str, target: str) -> bool:
    target_parts = _normalize_target_parts(target)
    prefix_match = surface.endswith("/")
    surface_parts = tuple(part for part in surface.strip("/").split("/") if part)
    if not surface_parts:
        return False
    if prefix_match:
        if len(target_parts) < len(surface_parts):
            return False
    elif len(target_parts) != len(surface_parts):
        return False

    for expected, actual in zip(surface_parts, target_parts):
        if expected.startswith("<") and expected.endswith(">"):
            if expected == "<domain>":
                continue
            return False
        if expected != actual:
            return False
    return True


def _normalize_target_parts(target: str) -> tuple[str, ...]:
    _validate_token(target, "write target")
    if target.startswith("/"):
        raise OpticCategoryError("invalid write target: absolute paths are not allowed")
    parts = tuple(target.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise OpticCategoryError("invalid write target: traversal is not allowed")
    return parts
