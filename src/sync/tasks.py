from datetime import datetime, timedelta
import logging
from typing import Any, Optional

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
import dramatiq
from dramatiq_crontab import cron, interval

from sync.client import ClientOutcome, SalesforceClient
from sync.limiter import RateLimiter, get_rate_limiter
from sync.models import ChangeEvent, Contact, Customer, Outbox, SyncState
from sync.services import Change, apply

logger = logging.getLogger(__name__)

DEDUPE_RETENTION_DAYS = 7
MAX_OUTBOUND_BACKOFF_SECONDS = 60
CONSECUTIVE_FAILURES_THRESHOLD = 5

MODELS = {"Customer": Customer, "Contact": Contact}


def get_salesforce_state() -> dict[str, Any]:
    state, _ = SyncState.objects.get_or_create(
        key="salesforce",
        defaults={"value": {"status": "up", "consecutive_failures": 0}},
    )
    return state.value


def record_salesforce_success():
    with transaction.atomic():
        state, _ = SyncState.objects.select_for_update().get_or_create(
            key="salesforce",
            defaults={"value": {"status": "up", "consecutive_failures": 0}},
        )
        val = dict(state.value)
        val["status"] = "up"
        val["consecutive_failures"] = 0
        state.value = val
        state.save()


def record_salesforce_failure() -> bool:
    """Record an unreachable failure; returns True if state transitioned to down."""
    with transaction.atomic():
        state, _ = SyncState.objects.select_for_update().get_or_create(
            key="salesforce",
            defaults={"value": {"status": "up", "consecutive_failures": 0}},
        )
        val = dict(state.value)
        fails = val.get("consecutive_failures", 0) + 1
        val["consecutive_failures"] = fails
        if fails >= CONSECUTIVE_FAILURES_THRESHOLD:
            val["status"] = "down"
        state.value = val
        state.save()
        return val["status"] == "down"


def make_all_pending_due():
    """Make all pending outbox rows due immediately (used on recovery)."""
    now = timezone.now()
    Outbox.objects.filter(state=Outbox.State.PENDING).update(next_attempt_at=now, lease_until=None)


@cron("0 2 * * *")  # daily at 02:00
@dramatiq.actor
def purge_expired_change_events(cutoff: Optional[datetime] = None, batch_size: int = 5000) -> int:
    """Delete ChangeEvent records older than 7 days in batches (design §8, build-plan 3.2.1)."""
    if cutoff is None:
        cutoff = timezone.now() - timedelta(days=DEDUPE_RETENTION_DAYS)
    total_deleted = 0
    while True:
        pks = list(ChangeEvent.objects.filter(received_at__lt=cutoff).values_list("pk", flat=True)[:batch_size])
        if not pks:
            break
        deleted_count, _ = ChangeEvent.objects.filter(pk__in=pks).delete()
        total_deleted += deleted_count
    return total_deleted


def _claim_due_row(now: datetime) -> Optional[Outbox]:
    with transaction.atomic():
        row = (
            Outbox.objects.select_for_update(skip_locked=True)
            .filter(state=Outbox.State.PENDING, next_attempt_at__lte=now)
            .filter(models.Q(lease_until__isnull=True) | models.Q(lease_until__lte=now))
            .order_by("next_attempt_at")
            .first()
        )
        if row:
            row.lease_until = now + timedelta(seconds=60)
            row.save(update_fields=["lease_until"])
            return row
        return None


@dramatiq.actor(max_retries=0)
def send_outbound(max_rows: int = 50, client: Optional[SalesforceClient] = None, limiter: Optional[RateLimiter] = None) -> int:
    """Outbound sync worker: claims due Outbox rows under rate limiter (design §6, build-plan 5.3)."""
    state_val = get_salesforce_state()
    if state_val.get("status") == "down":
        logger.info("Salesforce is down; skipping outbound send.")
        return 0

    limiter = limiter or get_rate_limiter()
    owns_client = client is None
    client = client or SalesforceClient()
    processed_count = 0

    try:
        for _ in range(max_rows):
            now = timezone.now()
            row = _claim_due_row(now)
            if not row:
                break

            processed_count += 1
            model = MODELS.get(row.object_type)
            if not model:
                row.state = Outbox.State.NEEDS_REVIEW
                row.last_error = f"Unknown object_type: {row.object_type}"
                row.lease_until = None
                row.save(update_fields=["state", "last_error", "lease_until"])
                continue

            record = model.objects.filter(pk=row.record_id).first()
            if not record:
                row.state = Outbox.State.NEEDS_REVIEW
                row.last_error = f"{row.object_type} pk {row.record_id} does not exist"
                row.lease_until = None
                row.save(update_fields=["state", "last_error", "lease_until"])
                continue

            # Read current values at send time for row.fields (prevents clobbering)
            sent_fields = list(row.fields)
            fields_data = {}
            for f in sent_fields:
                if f == "customer" and row.object_type == "Contact":
                    fields_data["customer"] = record.customer_salesforce_id
                elif hasattr(record, f):
                    fields_data[f] = getattr(record, f)

            # Acquire rate limiter token
            if not limiter.acquire(timeout=5.0):
                # Limiter acquisition timed out; release lease and reschedule
                row.lease_until = None
                row.next_attempt_at = now + timedelta(seconds=2)
                row.save(update_fields=["lease_until", "next_attempt_at"])
                continue

            # Dispatch via client
            if row.operation == Outbox.Operation.CREATE or not record.salesforce_id:
                outcome = client.upsert(row.object_type, str(record.external_id), fields_data)
            else:
                outcome = client.update(row.object_type, record.salesforce_id, fields_data)

            _handle_outcome(row, record, sent_fields, outcome, limiter)

            if outcome.outcome == "auth_error":
                logger.error("Authentication error calling Salesforce: %s", outcome.error_message)
                break
            if outcome.outcome == "unreachable" and get_salesforce_state().get("status") == "down":
                logger.info("Salesforce marked down; stopping current outbound batch.")
                break

        if processed_count == max_rows:
            now = timezone.now()
            if Outbox.objects.filter(state=Outbox.State.PENDING, next_attempt_at__lte=now).exists():
                try:
                    send_outbound.send(max_rows=max_rows)
                except Exception:
                    pass
    finally:
        if owns_client:
            client.close()

    return processed_count


def _handle_outcome(
    row: Outbox,
    record: Any,
    sent_fields: list[str],
    outcome: ClientOutcome,
    limiter: RateLimiter,
) -> None:
    now = timezone.now()

    if outcome.outcome == "ok":
        record_salesforce_success()
        with transaction.atomic():
            fresh_row = Outbox.objects.select_for_update().get(pk=row.pk)
            remaining_fields = sorted(set(fresh_row.fields) - set(sent_fields))
            if remaining_fields:
                fresh_row.fields = remaining_fields
                fresh_row.lease_until = None
                fresh_row.last_error = ""
                fresh_row.save(update_fields=["fields", "lease_until", "last_error"])
            else:
                fresh_row.state = Outbox.State.SENT
                fresh_row.lease_until = None
                fresh_row.last_error = ""
                fresh_row.save(update_fields=["state", "lease_until", "last_error"])

        if outcome.salesforce_id and not record.salesforce_id:
            record.salesforce_id = outcome.salesforce_id
            record.save(update_fields=["salesforce_id"])

    elif outcome.outcome == "unreachable":
        record_salesforce_failure()
        row.attempts += 1
        backoff = min(2 ** (row.attempts - 1), MAX_OUTBOUND_BACKOFF_SECONDS)
        row.next_attempt_at = now + timedelta(seconds=backoff)
        row.lease_until = None
        row.last_error = outcome.error_message
        row.save(update_fields=["attempts", "next_attempt_at", "lease_until", "last_error"])

    elif outcome.outcome == "throttled":
        limiter.throttle(retry_after=outcome.retry_after)
        row.next_attempt_at = now + timedelta(seconds=outcome.retry_after)
        row.lease_until = None
        row.last_error = f"Throttled (429): retry after {outcome.retry_after}s"
        row.save(update_fields=["next_attempt_at", "lease_until", "last_error"])

    elif outcome.outcome == "rejected":
        row.state = Outbox.State.NEEDS_REVIEW
        row.lease_until = None
        row.last_error = outcome.error_message
        row.save(update_fields=["state", "lease_until", "last_error"])

    elif outcome.outcome == "auth_error":
        row.lease_until = None
        row.last_error = f"Auth error (401/403): {outcome.error_message}"
        row.save(update_fields=["lease_until", "last_error"])


CATCHUP_OVERLAP_MINUTES = 5


def get_watermark() -> datetime:
    state, _ = SyncState.objects.get_or_create(
        key="watermark",
        defaults={"value": {"timestamp": (timezone.now() - timedelta(days=30)).isoformat()}},
    )
    ts_str = state.value.get("timestamp")
    if ts_str:
        dt = datetime.fromisoformat(ts_str)
        return dt if timezone.is_aware(dt) else timezone.make_aware(dt)
    return timezone.now() - timedelta(days=30)


def set_watermark(ts: datetime):
    with transaction.atomic():
        state, _ = SyncState.objects.select_for_update().get_or_create(
            key="watermark",
            defaults={"value": {"timestamp": ts.isoformat()}},
        )
        val = dict(state.value)
        val["timestamp"] = ts.isoformat()
        state.value = val
        state.save()


@interval(seconds=10)
@dramatiq.actor(max_retries=0)
def probe_salesforce(client: Optional[SalesforceClient] = None, limiter: Optional[RateLimiter] = None) -> bool:
    """Probes Salesforce every 10s while down; on recovery runs catch-up then makes pending rows due (5.4, 6.1)."""
    state_val = get_salesforce_state()
    if state_val.get("status") != "down":
        return True

    limiter = limiter or get_rate_limiter()
    if not limiter.acquire(timeout=5.0):
        return False

    owns_client = client is None
    client = client or SalesforceClient()
    try:
        outcome, _ = client.query("SELECT Id FROM Contact LIMIT 1")
        if outcome.outcome == "ok":
            logger.info("Salesforce recovered from outage! Triggering catch-up and drain.")
            record_salesforce_success()
            run_catchup(client=client, limiter=limiter)
            make_all_pending_due()
            send_outbound.send()
            return True
        return False
    finally:
        if owns_client:
            client.close()


@dramatiq.actor(max_retries=0)
def run_catchup(
    client: Optional[SalesforceClient] = None,
    limiter: Optional[RateLimiter] = None,
    overlap_minutes: int = CATCHUP_OVERLAP_MINUTES,
    page_size: int = 2000,
) -> int:
    """Catch-up task: query Salesforce for changes since watermark - overlap and apply them (Phase 6)."""
    watermark = get_watermark()
    since = watermark - timedelta(minutes=overlap_minutes)
    since_iso = since.isoformat()

    limiter = limiter or get_rate_limiter()
    owns_client = client is None
    client = client or SalesforceClient()

    soql = f"SELECT Id, SystemModstamp FROM Contact WHERE SystemModstamp >= '{since_iso}'"
    total_processed = 0

    try:
        if not limiter.acquire(timeout=5.0):
            return 0

        outcome, data = client.query(soql)
        if outcome.outcome != "ok":
            return 0

        while True:
            records = data.get("records", [])
            if not records:
                break

            page_max_ts = None
            with transaction.atomic():
                for r in records:
                    sf_id = r.get("Id")
                    modstamp_str = r.get("SystemModstamp")
                    if not sf_id or not modstamp_str:
                        continue

                    occurred_at = datetime.fromisoformat(modstamp_str)
                    if timezone.is_naive(occurred_at):
                        occurred_at = timezone.make_aware(occurred_at)

                    if page_max_ts is None or occurred_at > page_max_ts:
                        page_max_ts = occurred_at

                    obj_type = r.get("attributes", {}).get("type", "Contact")
                    fields = {
                        k: v for k, v in r.items()
                        if k not in ("Id", "SystemModstamp", "attributes", "modified_by", "changedBy")
                    }
                    actor = r.get("modified_by") or r.get("changedBy") or "salesforce-query"

                    model = MODELS.get(obj_type, Contact)
                    op = "update" if model.objects.filter(salesforce_id=sf_id).exists() else "create"

                    change = Change(
                        source="catch-up",
                        event_id=f"{sf_id}:{modstamp_str}",
                        object_type=obj_type,
                        record_ref=sf_id,
                        occurred_at=occurred_at,
                        actor=actor,
                        fields=fields,
                        operation=op,
                    )
                    apply(change)
                    total_processed += 1

                if page_max_ts and page_max_ts > watermark:
                    set_watermark(page_max_ts)
                    watermark = page_max_ts

            if data.get("done") or not data.get("nextRecordsUrl"):
                break

            next_url = data["nextRecordsUrl"]
            if not limiter.acquire(timeout=5.0):
                break
            outcome, data = client.query_url(next_url)
            if outcome.outcome != "ok":
                break

    finally:
        if owns_client:
            client.close()

    return total_processed


@interval(seconds=300)  # every 5 minutes
@dramatiq.actor(max_retries=0)
def scheduled_catchup() -> int:
    """Safety-net catch-up running every 5 minutes (build-plan 6.1.4)."""
    return run_catchup()


@interval(seconds=30)
@dramatiq.actor(max_retries=0)
def recover_limiter_rate(limiter: Optional[RateLimiter] = None, step: int = 5) -> int:
    """Recovers rate limit by +5 req/s every 30s after 429 throttling (5.2.2)."""
    limiter = limiter or get_rate_limiter()
    return limiter.recover(step=step)


@interval(seconds=5)
@dramatiq.actor(max_retries=0)
def sweep_outbound() -> None:
    """Periodic 5-second backup sweep to trigger outbound worker for missed wake-ups (5.3.3)."""
    now = timezone.now()
    if Outbox.objects.filter(state=Outbox.State.PENDING, next_attempt_at__lte=now).exists():
        send_outbound.send()
