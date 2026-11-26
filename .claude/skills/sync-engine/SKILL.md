---
name: sync-engine
description: How to build apply(), the outbox worker, the shared rate limiter, the recovery probe and catch-up (build-plan phases 2, 5, 6). The one place every brief rule (duplicate, out-of-order, sync loop, outage) is enforced. Use when implementing any of these, or reviewing code that touches Customer, Contact, ChangeEvent, FieldChange, Outbox or SyncState.
---

# The sync engine: `apply()`, outbound, limiter, catch-up

Source of truth: `docs/design.md` §5–§10 and the wiki Architecture page's `apply(change)` and
"Sending and retries" sections. This skill turns that into a build order and the mechanics
Phoebe needs. Don't re-decide anything here — it's already decided; this is the "how".

## 1. `apply()` (build-plan phase 2 — build this first)

Every inbound change, from any of the three paths, goes through one function. Put it in
`src/sync/services.py` (new file) as `apply(change: Change) -> Result`.

**The `Change` input** (2.1.1), a frozen dataclass or similar in the same file:

```python
@dataclass(frozen=True)
class Change:
    source: str          # "salesforce" | "portal"
    event_id: str        # ChangeEvent.event_id — see "event_id by source" below
    object_type: str      # "Customer" | "Contact"
    record_ref: str | int  # Salesforce ID (str) for source=salesforce, portal pk (int) for source=portal
    occurred_at: datetime
    actor: str
    fields: dict[str, Any]   # already-decided field values (see "which fields" below)
    operation: str        # "create" | "update"
```

**`event_id` by source** (design §8, Architecture "Where the inputs come from"):

| Source | `event_id` |
|---|---|
| Webhook | the payload's `eventId` |
| Portal save | a new UUID (one per save, not per field) |
| Catch-up | `record_id` + Salesforce's modified time |

**The transaction** (2.1.2, one Postgres transaction, matches the Architecture diagram exactly):

```python
SF_SOURCES = {"salesforce", "catch-up"}   # treated identically everywhere (2.1.1)

def apply(change: Change) -> Result:
    with transaction.atomic():
        try:
            # Savepoint: after a caught IntegrityError, Postgres aborts the enclosing transaction
            # unless the failing INSERT ran inside its own nested atomic(). Without it, every
            # later query in this call fails.
            with transaction.atomic():
                ChangeEvent.objects.create(event_id=change.event_id, source=change.source)
        except IntegrityError:
            return Result(outcome="duplicate")  # the unique constraint caught a replay

        # SELECT ... FOR UPDATE, create if new - see _lock_record below. May raise
        # UnknownRecord on an update/echo for a record that doesn't exist; let it propagate so
        # the whole transaction (ChangeEvent included) rolls back - a retry then isn't
        # misreported as a duplicate. The caller (e.g. the phase 3 webhook view) handles it.
        record, created = _lock_record(change)

        if change.source in SF_SOURCES and change.actor == INTEGRATION_USER_IDENTIFIER:
            return _record_echo(change, record)   # 2.2.2 — confirmation only, apply nothing

        applied_fields = []
        for name, value in _changed_fields(change, record).items():      # 2.2.1
            stored = record.field_meta.get(name, {}).get("at")
            stored_at = datetime.fromisoformat(stored) if stored else None   # ISO string in JSON
            is_newer = stored_at is None or change.occurred_at > stored_at \
                or (change.occurred_at == stored_at and change.source in SF_SOURCES)  # tie -> Salesforce
            FieldChange.objects.create(
                object_type=change.object_type, record_id=record.pk, field=name,
                value=str(value), actor=change.actor, source=change.source,
                occurred_at=change.occurred_at,
                status=FieldChange.Status.APPLIED if is_newer else FieldChange.Status.SUPERSEDED,
                event_id=change.event_id,
            )
            if is_newer:
                setattr(record, name, value)
                record.field_meta[name] = {"at": change.occurred_at.isoformat(), "source": change.source,
                                            "by": change.actor,
                                            "sf_value": value if change.source in SF_SOURCES else record.field_meta.get(name, {}).get("sf_value")}
                applied_fields.append(name)

        if created:
            record.created_by = change.actor
        if applied_fields:
            record.updated_by = change.actor                              # 2.2.4
        if created or applied_fields:
            record.save()

        if change.source == "portal" and applied_fields:                  # 2.3
            _upsert_outbox_row(record, applied_fields, change.operation)

        return Result(outcome="applied" if applied_fields else "no_change", applied_fields=applied_fields)
```

**`_lock_record`:** find-or-create, the one part the design leaves to implementation. Returns
`(record, created)` — use the `created` flag for `created_by` above, not `record._state.adding`
(unreliable once `get_or_create` has already saved the row).

- `source in SF_SOURCES`: look up by `salesforce_id=record_ref`. Not found **and**
  `operation="create"` **and not an echo** → create a new row with that `salesforce_id` and no
  `external_id` collision risk (UUID default). Use
  `Customer.objects.select_for_update().get_or_create(...)` inside the already-open transaction,
  not a separate query then a separate lock. Not found otherwise (an update, or any echo,
  including a create-echo) → raise `UnknownRecord`: an echo must never create a record, even if
  the "create" it's echoing hasn't landed locally yet for some reason.
- `source="portal"`: `record_ref` is the portal pk. For a create, the row was already made by
  the portal view before calling `apply()` (see `drf.md`: `perform_create` calls `apply()` with
  the just-created instance's pk) — so this is always a lookup, never a creation, for portal.
  Not found → raise `UnknownRecord` (shouldn't happen with a real pk, but never silently no-op).

**`_changed_fields`** (2.2.1 — Q2, revised 2026-11-27: the default assumption is now
**changed-fields-only**, matching the brief's own sample, which has exactly one field in
`changedFields`):

```python
def _changed_fields(change, record):
    if change.source == "portal":
        return change.fields          # the form only sends what changed
    # salesforce / catch-up: the payload normally carries only the changed fields (Q2's
    # default), but it may occasionally carry the whole record instead (Q2's "other way") -
    # this comparison is correct either way. Compare against what we last received FROM
    # Salesforce for that field, not against the current value - the current value may be a
    # newer portal edit we haven't told Salesforce about yet. Check "sf_value" not in meta
    # explicitly, not just meta.get("sf_value") != value - a field that really is None (not
    # just absent) must still count as changed the first time it's ever synced.
    changed = {}
    for name, value in change.fields.items():
        meta = record.field_meta.get(name, {})
        if "sf_value" not in meta or meta["sf_value"] != value:
            changed[name] = value
    return changed
```

This one line is what makes the assumption not actually matter: a stale field in a
whole-record webhook can't clobber a newer portal edit, and a changed-fields-only webhook
needs nothing extra — the check is a no-op (every field present already differs from
`sf_value` by definition). See design §7 and the brief's own city/phone scenario.

**Echo** (2.2.2): `INTEGRATION_USER_IDENTIFIER` is the Salesforce username/email the
integration user's webhooks arrive as — read it from settings (`SALESFORCE_INTEGRATION_USER`
or similar; add the setting if it doesn't exist yet, next to `SALESFORCE_TOKEN`). Record the
`ChangeEvent` (already done above) and a `FieldChange` row per field with `status=ECHO`, but
touch no column on the record and queue nothing. The one exception is `field_meta[f]["sf_value"]`:
set it to the echoed value, but only when `_is_newer(change, record, f)` holds (a field with no prior
`field_meta` entry counts as newer), so a late echo can't clobber a newer real value. Leave `at`, `source`
and `by` alone. Without this, a later genuine Salesforce revert to the old value looks unchanged and is
dropped (T-007).

**Outbox** (2.3.1 — merge, don't duplicate):

```python
def _upsert_outbox_row(record, applied_fields, operation):
    row, created = Outbox.objects.get_or_create(
        object_type=record.__class__.__name__, record_id=record.pk, state=Outbox.State.PENDING,
        defaults={"operation": operation, "fields": applied_fields, "next_attempt_at": timezone.now()},
    )
    if not created:
        row.fields = sorted(set(row.fields) | set(applied_fields))
        row.save(update_fields=["fields"])
```

Relies on `outbox_one_pending_per_record` (the partial unique constraint already on `Outbox`)
to make "is there already a pending row" race-safe even without this function's own locking —
the `get_or_create` + unique constraint pair is the standard Django pattern for it.

### Concurrency (2.4)

The `SELECT ... FOR UPDATE` in `_lock_record` is what makes two simultaneous `apply()` calls on
the same record serialize instead of racing. Test it by actually running two calls in separate
threads/connections and asserting the outcome, not by reasoning about it — see the
`sync-testing` skill.

## 2. Outbound task (build-plan phase 5)

New file `src/sync/tasks.py` (Dramatiq actors; this is what `<app>/tasks.py` discovery in
`CLAUDE.md` is for).

```python
@dramatiq.actor(max_retries=0)          # the outbox drives retries, not Dramatiq (design §6, Q7)
def send_outbound():
    now = timezone.now()
    for row in Outbox.objects.select_for_update(skip_locked=True).filter(
            state=Outbox.State.PENDING, next_attempt_at__lte=now
    ).order_by("next_attempt_at"):
        with transaction.atomic():
            row.lease_until = now + timedelta(seconds=60)
            row.save(update_fields=["lease_until"])
        _send_one(row)   # reads CURRENT field values at send time, not a stored snapshot (Architecture)
```

- **`SKIP LOCKED`** is why two worker processes never fight over the same row — the loop body
  must run inside its own short transaction per row, not one transaction for the whole batch,
  or `SKIP LOCKED` can't do its job.
- **Current values, not a snapshot:** read `getattr(record, field)` for each name in
  `row.fields` right before sending — this is what makes several merged edits collapse into one
  call and makes a retried send harmless (Architecture, "We send the current value").
- **Outcome handling** (5.3.2), one `match`/`if` block in `_send_one`:

  | Outcome | Action |
  |---|---|
  | accepted | `row.state = SENT`; `row.sent_at = now`; on a create, `record.salesforce_id = <returned id>; record.save()` |
  | unreachable / timeout / 5xx | `row.attempts += 1; row.next_attempt_at = now + min(2**row.attempts, 60)s; row.lease_until = None` |
  | 429 | same as above but also call the limiter's slow-down (below); honour `Retry-After` if present |
  | 400-type (bad data) | `row.state = NEEDS_REVIEW; row.last_error = <message>` — never deleted (Q9) |
  | 401/403 | stop the whole task early and raise/alert — don't keep burning the rate limit on calls that will all fail |

- **Wake-up:** `transaction.on_commit(lambda: send_outbound.send())` wherever `apply()` (or the
  portal view) commits a change that touched the outbox, **plus** a crontab job
  (`dramatiq_crontab`, `@interval(seconds=5)`) that calls `send_outbound.send()` unconditionally
  as a backstop for a lost wake-up (5.3.3). The task itself is idempotent (it only sends rows
  that are actually due), so calling it extra times is harmless.

## 3. Shared rate limiter (5.2)

One Redis key, one Lua script (atomic token bucket), used by **every** caller: sends, retries,
the probe, and catch-up queries. Put it in `src/sync/limiter.py`.

- State in Redis: current allowed rate (starts at `settings.SALESFORCE_RATE_LIMIT`) and a token
  count, refilled continuously.
- `limiter.acquire(timeout=...)` blocks (or the caller re-queues) until a token is free.
- On a 429: `limiter.throttle()` halves the current rate immediately, then a crontab job
  (`@interval(seconds=30)`) adds 5 req/s back, capped at the configured limit, until it's
  recovered.
- Test with `redis-py`'s real client against the Redis the compose stack already runs — no
  need to mock Redis for this.

## 4. Down detection and probe (5.4)

`SyncState` row `key="salesforce"`, `value={"status": "up"|"down", "consecutive_failures": N}`.

- Every outbound failure classified as "unreachable" increments `consecutive_failures`; any
  success resets it to 0.
- At 5, set `status="down"`. While down, `send_outbound` should skip sending (checked at the
  top of the task) so it doesn't burn attempts against a Salesforce that's confirmed down.
- A separate crontab actor, `@interval(seconds=10)`, does a cheap probe call. On success:
  `status="up"`, `consecutive_failures=0`, then **run catch-up before anything else** (next
  section), then let `send_outbound` resume on its own next tick.

## 5. Catch-up (build-plan phase 6)

`src/sync/tasks.py`, another actor, `run_catchup()`:

1. Read `SyncState.key="watermark"` (a timestamp); subtract a few minutes of overlap.
2. Query the Salesforce client for "changed since T", 2,000 records/page, through the limiter
   (6.1.1).
3. For each record returned, build a `Change(source="salesforce", event_id=f"{record_id}:{modified_at}", ...)`
   and call `apply()` (6.1.2). The echo check in `apply()` is **field-level** (compares each
   field's `sf_value`, same code path as a webhook) — don't add a record-level "skip if last
   modified by us" shortcut; it would skip real changes that happened before our own edit in
   the same record.
4. Advance the watermark to the latest `modified_at` seen **only after that page's transaction
   commits** (6.1.3) — so a crash mid-page re-reads the same page instead of skipping it.
5. Triggered by the probe's success (step above) and by a `@interval(minutes=5)` safety net
   (6.1.4). Both call the same function; it's idempotent because `apply()` already is.

## Build order inside this skill

Follow build-plan order: 2.1 → 2.2 → 2.3 → 2.4 (all testable with zero network), then 5.1 (the
`SalesforceClient`, needs `sync-test-scenarios`'s fake) → 5.2 → 5.3 → 5.4 → 5.5, then 6.1.
Don't start the outbound task before `apply()`'s tests pass — it depends on the outbox rows
`apply()` produces.
