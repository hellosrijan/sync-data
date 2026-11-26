import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q


class SyncRecord(models.Model):
    """Abstract base for Customer and Contact: the columns every record synced with Salesforce
    shares (design §4). Has no table of its own.

    Unique constraints: `external_id` (our stable id) and `salesforce_id` (NULL until the
    record exists in Salesforce; Postgres allows many NULLs under UNIQUE).
    """

    external_id = models.UUIDField(default=uuid.uuid4, unique=True)
    salesforce_id = models.CharField(max_length=18, unique=True, null=True, blank=True)
    # Per-field last-write info, used by the ordering rule (design §7). Shape:
    #   {"<field>": {"at": ISO time of the change, "source": "salesforce"|"portal",
    #                "by": actor (user or SF user), "sf_value": last value Salesforce sent}}
    # "at" decides which of two writes wins; "sf_value" lets us tell an echo of our own
    # update from a real change (design §9).
    field_meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Who created / last changed the record (design §4). Set by apply() (phase 2, build-plan
    # 2.2.4), so they are empty strings until that exists.
    created_by = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        abstract = True


class Customer(SyncRecord):
    """A customer (Salesforce Account) in the portal (design §4). Synced fields: name,
    status, owner. Uniqueness comes from SyncRecord (`external_id`, `salesforce_id`).
    Write only through apply() (design §7-9)."""

    name = models.CharField(max_length=255)
    status = models.CharField(max_length=40)
    owner = models.CharField(max_length=255)


class Contact(SyncRecord):
    """A contact (Salesforce Contact) in the portal (design §4). Synced fields: full_name,
    phone, email, city, owner, customer. Indexes: `email`, `customer_salesforce_id`, plus the
    SyncRecord unique columns. Write only through apply() (design §7-9)."""

    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=40)
    email = models.EmailField(max_length=254, db_index=True)
    city = models.CharField(max_length=120)
    owner = models.CharField(max_length=255)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="contacts")
    # Salesforce id of the parent Account as sent by Salesforce. Kept so a contact that arrives
    # before its customer can be linked to `customer` later (design §4). "" when unknown.
    customer_salesforce_id = models.CharField(max_length=18, db_index=True, blank=True, default="")


class ChangeEvent(models.Model):
    """One row per webhook event already processed: the duplicate-webhook guard (design §8).
    The DB-level unique constraint `change_event_event_id_uniq` on `event_id` makes a replay
    fail to insert, so it is skipped. Index: `received_at`."""

    event_id = models.CharField(max_length=128)
    source = models.CharField(max_length=40)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event_id"], name="change_event_event_id_uniq")]


class FieldChange(models.Model):
    """Append-only history of every field change, from either side (design §4, §7). Backs
    `GET customer/{id}/history/`. Index `field_change_record_idx` on
    (object_type, record_id, occurred_at) serves that lookup."""

    class Status(models.TextChoices):
        APPLIED = "applied"
        SUPERSEDED = "superseded"
        ECHO = "echo"

    object_type = models.CharField(max_length=20)
    # Plain id of the Customer or Contact row (which table is given by object_type), so no FK.
    record_id = models.BigIntegerField()
    field = models.CharField(max_length=64)
    value = models.TextField(null=True, blank=True)
    actor = models.CharField(max_length=255)
    source = models.CharField(max_length=40)
    occurred_at = models.DateTimeField()
    # applied = won and written to the record; superseded = lost to a newer change;
    # echo = our own outbound update coming back from Salesforce (design §7, §9).
    status = models.CharField(max_length=12, choices=Status)
    event_id = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["object_type", "record_id", "occurred_at"], name="field_change_record_idx"),
        ]


class Outbox(models.Model):
    """Pending calls to the Salesforce record API; survives restarts and outages (design §6, §10).
    Constraints: `outbox_one_pending_per_record` (partial unique on object_type, record_id
    where state = 'pending'), so later edits merge into the one pending row. Index
    `outbox_state_next_idx` on (state, next_attempt_at) serves the drain query."""

    class Operation(models.TextChoices):
        CREATE = "create"
        UPDATE = "update"

    class State(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        NEEDS_REVIEW = "needs_review"

    object_type = models.CharField(max_length=20)
    # Plain id of the Customer or Contact row (table given by object_type), so no FK.
    record_id = models.BigIntegerField()
    operation = models.CharField(max_length=10, choices=Operation)
    # Names of the changed fields still to send; only these go to Salesforce (PATCH semantics).
    fields = ArrayField(models.CharField(max_length=64), default=list)
    # pending = waiting to send; sent = Salesforce accepted; needs_review = gave up after
    # repeated failures, an admin requeues it (design §6).
    state = models.CharField(max_length=12, choices=State, default=State.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    # Earliest time to try again (backoff). No default: the writer sets it explicitly.
    next_attempt_at = models.DateTimeField()
    # A worker claims the row until this time; if it dies the lease expires and another worker
    # retries. NULL = not claimed.
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["object_type", "record_id"],
                condition=Q(state="pending"),
                name="outbox_one_pending_per_record",
            )
        ]
        indexes = [models.Index(fields=["state", "next_attempt_at"], name="outbox_state_next_idx")]


class SyncState(models.Model):
    """Small key/value store for sync bookkeeping, e.g. the catch-up watermark and up/down flag (design §4, §10).
    Primary key is `key`."""

    key = models.CharField(max_length=64, primary_key=True)
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
