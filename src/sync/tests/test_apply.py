"""T-003 acceptance tests for apply() (build-plan 2.1-2.4). Owned by agent-ross.

Every apply() test passes an explicit occurred_at (sync-testing §1): no freezegun, no
timezone.now() for ordering. The concurrency tests use TransactionTestCase with real threads
(sync-testing §2).
"""

import ast
import re
import threading
import tomllib
import uuid
from dataclasses import FrozenInstanceError
from dataclasses import fields as dc_fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import get_resolver

from sync import services
from sync.models import ChangeEvent, Contact, Customer, FieldChange, Outbox
from sync.services import Change, Result, UnknownRecord, apply

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
T3 = T0 + timedelta(hours=3)
T4 = T0 + timedelta(hours=4)
T5 = T0 + timedelta(hours=5)

INTEGRATION_USER = "integration@salesforce.example"
SF_CONTACT = {
    "full_name": "Ada Lovelace",
    "phone": "111",
    "email": "ada@example.com",
    "city": "Old",
    "owner": "owner-1",
}
SF_CUSTOMER = {"name": "Acme", "status": "active", "owner": "owner-1"}

db = pytest.mark.django_db
brief = pytest.mark.brief


def eid():
    return f"evt-{uuid.uuid4()}"


def sf_change(ref, fields, at, *, actor="sf-user", operation="update", source="salesforce",
              object_type="Contact", event_id=None):
    return Change(source=source, event_id=event_id or eid(), object_type=object_type,
                  record_ref=ref, occurred_at=at, actor=actor, fields=fields, operation=operation)


def portal_change(pk, fields, at, *, actor="portal-user", operation="update",
                  object_type="Contact", event_id=None):
    return Change(source="portal", event_id=event_id or eid(), object_type=object_type,
                  record_ref=pk, occurred_at=at, actor=actor, fields=fields, operation=operation)


def sf_contact(sf_id, at=T0, actor="sf-creator", **overrides):
    """Create a Contact the way Salesforce does: an operation=create change through apply()."""
    result = apply(sf_change(sf_id, {**SF_CONTACT, **overrides}, at, actor=actor, operation="create"))
    assert result.outcome == "applied"
    return Contact.objects.get(salesforce_id=sf_id)


def reload(pk):
    return Contact.objects.get(pk=pk)


def record_state(pk):
    """Everything a write to the record or its outbox would change (not history, not ChangeEvent)."""
    return {
        "record": Contact.objects.values().get(pk=pk),
        "outbox": list(Outbox.objects.order_by("pk").values()),
    }


def run_in_threads(jobs):
    """Start one thread per job behind a barrier. Each thread closes its own DB connection in a
    finally. Exceptions are collected and re-raised in the caller; nothing is swallowed."""
    barrier = threading.Barrier(len(jobs))
    results = [None] * len(jobs)
    errors = []

    def runner(index, job):
        try:
            barrier.wait(timeout=10)
            results[index] = job()
        except BaseException as exc:  # collected, re-raised below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=runner, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "apply() hung: possible deadlock"
    if errors:
        raise errors[0]
    return results


# --- AC-2.1.1: Change, Result, outcomes -------------------------------------------------------

def test_ac_2_1_1_change_is_frozen_with_exact_fields():
    change = sf_change("003X", {"phone": "1"}, T1)
    assert [f.name for f in dc_fields(Change)] == [
        "source", "event_id", "object_type", "record_ref", "occurred_at", "actor", "fields", "operation",
    ]
    assert [f.name for f in dc_fields(Result)] == ["outcome", "applied_fields"]
    with pytest.raises(FrozenInstanceError):
        change.source = "portal"


@db
def test_ac_2_1_1_apply_returns_result_with_allowed_outcome():
    contact = sf_contact("003A01")
    result = apply(sf_change("003A01", {"phone": "222"}, T1))
    assert isinstance(result, Result)
    assert result.outcome in {"applied", "no_change", "duplicate", "echo"}
    assert result.applied_fields == ["phone"]
    assert reload(contact.pk).phone == "222"


@db
def test_ac_2_1_1_catch_up_behaves_like_salesforce():
    contact = sf_contact("003A02")
    result = apply(sf_change("003A02", {"phone": "222"}, T1, source="catch-up"))
    assert result == Result(outcome="applied", applied_fields=["phone"])
    assert Outbox.objects.count() == 0
    meta = reload(contact.pk).field_meta["phone"]
    assert meta["source"] == "catch-up"
    assert meta["sf_value"] == "222"


# --- AC-2.1.2a: duplicate event is a no-op ----------------------------------------------------

@db
@brief
def test_ac_2_1_2a_duplicate_event_is_noop():
    contact = sf_contact("003B01")

    # Salesforce change: apply once, replay with different values.
    sf_event = eid()
    assert apply(sf_change("003B01", {"phone": "222"}, T1, event_id=sf_event)).outcome == "applied"
    state, history = record_state(contact.pk), FieldChange.objects.count()
    dup = apply(sf_change("003B01", {"phone": "999", "email": "dup@example.com"}, T2, event_id=sf_event))
    assert dup == Result(outcome="duplicate", applied_fields=[])
    assert record_state(contact.pk) == state
    assert FieldChange.objects.count() == history
    assert ChangeEvent.objects.filter(event_id=sf_event).count() == 1

    # Portal change: same rule, and the replay must not add an outbox row or merge into one.
    portal_event = eid()
    assert apply(portal_change(contact.pk, {"city": "Portal City"}, T3, event_id=portal_event)).outcome == "applied"
    state, history = record_state(contact.pk), FieldChange.objects.count()
    assert Outbox.objects.count() == 1
    dup = apply(portal_change(contact.pk, {"city": "Other", "phone": "000"}, T4, event_id=portal_event))
    assert dup.outcome == "duplicate"
    assert record_state(contact.pk) == state
    assert FieldChange.objects.count() == history
    assert ChangeEvent.objects.filter(event_id=portal_event).count() == 1


# --- AC-2.1.2b: one transaction ---------------------------------------------------------------

@db
def test_ac_2_1_2b_failure_after_record_save_rolls_back_everything():
    contact = sf_contact("003C01")
    state, history = record_state(contact.pk), FieldChange.objects.count()
    change = portal_change(contact.pk, {"city": "Boom"}, T2)
    with patch("sync.services._upsert_outbox_row", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            apply(change)
    assert record_state(contact.pk) == state
    assert FieldChange.objects.count() == history
    assert not ChangeEvent.objects.filter(event_id=change.event_id).exists()


# --- AC-2.1.2c: record lookup and creation ----------------------------------------------------

@db
def test_ac_2_1_2c_salesforce_create_makes_one_customer_and_one_contact():
    result = apply(sf_change("001AAA", SF_CUSTOMER, T0, operation="create", object_type="Customer"))
    assert result.outcome == "applied"
    customer = Customer.objects.get(salesforce_id="001AAA")
    assert (customer.name, customer.status, customer.owner) == ("Acme", "active", "owner-1")
    sf_contact("003C02")
    assert Contact.objects.filter(salesforce_id="003C02").count() == 1
    assert Customer.objects.count() == 1 and Contact.objects.count() == 1


@db
def test_ac_2_1_2c_second_salesforce_update_finds_same_row():
    contact = sf_contact("003C03")
    apply(sf_change("003C03", {"phone": "555"}, T1))
    assert Contact.objects.filter(salesforce_id="003C03").count() == 1
    assert Contact.objects.get(salesforce_id="003C03").pk == contact.pk
    assert reload(contact.pk).phone == "555"


@db
@pytest.mark.parametrize("operation", ["update", "create"])
def test_ac_2_1_2c_unknown_portal_pk_raises_never_creates(operation):
    change = portal_change(999_999_999, {"city": "X"}, T1, operation=operation)
    with pytest.raises(UnknownRecord):
        apply(change)
    assert Contact.objects.count() == 0
    assert not ChangeEvent.objects.filter(event_id=change.event_id).exists()
    # The ChangeEvent was rolled back, so a retry raises again instead of reporting duplicate.
    with pytest.raises(UnknownRecord):
        apply(change)


@db
def test_ac_2_1_2c_unknown_salesforce_update_raises_and_creates_nothing():
    change = sf_change("003NOPE", {"phone": "1"}, T1)
    with pytest.raises(UnknownRecord):
        apply(change)
    assert not Contact.objects.filter(salesforce_id="003NOPE").exists()
    assert not ChangeEvent.objects.filter(event_id=change.event_id).exists()


@db
@override_settings(SALESFORCE_INTEGRATION_USER=INTEGRATION_USER)
def test_ac_2_1_2c_echo_create_for_unknown_id_raises_and_creates_nothing():
    # DEFAULTED (Phoebe): an echo never creates a record, so no second copy of it appears.
    change = sf_change("003ECHO", SF_CONTACT, T1, actor=INTEGRATION_USER, operation="create")
    with pytest.raises(UnknownRecord):
        apply(change)
    assert not Contact.objects.filter(salesforce_id="003ECHO").exists()
    assert not ChangeEvent.objects.filter(event_id=change.event_id).exists()


# --- AC-2.1.2d: the lock is taken before the field check --------------------------------------

@db
def test_ac_2_1_2d_record_row_is_locked_inside_apply():
    # Smoke check only: AC-2.4.1 and AC-2.4.2 are the proof of the lock's behaviour.
    contact = sf_contact("003D01")
    with CaptureQueriesContext(connection) as ctx:
        apply(sf_change("003D01", {"phone": "222"}, T1))
    locks = [q["sql"] for q in ctx.captured_queries if "FOR UPDATE" in q["sql"]]
    assert any("sync_contact" in sql for sql in locks)
    assert reload(contact.pk).phone == "222"


# --- AC-2.2.1: changed fields are compared with sf_value --------------------------------------

@db
def test_ac_2_2_1_whole_record_webhook_does_not_undo_newer_portal_city():
    contact = sf_contact("003E01")                         # city "Old", sf_value "Old"
    assert apply(portal_change(contact.pk, {"city": "X"}, T2)).outcome == "applied"
    result = apply(sf_change("003E01", {**SF_CONTACT, "city": "Old", "phone": "222"}, T3))
    assert result == Result(outcome="applied", applied_fields=["phone"])
    record = reload(contact.pk)
    assert record.city == "X"
    assert record.phone == "222"


@db
def test_ac_2_2_1_whole_record_equal_to_sf_value_is_no_change():
    contact = sf_contact("003E02")
    state = record_state(contact.pk)
    result = apply(sf_change("003E02", dict(SF_CONTACT), T3))
    assert result == Result(outcome="no_change", applied_fields=[])
    assert record_state(contact.pk) == state


@db
def test_ac_2_2_1_field_without_sf_value_counts_as_changed():
    contact = Contact.objects.create(
        salesforce_id="003E03", full_name="Ada", phone="111", email="ada@example.com",
        city="Old", owner="owner-1", field_meta={},
    )
    result = apply(sf_change("003E03", {"phone": "111"}, T1))
    assert result == Result(outcome="applied", applied_fields=["phone"])
    assert reload(contact.pk).field_meta["phone"]["sf_value"] == "111"


@db
def test_ac_2_2_1_portal_source_is_not_filtered_by_sf_value():
    contact = sf_contact("003E04")                         # city "Old", sf_value "Old"
    result = apply(portal_change(contact.pk, {"city": "Old"}, T1))
    assert result == Result(outcome="applied", applied_fields=["city"])


# --- AC-2.2.2: echo, and the integration user setting -----------------------------------------

@db
@brief
@override_settings(SALESFORCE_INTEGRATION_USER=INTEGRATION_USER)
@pytest.mark.parametrize("source", ["salesforce", "catch-up"])
def test_ac_2_2_2_integration_user_echo_writes_history_only(source):
    contact = sf_contact("003F01")
    state = record_state(contact.pk)
    change = sf_change("003F01", {"phone": "999", "city": "Echoed"}, T3,
                       actor=INTEGRATION_USER, source=source)
    assert apply(change) == Result(outcome="echo", applied_fields=[])
    assert record_state(contact.pk) == state
    echoes = FieldChange.objects.filter(event_id=change.event_id)
    assert sorted(echoes.values_list("field", flat=True)) == ["city", "phone"]
    assert {e.status for e in echoes} == {"echo"}
    assert ChangeEvent.objects.filter(event_id=change.event_id).exists()
    assert Outbox.objects.count() == 0


@db
@override_settings(SALESFORCE_INTEGRATION_USER=INTEGRATION_USER)
def test_ac_2_2_2_portal_change_with_integration_actor_is_not_echo():
    contact = sf_contact("003F02")
    result = apply(portal_change(contact.pk, {"city": "Portal"}, T2, actor=INTEGRATION_USER))
    assert result == Result(outcome="applied", applied_fields=["city"])
    assert Outbox.objects.filter(record_id=contact.pk, state="pending").count() == 1


def test_ac_2_2_2_setting_key_exists_in_settings_env_and_env_example():
    assert hasattr(settings, "SALESFORCE_INTEGRATION_USER")
    for name in (".env", ".env.example"):
        text = (ROOT / name).read_text()  # key names only; values are never printed
        assert re.search(r"^SALESFORCE_INTEGRATION_USER=", text, re.MULTILINE), f"{name} lacks the key"


# --- AC-2.2.3: per-field history and ordering -------------------------------------------------

@db
def test_ac_2_2_3_brief_scenario_end_to_end():
    contact = sf_contact("003G01")                         # city "Old", phone "111"
    assert apply(portal_change(contact.pk, {"city": "New City"}, T2)).outcome == "applied"
    result = apply(sf_change("003G01", {**SF_CONTACT, "city": "Old", "phone": "222"}, T3))
    assert result == Result(outcome="applied", applied_fields=["phone"])
    record = reload(contact.pk)
    assert record.city == "New City"
    assert record.phone == "222"
    row = Outbox.objects.get()
    assert (row.object_type, row.record_id, row.fields, row.state) == ("Contact", contact.pk, ["city"], "pending")


@db
@brief
def test_ac_2_2_3_older_event_after_newer_is_superseded_and_kept_in_history():
    contact = sf_contact("003G02")
    assert apply(sf_change("003G02", {"city": "Newer"}, T2)).outcome == "applied"
    older = sf_change("003G02", {"city": "Older"}, T1)
    assert apply(older) == Result(outcome="no_change", applied_fields=[])
    assert reload(contact.pk).city == "Newer"
    kept = FieldChange.objects.get(event_id=older.event_id)
    assert (kept.field, kept.value, kept.status) == ("city", "Older", "superseded")
    assert FieldChange.objects.filter(record_id=contact.pk, field="city", status="applied").count() == 2


@db
def test_ac_2_2_3_two_fields_in_one_change_both_applied():
    contact = sf_contact("003G03")
    change = sf_change("003G03", {"phone": "222", "email": "new@example.com"}, T1)
    result = apply(change)
    assert result.outcome == "applied"
    assert sorted(result.applied_fields) == ["email", "phone"]
    rows = FieldChange.objects.filter(event_id=change.event_id)
    assert rows.count() == 2
    assert {r.status for r in rows} == {"applied"}
    assert reload(contact.pk).email == "new@example.com"


@db
def test_ac_2_2_3_exact_tie_goes_to_salesforce():
    contact = sf_contact("003G04")
    assert apply(portal_change(contact.pk, {"city": "Portal"}, T1)).outcome == "applied"
    tie_sf = apply(sf_change("003G04", {"city": "Salesforce"}, T1))
    assert tie_sf == Result(outcome="applied", applied_fields=["city"])
    assert reload(contact.pk).city == "Salesforce"

    # Reverse: a portal edit at the same instant as the stored Salesforce write loses.
    tie_portal = portal_change(contact.pk, {"city": "Late portal"}, T1)
    assert apply(tie_portal) == Result(outcome="no_change", applied_fields=[])
    assert reload(contact.pk).city == "Salesforce"
    assert FieldChange.objects.get(event_id=tie_portal.event_id).status == "superseded"


# --- AC-2.2.4: field_meta, updated_by, created_by ---------------------------------------------

@db
def test_ac_2_2_4_field_meta_shape_after_salesforce_apply():
    contact = sf_contact("003H01")
    apply(sf_change("003H01", {"phone": "222"}, T3, actor="sf-user"))
    assert reload(contact.pk).field_meta["phone"] == {
        "at": T3.isoformat(), "source": "salesforce", "by": "sf-user", "sf_value": "222",
    }


@db
def test_ac_2_2_4_portal_edit_keeps_sf_value():
    contact = sf_contact("003H02")                         # city "Old", sf_value "Old"
    apply(portal_change(contact.pk, {"city": "Portal"}, T2, actor="portal-user"))
    assert reload(contact.pk).field_meta["city"] == {
        "at": T2.isoformat(), "source": "portal", "by": "portal-user", "sf_value": "Old",
    }


@db
def test_ac_2_2_4_superseded_field_leaves_field_meta_alone():
    contact = sf_contact("003H03")
    apply(sf_change("003H03", {"city": "Newer"}, T2))
    meta = reload(contact.pk).field_meta["city"]
    apply(sf_change("003H03", {"city": "Older"}, T1))
    assert reload(contact.pk).field_meta["city"] == meta


@db
def test_ac_2_2_4_updated_by_only_moves_on_applied_changes():
    contact = sf_contact("003H04")
    apply(sf_change("003H04", {"city": "New"}, T2, actor="sf-user"))
    assert reload(contact.pk).updated_by == "sf-user"
    apply(sf_change("003H04", {"city": "Stale"}, T1, actor="sf-stale"))          # superseded
    assert reload(contact.pk).updated_by == "sf-user"
    apply(sf_change("003H04", {"city": "New"}, T3, actor="sf-same"))             # no_change
    assert reload(contact.pk).updated_by == "sf-user"
    dup = sf_change("003H04", {"city": "Dup"}, T4, actor="sf-dup")
    apply(dup)
    apply(dup)                                                                    # duplicate
    assert reload(contact.pk).updated_by == "sf-dup"


@db
def test_ac_2_2_4_created_by_set_on_create_and_never_overwritten():
    contact = sf_contact("003H05")
    assert (contact.created_by, contact.updated_by) == ("sf-creator", "sf-creator")
    apply(portal_change(contact.pk, {"city": "P"}, T2, actor="portal-user"))
    record = reload(contact.pk)
    assert (record.created_by, record.updated_by) == ("sf-creator", "portal-user")
    apply(sf_change("003H05", {"phone": "9"}, T3, actor="sf-other"))
    assert reload(contact.pk).created_by == "sf-creator"


# --- AC-2.3.1: outbox upsert -------------------------------------------------------------------

@db
def test_ac_2_3_1_three_portal_edits_merge_into_one_pending_row():
    contact = sf_contact("003I01")
    for fields, at in [({"city": "A"}, T1), ({"phone": "5"}, T2), ({"email": "b@example.com"}, T3)]:
        assert apply(portal_change(contact.pk, fields, at)).outcome == "applied"
    rows = Outbox.objects.filter(object_type="Contact", record_id=contact.pk)
    assert rows.count() == 1
    row = rows.get()
    assert row.state == "pending"
    assert row.operation == "update"
    assert row.fields == ["city", "email", "phone"]
    assert row.next_attempt_at is not None


@db
def test_ac_2_3_1_sent_row_does_not_block_new_pending_row():
    contact = sf_contact("003I02")
    Outbox.objects.create(object_type="Contact", record_id=contact.pk, operation="update",
                          fields=["city"], state="sent", next_attempt_at=T0)
    apply(portal_change(contact.pk, {"phone": "7"}, T1))
    assert Outbox.objects.filter(record_id=contact.pk, state="pending").count() == 1
    assert Outbox.objects.filter(record_id=contact.pk).count() == 2


@db
def test_ac_2_3_1_db_rejects_second_pending_row_for_a_record():
    contact = sf_contact("003I03")
    apply(portal_change(contact.pk, {"city": "A"}, T1))
    with pytest.raises(IntegrityError), transaction.atomic():
        Outbox.objects.create(object_type="Contact", record_id=contact.pk, operation="update",
                              fields=["phone"], next_attempt_at=T1)


@db
def test_ac_2_3_1_all_superseded_portal_change_creates_no_row():
    contact = sf_contact("003I04")
    apply(sf_change("003I04", {"city": "Salesforce newer"}, T5))
    result = apply(portal_change(contact.pk, {"city": "Stale portal"}, T1))
    assert result == Result(outcome="no_change", applied_fields=[])
    assert Outbox.objects.count() == 0


# --- AC-2.3.2: sync loop (brief) ----------------------------------------------------------------

@db
@brief
def test_ac_2_3_2_salesforce_create_writes_no_outbox_row():
    sf_contact("003J01")
    assert Outbox.objects.count() == 0


@db
@brief
@override_settings(SALESFORCE_INTEGRATION_USER=INTEGRATION_USER)
def test_ac_2_3_2_salesforce_outcomes_leave_pending_portal_row_untouched():
    contact = sf_contact("003J02")
    apply(portal_change(contact.pk, {"city": "Portal"}, T1))
    row = Outbox.objects.get(state="pending")
    before = (row.pk, row.fields, row.operation)

    dup_event = eid()
    outcomes = [
        apply(sf_change("003J02", {"phone": "2"}, T2)).outcome,                     # applied
        apply(sf_change("003J02", {"phone": "2"}, T3)).outcome,                     # no_change
        apply(sf_change("003J02", {"email": "e@example.com"}, T3, event_id=dup_event)).outcome,
        apply(sf_change("003J02", {"email": "zz@example.com"}, T4, event_id=dup_event)).outcome,  # duplicate
        apply(sf_change("003J02", {"phone": "echo"}, T4, actor=INTEGRATION_USER)).outcome,        # echo
    ]
    assert outcomes == ["applied", "no_change", "applied", "duplicate", "echo"]
    assert Outbox.objects.count() == 1
    row.refresh_from_db()
    assert (row.pk, row.fields, row.operation) == before


@db
@brief
@override_settings(SALESFORCE_INTEGRATION_USER=INTEGRATION_USER)
def test_ac_2_3_2_salesforce_update_on_record_with_pending_row_adds_no_row():
    contact = sf_contact("003J03")
    apply(portal_change(contact.pk, {"city": "P"}, T1))
    apply(sf_change("003J03", {"phone": "8"}, T2))
    assert Outbox.objects.count() == 1
    assert Outbox.objects.get().fields == ["city"]


# --- AC-2.3.3: create stays create ---------------------------------------------------------------

@db
def test_ac_2_3_3_pending_create_row_stays_create_after_merge():
    contact = sf_contact("003K01")
    apply(portal_change(contact.pk, {"city": "A"}, T1, operation="create"))
    row = Outbox.objects.get(state="pending")
    assert row.operation == "create"
    apply(portal_change(contact.pk, {"phone": "8"}, T2, operation="update"))
    row.refresh_from_db()
    assert row.operation == "create"
    assert row.fields == ["city", "phone"]
    assert Outbox.objects.filter(state="pending").count() == 1


# --- AC-2.4.1 / 2.4.2 / 2.4.3: concurrency (real threads) ---------------------------------------

def check_portal_vs_salesforce_race(sf_id):
    contact = sf_contact(sf_id)                            # city "Old"
    portal = portal_change(contact.pk, {"city": "Portal"}, T1)
    sf = sf_change(sf_id, {"city": "Salesforce"}, T2)
    run_in_threads([lambda: apply(portal), lambda: apply(sf)])

    record = reload(contact.pk)
    assert record.city == "Salesforce"                     # newest occurred_at wins
    assert record.field_meta["city"]["at"] == T2.isoformat()
    assert FieldChange.objects.get(event_id=sf.event_id).status == "applied"
    # Lock order decides whether the portal edit lands first (applied) or second (superseded).
    assert FieldChange.objects.get(event_id=portal.event_id).status in {"applied", "superseded"}
    assert FieldChange.objects.filter(event_id__in=[portal.event_id, sf.event_id]).count() == 2
    assert ChangeEvent.objects.filter(event_id=portal.event_id).count() == 1
    assert ChangeEvent.objects.filter(event_id=sf.event_id).count() == 1
    assert Outbox.objects.filter(record_id=contact.pk, state="pending").count() <= 1


def check_whole_record_race_loses_no_field(sf_id):
    contact = sf_contact(sf_id)                            # city "Old", phone "111"
    a = sf_change(sf_id, {**SF_CONTACT, "city": "NewCity"}, T1)
    b = sf_change(sf_id, {**SF_CONTACT, "city": "NewCity", "phone": "NewPhone"}, T2)
    run_in_threads([lambda: apply(a), lambda: apply(b)])

    record = reload(contact.pk)
    assert (record.city, record.phone) == ("NewCity", "NewPhone")
    assert record.field_meta["city"]["sf_value"] == "NewCity"
    assert record.field_meta["phone"]["sf_value"] == "NewPhone"
    assert ChangeEvent.objects.filter(event_id__in=[a.event_id, b.event_id]).count() == 2
    assert not Outbox.objects.filter(record_id=contact.pk).exists()  # salesforce writes: no outbox


def check_same_event_id_race(sf_id):
    contact = sf_contact(sf_id)
    shared = eid()
    a = sf_change(sf_id, {"phone": "DUP"}, T1, event_id=shared)
    b = sf_change(sf_id, {"phone": "DUP"}, T1, event_id=shared)
    results = run_in_threads([lambda: apply(a), lambda: apply(b)])
    assert sorted(r.outcome for r in results) == ["applied", "duplicate"]
    assert ChangeEvent.objects.filter(event_id=shared).count() == 1
    assert reload(contact.pk).phone == "DUP"


class ConcurrentApplyTests(TransactionTestCase):
    def test_ac_2_4_1_portal_and_salesforce_race_on_one_field(self):
        check_portal_vs_salesforce_race("003L01")

    def test_ac_2_4_2_two_whole_record_webhooks_race_without_losing_a_field(self):
        check_whole_record_race_loses_no_field("003L02")

    def test_ac_2_4_2_same_event_id_race_gives_one_applied_one_duplicate(self):
        check_same_event_id_race("003L03")

    def test_ac_2_4_3_all_three_races_hold_over_ten_rounds(self):
        # Ten fresh rounds in one run. The shell loop in the ledger repeats the whole file ten times.
        for i in range(10):
            check_portal_vs_salesforce_race(f"003R{i:02d}A")
            check_whole_record_race_loses_no_field(f"003R{i:02d}B")
            check_same_event_id_race(f"003R{i:02d}C")


# --- AC-2.5: brief markers and cross-cutting checks ---------------------------------------------

def test_ac_2_5_brief_marker_registered_and_on_the_four_rule_tests():
    markers = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("brief:") for m in markers)
    module = __import__(__name__, fromlist=["x"])
    expected = [
        "test_ac_2_1_2a_duplicate_event_is_noop",                       # duplicate
        "test_ac_2_2_3_older_event_after_newer_is_superseded_and_kept_in_history",  # out-of-order
        "test_ac_2_2_2_integration_user_echo_writes_history_only",      # echo
        "test_ac_2_3_2_salesforce_outcomes_leave_pending_portal_row_untouched",  # sync loop
    ]
    for name in expected:
        marks = [m.name for m in getattr(module, name).pytestmark]
        assert "brief" in marks, name


# --- AC-2.6: no webhook, no task, no network in apply() -----------------------------------------

def test_ac_2_6_services_module_imports_no_network_or_queue_code():
    tree = ast.parse(Path(services.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ["httpx", "requests", "redis", "dramatiq", "urllib", "socket", "salesforce"]:
        assert banned not in imported, banned


def _url_strings(patterns):
    for p in patterns:
        yield str(p.pattern)
        if hasattr(p, "url_patterns"):
            yield from _url_strings(p.url_patterns)


def test_ac_2_6_no_webhook_route_or_task_module_in_this_phase():
    assert not (ROOT / "src" / "sync" / "tasks.py").exists()
    assert not any("webhook" in s.lower() for s in _url_strings(get_resolver().url_patterns))


@db
def test_ac_2_6_apply_makes_no_network_call():
    contact = sf_contact("003M01")
    with patch("socket.socket.connect", side_effect=AssertionError("network call")):
        apply(portal_change(contact.pk, {"city": "Q"}, T2))
        apply(sf_change("003M01", {"phone": "4"}, T3))
    assert reload(contact.pk).phone == "4"
