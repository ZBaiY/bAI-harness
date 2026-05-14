"""Composable typed optics for declared task views.

This module is deliberately declarative. It does not crawl workspaces, call
models, execute tools, or infer what an agent should see. Callers provide an
optic contract, source data, and explicit lineage; the module projects only the
declared view and validates patches mechanically against that visible evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


class OpticContractError(ValueError):
    """Raised when an optic declaration is structurally invalid."""


class OpticPermissionError(PermissionError):
    """Raised when a projection or patch exceeds a declared optic contract."""


@dataclass(frozen=True, slots=True)
class ViewSpec:
    """Typed field set accepted or produced by an optic."""

    name: str
    fields: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_name(self.name, "view name")
        object.__setattr__(
            self,
            "fields",
            _normalize_names(self.fields, label=f"{self.name} fields"),
        )

    def restrict(self, requested_fields: Iterable[str]) -> "ViewSpec":
        requested = _normalize_names(requested_fields, label="requested fields")
        unknown = set(requested) - set(self.fields)
        if unknown:
            raise OpticPermissionError(
                "optic request cannot expand visibility beyond declared projection: "
                + ", ".join(sorted(unknown))
            )
        return ViewSpec(self.name, requested)


@dataclass(frozen=True, slots=True)
class PatchRule:
    """Allowed write surface for one projected field."""

    field: str
    operations: tuple[str, ...] = ("set",)
    required_lineage_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_name(self.field, "patch field")
        object.__setattr__(
            self,
            "operations",
            _normalize_names(self.operations, label=f"{self.field} operations"),
        )
        required = _normalize_names(
            self.required_lineage_fields,
            label=f"{self.field} required lineage fields",
            allow_empty=True,
        )
        object.__setattr__(self, "required_lineage_fields", required)


@dataclass(frozen=True, slots=True)
class ProjectedView:
    """Runtime projection produced by an optic from explicit source lineage."""

    optic_name: str
    view: ViewSpec
    data: Mapping[str, Any]
    lineage: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        _validate_name(self.optic_name, "optic name")
        data = MappingProxyType(dict(self.data))
        lineage = _freeze_lineage(self.lineage)
        data_fields = set(data)
        view_fields = set(self.view.fields)
        if data_fields != view_fields:
            raise OpticContractError("projected data must match the projected view fields")
        if set(lineage) != data_fields:
            raise OpticContractError("projected lineage must match the visible data fields")
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "lineage", lineage)


@dataclass(frozen=True, slots=True)
class OpticPatch:
    """Patch request against an optic output view."""

    view_name: str
    operation: str
    changes: Mapping[str, Any]
    evidence: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        _validate_name(self.view_name, "patch view name")
        _validate_name(self.operation, "patch operation")
        if not self.changes:
            raise OpticContractError("optic patch requires at least one changed field")
        for field in self.changes:
            _validate_name(field, "patch change field")
        object.__setattr__(self, "changes", MappingProxyType(dict(self.changes)))
        object.__setattr__(self, "evidence", _freeze_lineage(self.evidence))


@dataclass(frozen=True, slots=True)
class ValidatedPatch:
    """Mechanical validation result for a patch that stayed inside an optic."""

    optic_name: str
    view_name: str
    operation: str
    changes: Mapping[str, Any]
    evidence: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", MappingProxyType(dict(self.changes)))
        object.__setattr__(self, "evidence", _freeze_lineage(self.evidence))


@dataclass(frozen=True, slots=True)
class Optic:
    """Typed projection plus allowed patch surface and lineage contract."""

    name: str
    input_view: ViewSpec
    output_view: ViewSpec
    patch_rules: tuple[PatchRule, ...] = ()

    def __post_init__(self) -> None:
        _validate_name(self.name, "optic name")
        output_fields = set(self.output_view.fields)
        if not output_fields <= set(self.input_view.fields):
            missing = output_fields - set(self.input_view.fields)
            raise OpticContractError(
                "optic output projection must be drawn from its input view: "
                + ", ".join(sorted(missing))
            )
        fields_with_rules: set[str] = set()
        patch_rules = tuple(self.patch_rules)
        for rule in patch_rules:
            if rule.field in fields_with_rules:
                raise OpticContractError(f"duplicate patch rule for field: {rule.field}")
            fields_with_rules.add(rule.field)
            if rule.field not in output_fields:
                raise OpticContractError(
                    f"patch field is outside optic output projection: {rule.field}"
                )
            missing_lineage = set(rule.required_lineage_fields) - output_fields
            if missing_lineage:
                raise OpticContractError(
                    f"patch lineage field is outside optic output projection: {rule.field}"
                )
        object.__setattr__(self, "patch_rules", patch_rules)

    def project(
        self,
        source: Mapping[str, Any],
        *,
        lineage: Mapping[str, Iterable[str]],
        requested_fields: Iterable[str] | None = None,
    ) -> ProjectedView:
        """Project source data without allowing request-time visibility expansion."""

        view = (
            self.output_view
            if requested_fields is None
            else self.output_view.restrict(requested_fields)
        )
        missing_data = [field for field in view.fields if field not in source]
        if missing_data:
            raise OpticContractError(
                "source data is missing declared projection fields: "
                + ", ".join(missing_data)
            )
        missing_lineage = [field for field in view.fields if field not in lineage]
        if missing_lineage:
            raise OpticPermissionError(
                "explicit lineage is missing for visible fields: "
                + ", ".join(missing_lineage)
            )
        return ProjectedView(
            optic_name=self.name,
            view=view,
            data={field: source[field] for field in view.fields},
            lineage={field: tuple(lineage[field]) for field in view.fields},
        )

    def validate_patch(self, view: ProjectedView, patch: OpticPatch) -> ValidatedPatch:
        """Validate a patch against declared write scope and visible lineage."""

        if view.optic_name != self.name:
            raise OpticPermissionError("projected view was not produced by this optic")
        if view.view.name != self.output_view.name or patch.view_name != self.output_view.name:
            raise OpticPermissionError("patch view type does not match optic output view")

        visible_tokens = {
            token for tokens in view.lineage.values() for token in tokens
        }
        rules = {rule.field: rule for rule in self.patch_rules}
        for field in patch.changes:
            rule = rules.get(field)
            if rule is None:
                raise OpticPermissionError(
                    f"patch field outside allowed patch surface: {field}"
                )
            if patch.operation not in rule.operations:
                raise OpticPermissionError(
                    f"patch operation is not allowed for field {field}: {patch.operation}"
                )
            supplied_tokens = set(patch.evidence.get(field, ()))
            if not supplied_tokens:
                raise OpticPermissionError(f"patch evidence is required for field: {field}")
            if not supplied_tokens <= visible_tokens:
                raise OpticPermissionError(
                    f"patch evidence references non-visible lineage for field: {field}"
                )
            for required_field in rule.required_lineage_fields:
                if required_field not in view.data:
                    raise OpticPermissionError(
                        "required lineage field is not visible for patch field "
                        f"{field}: {required_field}"
                    )
                required_tokens = set(view.lineage[required_field])
                if not required_tokens <= supplied_tokens:
                    raise OpticPermissionError(
                        "patch evidence is missing required visible lineage for field "
                        f"{field}: {required_field}"
                    )
        return ValidatedPatch(
            optic_name=self.name,
            view_name=patch.view_name,
            operation=patch.operation,
            changes=patch.changes,
            evidence=patch.evidence,
        )


@dataclass(frozen=True, slots=True)
class OpticGraph:
    """Mechanical composition of compatible typed optics."""

    optics: tuple[Optic, ...]

    def __post_init__(self) -> None:
        if not self.optics:
            raise OpticContractError("optic graph requires at least one optic")
        optics = tuple(self.optics)
        for producer, consumer in zip(optics, optics[1:]):
            if producer.output_view.name != consumer.input_view.name:
                raise TypeError(
                    "optic output type does not match next optic input type: "
                    f"{producer.name} -> {consumer.name}"
                )
            missing = set(consumer.input_view.fields) - set(producer.output_view.fields)
            if missing:
                raise TypeError(
                    "optic output projection does not satisfy next optic input fields: "
                    + ", ".join(sorted(missing))
                )
        object.__setattr__(self, "optics", optics)

    @property
    def input_view(self) -> ViewSpec:
        return self.optics[0].input_view

    @property
    def output_view(self) -> ViewSpec:
        return self.optics[-1].output_view

    def project(
        self,
        source: Mapping[str, Any],
        *,
        lineage: Mapping[str, Iterable[str]],
        requested_fields: Iterable[str] | None = None,
    ) -> ProjectedView:
        current_source: Mapping[str, Any] = source
        current_lineage: Mapping[str, Iterable[str]] = lineage
        result: ProjectedView | None = None
        for index, optic in enumerate(self.optics):
            is_last = index == len(self.optics) - 1
            result = optic.project(
                current_source,
                lineage=current_lineage,
                requested_fields=requested_fields if is_last else None,
            )
            current_source = result.data
            current_lineage = result.lineage
        assert result is not None
        return result


@dataclass(frozen=True, slots=True)
class WorkflowOpticRef:
    """Workflow/phase/role/resource key that points to optic names."""

    workflow: str
    phase: str
    role: str
    resource: str

    def __post_init__(self) -> None:
        _validate_name(self.workflow, "workflow")
        _validate_name(self.phase, "phase")
        _validate_name(self.role, "role")
        _validate_name(self.resource, "resource")


@dataclass(frozen=True, slots=True)
class OpticRegistry:
    """Registry that lets workflow policy reference reusable optic graphs."""

    optics: Mapping[str, Optic]
    workflow_bindings: Mapping[WorkflowOpticRef, tuple[str, ...]]

    def __post_init__(self) -> None:
        if not self.optics:
            raise OpticContractError("optic registry requires at least one optic")
        optics: dict[str, Optic] = {}
        for name, optic in self.optics.items():
            _validate_name(name, "registered optic name")
            if name != optic.name:
                raise OpticContractError(f"optic registry key does not match optic: {name}")
            optics[name] = optic

        bindings: dict[WorkflowOpticRef, tuple[str, ...]] = {}
        for ref, names in self.workflow_bindings.items():
            optic_names = _normalize_names(names, label="workflow optic binding")
            missing = [name for name in optic_names if name not in optics]
            if missing:
                raise OpticContractError(
                    "workflow optic binding references unknown optics: "
                    + ", ".join(missing)
                )
            bindings[ref] = optic_names
        object.__setattr__(self, "optics", MappingProxyType(optics))
        object.__setattr__(self, "workflow_bindings", MappingProxyType(bindings))

    def graph_for(self, ref: WorkflowOpticRef) -> OpticGraph:
        try:
            names = self.workflow_bindings[ref]
        except KeyError as exc:
            raise KeyError(f"workflow optic binding is not declared: {ref}") from exc
        return compose_optics(*(self.optics[name] for name in names))


def compose_optics(*optics: Optic) -> OpticGraph:
    return OpticGraph(tuple(optics))


def _validate_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise OpticContractError(f"{label} must be a non-empty string")


def _normalize_names(
    values: Iterable[str],
    *,
    label: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, str):
        raise OpticContractError(f"{label} must be an iterable of strings, not a string")
    names = tuple(values)
    if not names and not allow_empty:
        raise OpticContractError(f"{label} must not be empty")
    seen: set[str] = set()
    for name in names:
        _validate_name(name, label)
        if name in seen:
            raise OpticContractError(f"{label} contains a duplicate value: {name}")
        seen.add(name)
    return names


def _freeze_lineage(
    lineage: Mapping[str, Iterable[str]],
) -> Mapping[str, tuple[str, ...]]:
    frozen: dict[str, tuple[str, ...]] = {}
    for field, tokens in lineage.items():
        _validate_name(field, "lineage field")
        frozen[field] = _normalize_names(
            tokens,
            label=f"{field} lineage tokens",
            allow_empty=False,
        )
    return MappingProxyType(frozen)
