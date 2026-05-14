from __future__ import annotations

import pytest

from bai.core.optics import (
    Optic,
    OpticContractError,
    OpticPatch,
    OpticPermissionError,
    OpticRegistry,
    PatchRule,
    ViewSpec,
    WorkflowOpticRef,
    compose_optics,
)


def customer_contact_optic() -> Optic:
    return Optic(
        name="CustomerContactOptic",
        input_view=ViewSpec(
            "CustomerRecord",
            ("customer_id", "email", "order_id", "ssn", "internal_notes"),
        ),
        output_view=ViewSpec("CustomerContact", ("customer_id", "email", "order_id")),
        patch_rules=(
            PatchRule(
                field="email",
                operations=("set",),
                required_lineage_fields=("customer_id", "email"),
            ),
        ),
    )


def notification_optic() -> Optic:
    return Optic(
        name="NotificationOptic",
        input_view=ViewSpec("CustomerContact", ("customer_id", "email")),
        output_view=ViewSpec("NotificationTarget", ("email",)),
    )


def customer_source() -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    return (
        {
            "customer_id": "cust-123",
            "email": "old@example.com",
            "order_id": "order-456",
            "ssn": "000-00-0000",
            "internal_notes": "private",
        },
        {
            "customer_id": ("crm/customer_id/cust-123",),
            "email": ("crm/email/cust-123",),
            "order_id": ("orders/order_id/order-456",),
            "ssn": ("crm/ssn/cust-123",),
            "internal_notes": ("crm/internal_notes/cust-123",),
        },
    )


def test_optic_declares_projection_patch_surface_and_lineage_contract() -> None:
    optic = customer_contact_optic()

    assert optic.input_view == ViewSpec(
        "CustomerRecord",
        ("customer_id", "email", "order_id", "ssn", "internal_notes"),
    )
    assert optic.output_view == ViewSpec(
        "CustomerContact", ("customer_id", "email", "order_id")
    )
    assert optic.patch_rules == (
        PatchRule(
            field="email",
            operations=("set",),
            required_lineage_fields=("customer_id", "email"),
        ),
    )


def test_optics_compose_only_through_compatible_typed_interfaces() -> None:
    graph = compose_optics(customer_contact_optic(), notification_optic())

    assert graph.input_view.name == "CustomerRecord"
    assert graph.output_view == ViewSpec("NotificationTarget", ("email",))

    incompatible = Optic(
        name="PaymentPatchOptic",
        input_view=ViewSpec("PaymentRecord", ("payment_id", "email")),
        output_view=ViewSpec("PaymentPatch", ("email",)),
    )
    with pytest.raises(TypeError, match="output type"):
        compose_optics(customer_contact_optic(), incompatible)


def test_composition_rejects_missing_fields_even_when_type_name_matches() -> None:
    needs_phone = Optic(
        name="PhoneNotificationOptic",
        input_view=ViewSpec("CustomerContact", ("customer_id", "email", "phone")),
        output_view=ViewSpec("PhoneNotificationTarget", ("email",)),
    )

    with pytest.raises(TypeError, match="does not satisfy"):
        compose_optics(customer_contact_optic(), needs_phone)


def test_workflow_policy_references_optics_without_raw_field_duplication() -> None:
    ref = WorkflowOpticRef(
        workflow="refund",
        phase="notify",
        role="worker",
        resource="customer_contact",
    )
    registry = OpticRegistry(
        optics={
            "CustomerContactOptic": customer_contact_optic(),
            "NotificationOptic": notification_optic(),
        },
        workflow_bindings={ref: ("CustomerContactOptic", "NotificationOptic")},
    )

    graph = registry.graph_for(ref)

    assert registry.workflow_bindings[ref] == (
        "CustomerContactOptic",
        "NotificationOptic",
    )
    assert graph.output_view == ViewSpec("NotificationTarget", ("email",))


def test_llm_request_cannot_expand_visibility_beyond_declared_projection() -> None:
    optic = customer_contact_optic()
    source, lineage = customer_source()

    visible = optic.project(source, lineage=lineage, requested_fields=("email",))
    assert visible.data == {"email": "old@example.com"}
    assert visible.lineage == {"email": ("crm/email/cust-123",)}

    with pytest.raises(OpticPermissionError, match="cannot expand visibility"):
        optic.project(source, lineage=lineage, requested_fields=("email", "ssn"))


def test_writes_require_lineage_from_visible_evidence() -> None:
    optic = customer_contact_optic()
    source, lineage = customer_source()
    view = optic.project(source, lineage=lineage)

    validated = optic.validate_patch(
        view,
        OpticPatch(
            view_name="CustomerContact",
            operation="set",
            changes={"email": "new@example.com"},
            evidence={
                "email": (
                    "crm/customer_id/cust-123",
                    "crm/email/cust-123",
                )
            },
        ),
    )

    assert validated.changes == {"email": "new@example.com"}

    with pytest.raises(OpticPermissionError, match="missing required visible lineage"):
        optic.validate_patch(
            view,
            OpticPatch(
                view_name="CustomerContact",
                operation="set",
                changes={"email": "other@example.com"},
                evidence={"email": ("crm/email/cust-123",)},
            ),
        )

    with pytest.raises(OpticPermissionError, match="non-visible lineage"):
        optic.validate_patch(
            view,
            OpticPatch(
                view_name="CustomerContact",
                operation="set",
                changes={"email": "secret@example.com"},
                evidence={
                    "email": (
                        "crm/customer_id/cust-123",
                        "crm/email/cust-123",
                        "crm/ssn/cust-123",
                    )
                },
            ),
        )


def test_patch_surface_cannot_write_undeclared_fields_or_operations() -> None:
    optic = customer_contact_optic()
    source, lineage = customer_source()
    view = optic.project(source, lineage=lineage)

    with pytest.raises(OpticPermissionError, match="outside allowed patch surface"):
        optic.validate_patch(
            view,
            OpticPatch(
                view_name="CustomerContact",
                operation="set",
                changes={"order_id": "order-999"},
                evidence={"order_id": ("orders/order_id/order-456",)},
            ),
        )

    with pytest.raises(OpticPermissionError, match="operation is not allowed"):
        optic.validate_patch(
            view,
            OpticPatch(
                view_name="CustomerContact",
                operation="delete",
                changes={"email": ""},
                evidence={
                    "email": (
                        "crm/customer_id/cust-123",
                        "crm/email/cust-123",
                    )
                },
            ),
        )


def test_malformed_optic_specs_fail_closed() -> None:
    with pytest.raises(OpticContractError, match="outside optic output projection"):
        Optic(
            name="BadOptic",
            input_view=ViewSpec("CustomerRecord", ("customer_id", "email")),
            output_view=ViewSpec("CustomerContact", ("email",)),
            patch_rules=(PatchRule("customer_id"),),
        )

    with pytest.raises(OpticContractError, match="output projection"):
        Optic(
            name="ImpossibleProjectionOptic",
            input_view=ViewSpec("CustomerRecord", ("email",)),
            output_view=ViewSpec("CustomerContact", ("email", "ssn")),
        )
