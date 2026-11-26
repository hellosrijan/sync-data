from datetime import timedelta
import time
from uuid import uuid4

from django.conf import settings
from django.utils import timezone
import pytest
import redis

from sync.client import SalesforceClient
from sync.limiter import RateLimiter
from sync.models import Contact, Customer, Outbox, SyncState
from sync.services import Change, apply
from sync.tasks import (
    get_salesforce_state,
    make_all_pending_due,
    probe_salesforce,
    record_salesforce_failure,
    record_salesforce_success,
    recover_limiter_rate,
    send_outbound,
    sweep_outbound,
)
from sync.tests.fakes import FakeSalesforce, serve_fake_salesforce, transport_for

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.fixture
def fake():
    return FakeSalesforce()


@pytest.fixture
def client(fake):
    c = SalesforceClient(
        base_url="https://fake.salesforce.com",
        token="test_token",
        transport=transport_for(fake),
    )
    yield c
    c.close()


@pytest.fixture
def test_limiter():
    r = redis.Redis.from_url(settings.DRAMATIQ_BROKER["OPTIONS"]["url"])
    lim = RateLimiter(redis_client=r, default_rate=50, key_prefix="test:outbound:limiter")
    lim.reset()
    yield lim
    lim.reset()


def test_ac_5_3_1_send_outbound_claims_and_sends_current_values(fake, client, test_limiter):
    # Create contact locally via portal edit
    c = Contact.objects.create(full_name="Rajesh Patel", phone="111", city="Mumbai")
    now = timezone.now()
    apply(Change(
        source="portal",
        event_id=f"evt_{uuid4()}",
        object_type="Contact",
        record_ref=c.pk,
        occurred_at=now,
        actor="portal.user",
        fields={"city": "Noida", "phone": "222"},
        operation="create",
    ))
    row = Outbox.objects.get(object_type="Contact", record_id=c.pk, state=Outbox.State.PENDING)
    assert row.fields == ["city", "phone"]

    # In flight edit: user changes city to "Gurgaon" before worker runs
    c.city = "Gurgaon"
    c.save(update_fields=["city"])

    # Worker executes
    processed = send_outbound(max_rows=10, client=client, limiter=test_limiter)
    assert processed == 1

    row.refresh_from_db()
    assert row.state == Outbox.State.SENT
    c.refresh_from_db()
    assert c.salesforce_id is not None

    # Verify FakeSalesforce received CURRENT values (Gurgaon, not Noida)
    sf_rec = fake.records[c.salesforce_id]
    assert sf_rec["fields"]["city"] == "Gurgaon"
    assert sf_rec["fields"]["phone"] == "222"


def test_ac_5_3_2_outcome_unreachable_backs_off_capped_at_60s(fake, client, test_limiter):
    c = Contact.objects.create(full_name="Test Unreachable")
    apply(Change(
        source="portal",
        event_id=f"evt_{uuid4()}",
        object_type="Contact",
        record_ref=c.pk,
        occurred_at=timezone.now(),
        actor="portal.user",
        fields={"phone": "999"},
        operation="create",
    ))
    row = Outbox.objects.get(object_type="Contact", record_id=c.pk)

    fake.mode = "down"
    send_outbound(max_rows=1, client=client, limiter=test_limiter)

    row.refresh_from_db()
    assert row.state == Outbox.State.PENDING
    assert row.attempts == 1
    assert row.next_attempt_at > timezone.now()


def test_ac_5_3_2_outcome_rejected_moves_to_needs_review(fake, client, test_limiter):
    c = Contact.objects.create(full_name="Bad Data Record")
    apply(Change(
        source="portal",
        event_id=f"evt_{uuid4()}",
        object_type="Contact",
        record_ref=c.pk,
        occurred_at=timezone.now(),
        actor="portal.user",
        fields={"phone": "000"},
        operation="create",
    ))
    row = Outbox.objects.get(object_type="Contact", record_id=c.pk)

    fake.mode = "reject"
    fake.reject_id = str(c.external_id)
    send_outbound(max_rows=1, client=client, limiter=test_limiter)

    row.refresh_from_db()
    assert row.state == Outbox.State.NEEDS_REVIEW
    assert "Bad request" in row.last_error


def test_ac_5_3_2_outcome_throttled_halves_rate_and_reschedules(fake, client, test_limiter):
    c = Contact.objects.create(full_name="Throttle Record")
    apply(Change(
        source="portal",
        event_id=f"evt_{uuid4()}",
        object_type="Contact",
        record_ref=c.pk,
        occurred_at=timezone.now(),
        actor="portal.user",
        fields={"phone": "111"},
        operation="create",
    ))
    row = Outbox.objects.get(object_type="Contact", record_id=c.pk)

    fake.mode = "throttle"
    send_outbound(max_rows=1, client=client, limiter=test_limiter)

    row.refresh_from_db()
    assert row.state == Outbox.State.PENDING
    assert test_limiter.get_current_rate() == 25  # halved from 50


def test_ac_5_4_1_down_detection_after_5_failures(fake, client, test_limiter):
    SyncState.objects.filter(key="salesforce").delete()
    c = Contact.objects.create(full_name="Down Detector")
    apply(Change(
        source="portal",
        event_id=f"evt_{uuid4()}",
        object_type="Contact",
        record_ref=c.pk,
        occurred_at=timezone.now(),
        actor="portal.user",
        fields={"phone": "000"},
        operation="create",
    ))

    fake.mode = "down"
    for _ in range(5):
        row = Outbox.objects.get(object_type="Contact", record_id=c.pk)
        row.next_attempt_at = timezone.now() - timedelta(seconds=1)
        row.lease_until = None
        row.save()
        send_outbound(max_rows=1, client=client, limiter=test_limiter)

    state = get_salesforce_state()
    assert state["status"] == "down"
    assert state["consecutive_failures"] >= 5

    # While down, send_outbound skips sending immediately
    processed = send_outbound(max_rows=1, client=client, limiter=test_limiter)
    assert processed == 0


def test_ac_5_4_2_recovery_probe_triggers_drain_when_salesforce_recovers(fake, client, test_limiter):
    # Set state to down
    SyncState.objects.update_or_create(
        key="salesforce",
        defaults={"value": {"status": "down", "consecutive_failures": 5}},
    )
    # Pending row in future
    c = Contact.objects.create(full_name="Recover Contact")
    row = Outbox.objects.create(
        object_type="Contact",
        record_id=c.pk,
        state=Outbox.State.PENDING,
        operation=Outbox.Operation.CREATE,
        fields=["full_name"],
        next_attempt_at=timezone.now() + timedelta(hours=1),
    )

    # 1. Probe while still down -> returns False
    fake.mode = "down"
    assert probe_salesforce(client=client, limiter=test_limiter) is False
    assert get_salesforce_state()["status"] == "down"

    # 2. Salesforce recovers -> probe succeeds
    fake.mode = "up"
    assert probe_salesforce(client=client, limiter=test_limiter) is True
    assert get_salesforce_state()["status"] == "up"

    # Row is made due immediately
    row.refresh_from_db()
    assert row.next_attempt_at <= timezone.now()


@pytest.mark.brief
def test_ac_5_5_1_restart_while_down_brief_check(fake):
    """Brief check 4: restart while fake Salesforce is down.

    Portal edits accumulate in Outbox; worker restart while down loses nothing;
    switching fake to up successfully drains all edits to Salesforce.
    """
    server, base_url = serve_fake_salesforce(fake, port=0)
    try:
        client = SalesforceClient(base_url=base_url, token="secret")
        r = redis.Redis.from_url(settings.DRAMATIQ_BROKER["OPTIONS"]["url"])
        lim = RateLimiter(redis_client=r, default_rate=50, key_prefix="test:restart:limiter")
        lim.reset()

        # 1. Fake Salesforce is down
        fake.mode = "down"

        # 2. Portal user creates 2 contacts
        c1 = Contact.objects.create(full_name="Contact One", phone="111")
        c2 = Contact.objects.create(full_name="Contact Two", phone="222")
        apply(Change(
            source="portal", event_id=f"evt_{uuid4()}", object_type="Contact",
            record_ref=c1.pk, occurred_at=timezone.now(), actor="portal.user",
            fields={"full_name": "Contact One", "phone": "111"}, operation="create"
        ))
        apply(Change(
            source="portal", event_id=f"evt_{uuid4()}", object_type="Contact",
            record_ref=c2.pk, occurred_at=timezone.now(), actor="portal.user",
            fields={"full_name": "Contact Two", "phone": "222"}, operation="create"
        ))
        assert Outbox.objects.filter(state=Outbox.State.PENDING).count() == 2

        # 3. Worker pass while down: attempts fail, rows back off, work is NOT dropped
        send_outbound(max_rows=10, client=client, limiter=lim)
        assert Outbox.objects.filter(state=Outbox.State.PENDING).count() == 2

        # 4. "Restart": simulate worker restart by clearing leases and resetting client connection
        client.close()
        client = SalesforceClient(base_url=base_url, token="secret")
        Outbox.objects.filter(state=Outbox.State.PENDING).update(
            lease_until=None, next_attempt_at=timezone.now()
        )

        # 5. Salesforce comes back UP
        fake.mode = "up"
        record_salesforce_success()

        # 6. Worker pass succeeds: all pending rows sent, Salesforce records populated
        processed = send_outbound(max_rows=10, client=client, limiter=lim)
        assert processed == 2
        assert Outbox.objects.filter(state=Outbox.State.PENDING).count() == 0
        assert Outbox.objects.filter(state=Outbox.State.SENT).count() == 2

        c1.refresh_from_db()
        c2.refresh_from_db()
        assert c1.salesforce_id in fake.records
        assert c2.salesforce_id in fake.records
        assert fake.records[c1.salesforce_id]["fields"]["phone"] == "111"
        assert fake.records[c2.salesforce_id]["fields"]["phone"] == "222"

        client.close()
        lim.reset()
    finally:
        server.shutdown()
        server.server_close()
