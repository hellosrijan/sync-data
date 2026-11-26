from datetime import datetime
from uuid import uuid4

from django.conf import settings
from django.utils import timezone

from sync.tests.fakes import FakeSalesforce


def sales_edit(fake: FakeSalesforce, salesforce_id: str, **changed_fields) -> dict:
    """Changes a record in the fake and returns the matching webhook payload — only the
    fields actually changed (Q2's default assumption, revised 2026-11-27)."""
    record = fake.records[salesforce_id]
    record["fields"].update(changed_fields)
    record["modified_at"] = timezone.now()
    record["modified_by"] = "sales.user@client.example"
    return {
        "eventId": f"evt_{uuid4()}",
        "recordId": salesforce_id,
        "objectType": record["object_type"],
        "occurredAt": record["modified_at"].isoformat(),
        "changedBy": record["modified_by"],
        "changedFields": dict(changed_fields),
    }


def sales_edit_whole_record(fake: FakeSalesforce, salesforce_id: str, **changed_fields) -> dict:
    """Fallback case: webhook carries every field on the record (Q2's 'other way')
    to prove apply()'s sf_value comparison handles this safely too."""
    record = fake.records[salesforce_id]
    record["fields"].update(changed_fields)
    record["modified_at"] = timezone.now()
    record["modified_by"] = "sales.user@client.example"
    return {
        "eventId": f"evt_{uuid4()}",
        "recordId": salesforce_id,
        "objectType": record["object_type"],
        "occurredAt": record["modified_at"].isoformat(),
        "changedBy": record["modified_by"],
        "changedFields": dict(record["fields"]),
    }


def our_echo(fake: FakeSalesforce, salesforce_id: str, **fields) -> dict:
    """What a webhook looks like when our own outbound update comes back —
    changedBy equals settings.SALESFORCE_INTEGRATION_USER (design §9)."""
    record = fake.records[salesforce_id]
    record["fields"].update(fields)
    record["modified_at"] = timezone.now()
    record["modified_by"] = settings.SALESFORCE_INTEGRATION_USER
    return {
        "eventId": f"evt_{uuid4()}",
        "recordId": salesforce_id,
        "objectType": record["object_type"],
        "occurredAt": record["modified_at"].isoformat(),
        "changedBy": settings.SALESFORCE_INTEGRATION_USER,
        "changedFields": dict(fields),
    }


def duplicate(payload: dict) -> dict:
    """Same payload and eventId — posting it twice exercises duplicate suppression."""
    return dict(payload)


def stale_after(newer_payload: dict, older_occurred_at: datetime, **older_fields) -> dict:
    """An older event for the same record, delivered after a newer one already applied."""
    return {
        "eventId": f"evt_{uuid4()}",
        "recordId": newer_payload["recordId"],
        "objectType": newer_payload["objectType"],
        "occurredAt": older_occurred_at.isoformat(),
        "changedBy": newer_payload["changedBy"],
        "changedFields": dict(older_fields),
    }
