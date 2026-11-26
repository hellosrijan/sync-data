"""apply(): the one place every inbound change goes through (design §7-9, build-plan 2.1-2.4).

Pure Postgres: no network, no Salesforce client, no Redis.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from sync.models import ChangeEvent, Contact, Customer, FieldChange, Outbox

# "catch-up" is treated exactly like "salesforce" everywhere (AC-2.1.1).
SF_SOURCES = frozenset({"salesforce", "catch-up"})
MODELS = {"Customer": Customer, "Contact": Contact}


class UnknownRecord(LookupError):
    """A portal change or a Salesforce update names a record that does not exist. Raised rather
    than silently created (DEFAULTED, AC-2.1.2c); the transaction rolls back, so the ChangeEvent
    is not kept and a retry is not misreported as a duplicate."""


@dataclass(frozen=True)
class Change:
    source: str  # "salesforce" | "catch-up" | "portal"
    event_id: str
    object_type: str  # "Customer" | "Contact"
    record_ref: str | int  # salesforce_id (str) for salesforce/catch-up, pk (int) for portal
    occurred_at: datetime  # timezone-aware
    actor: str
    fields: dict[str, Any]
    operation: str  # "create" | "update"


@dataclass(frozen=True)
class Result:
    outcome: str  # "applied" | "no_change" | "duplicate" | "echo"
    applied_fields: list[str] = field(default_factory=list)


def apply(change: Change) -> Result:
    with transaction.atomic():
        try:
            # Savepoint, so a duplicate's IntegrityError doesn't poison the outer transaction.
            with transaction.atomic():
                ChangeEvent.objects.create(event_id=change.event_id, source=change.source)
        except IntegrityError:
            return Result(outcome="duplicate")

        record, created = _lock_record(change)

        if _is_echo(change):
            for name, value in change.fields.items():
                _write_history(change, record, name, value, FieldChange.Status.ECHO)
            return Result(outcome="echo")

        applied_fields = []
        for name, value in _changed_fields(change, record).items():
            is_newer = _is_newer(change, record, name)
            status = FieldChange.Status.APPLIED if is_newer else FieldChange.Status.SUPERSEDED
            _write_history(change, record, name, value, status)
            if is_newer:
                _apply_field(change, record, name, value)
                applied_fields.append(name)

        if created:
            record.created_by = change.actor
        if applied_fields:
            record.updated_by = change.actor
        if created or applied_fields:
            record.save()

        if change.source == "portal" and applied_fields:
            _upsert_outbox_row(change.object_type, record.pk, applied_fields, change.operation)

        return Result(outcome="applied" if applied_fields else "no_change", applied_fields=applied_fields)


def _is_echo(change: Change) -> bool:
    return change.source in SF_SOURCES and change.actor == settings.SALESFORCE_INTEGRATION_USER


def _lock_record(change: Change) -> tuple[Any, bool]:
    """Return (record, created). The row is locked FOR UPDATE until the transaction ends."""
    model = MODELS[change.object_type]
    qs = model.objects.select_for_update()
    try:
        if change.source == "portal":
            return qs.get(pk=change.record_ref), False
        # An echo never creates a record: a create echo for an unknown id is an error, not a new row.
        if change.operation == "create" and not _is_echo(change):
            return qs.get_or_create(salesforce_id=change.record_ref)
        return qs.get(salesforce_id=change.record_ref), False
    except model.DoesNotExist as exc:
        raise UnknownRecord(f"{change.object_type} {change.record_ref!r} does not exist") from exc


def _changed_fields(change: Change, record: Any) -> dict[str, Any]:
    if change.source == "portal":
        return change.fields
    # Compare with the last value Salesforce sent for the field, not the current column (design §7).
    # A field with no sf_value yet counts as changed.
    changed = {}
    for name, value in change.fields.items():
        meta = record.field_meta.get(name, {})
        if "sf_value" not in meta or meta["sf_value"] != value:
            changed[name] = value
    return changed


def _is_newer(change: Change, record: Any, name: str) -> bool:
    stored = record.field_meta.get(name, {}).get("at")
    if stored is None:
        return True
    stored_at = datetime.fromisoformat(stored)  # stored as ISO string in JSON
    if change.occurred_at == stored_at:
        return change.source in SF_SOURCES  # exact tie: Salesforce wins (design §7)
    return change.occurred_at > stored_at


def _write_history(change: Change, record: Any, name: str, value: Any, status: str) -> None:
    FieldChange.objects.create(
        object_type=change.object_type,
        record_id=record.pk,
        field=name,
        value=None if value is None else str(value),
        actor=change.actor,
        source=change.source,
        occurred_at=change.occurred_at,
        status=status,
        event_id=change.event_id,
    )


def _apply_field(change: Change, record: Any, name: str, value: Any) -> None:
    old = record.field_meta.get(name, {})
    meta = {"at": change.occurred_at.isoformat(), "source": change.source, "by": change.actor}
    if change.source in SF_SOURCES:
        meta["sf_value"] = value
    elif "sf_value" in old:  # portal edit: keep what Salesforce last sent
        meta["sf_value"] = old["sf_value"]
    record.field_meta[name] = meta
    setattr(record, name, value)


def _upsert_outbox_row(object_type: str, record_id: int, applied_fields: list[str], operation: str) -> None:
    row, created = Outbox.objects.get_or_create(
        object_type=object_type,
        record_id=record_id,
        state=Outbox.State.PENDING,
        defaults={"operation": operation, "fields": sorted(applied_fields), "next_attempt_at": timezone.now()},
    )
    if not created:  # merge; `operation` is not touched, so a pending create stays a create
        row.fields = sorted(set(row.fields) | set(applied_fields))
        row.save(update_fields=["fields"])
