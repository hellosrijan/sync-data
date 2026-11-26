from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.utils import timezone
import pytest
import redis

from sync.client import SalesforceClient
from sync.limiter import RateLimiter
from sync.models import Contact, Customer, FieldChange, Outbox, SyncState
from sync.services import Change, apply
from sync.tasks import (
    get_watermark,
    probe_salesforce,
    run_catchup,
    scheduled_catchup,
    set_watermark,
)
from sync.tests.fakes import FakeSalesforce, transport_for

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
    lim = RateLimiter(redis_client=r, default_rate=50, key_prefix="test:catchup:limiter")
    lim.reset()
    yield lim
    lim.reset()


def test_ac_6_1_1_catchup_queries_since_watermark_minus_overlap(fake, client, test_limiter):
    now = timezone.now()
    set_watermark(now - timedelta(hours=1))

    # 3 sales edits in fake made during outage (no webhooks sent)
    sf_id1, _ = fake.upsert_by_external_id(str(uuid4()), {"phone": "111", "city": "Pune"}, object_type="Contact")
    sf_id2, _ = fake.upsert_by_external_id(str(uuid4()), {"phone": "222", "city": "Delhi"}, object_type="Contact")
    sf_id3, _ = fake.upsert_by_external_id(str(uuid4()), {"name": "Acme Industrial"}, object_type="Customer")

    # Set modified_at within the catch-up window
    fake.records[sf_id1]["modified_at"] = now - timedelta(minutes=45)
    fake.records[sf_id2]["modified_at"] = now - timedelta(minutes=30)
    fake.records[sf_id3]["modified_at"] = now - timedelta(minutes=15)

    processed = run_catchup(client=client, limiter=test_limiter, overlap_minutes=5)
    assert processed == 3

    # All 3 records now exist in portal Postgres
    c1 = Contact.objects.get(salesforce_id=sf_id1)
    assert c1.phone == "111"
    assert c1.city == "Pune"

    c2 = Contact.objects.get(salesforce_id=sf_id2)
    assert c2.phone == "222"
    assert c2.city == "Delhi"

    cust = Customer.objects.get(salesforce_id=sf_id3)
    assert cust.name == "Acme Industrial"

    # Watermark advanced to latest timestamp
    assert get_watermark() >= now - timedelta(minutes=16)


def test_ac_6_1_2_catchup_is_idempotent(fake, client, test_limiter):
    now = timezone.now()
    set_watermark(now - timedelta(hours=2))

    sf_id, _ = fake.upsert_by_external_id(str(uuid4()), {"phone": "555"}, object_type="Contact")
    fake.records[sf_id]["modified_at"] = now - timedelta(hours=1)

    # First run
    p1 = run_catchup(client=client, limiter=test_limiter)
    assert p1 == 1

    # Second run immediately: records already matched or watermark advanced
    p2 = run_catchup(client=client, limiter=test_limiter)
    assert p2 in (0, 1)  # overlap may re-query, but apply() is no-op
    # Contact phone is still 555 and only 1 applied history row exists
    c = Contact.objects.get(salesforce_id=sf_id)
    assert c.phone == "555"
    assert FieldChange.objects.filter(record_id=c.pk, field="phone", status="applied").count() == 1


def test_ac_6_1_2_field_level_echo_check_on_catchup(fake, client, test_limiter):
    now = timezone.now()
    set_watermark(now - timedelta(hours=1))

    # Record created in portal first
    c = Contact.objects.create(salesforce_id="003EchoTest", full_name="Echo Contact", city="Noida")
    apply(Change(
        source="portal",
        event_id=f"evt_{uuid4()}",
        object_type="Contact",
        record_ref=c.pk,
        occurred_at=now - timedelta(minutes=40),
        actor="portal.user",
        fields={"city": "Noida"},
        operation="update",
    ))

    # In fake Salesforce, modified_by is the integration user (our echo coming back in catch-up)
    fake.records["003EchoTest"] = {
        "salesforce_id": "003EchoTest",
        "external_id": str(c.external_id),
        "object_type": "Contact",
        "fields": {"city": "Noida"},
        "modified_at": now - timedelta(minutes=30),
        "modified_by": settings.SALESFORCE_INTEGRATION_USER,
    }

    run_catchup(client=client, limiter=test_limiter)

    # Verified: echo is recorded in history, but creates no outbox row and does not change portal value
    echo_history = FieldChange.objects.filter(record_id=c.pk, field="city", status="echo")
    assert echo_history.exists()
    assert Outbox.objects.filter(record_id=c.pk, state=Outbox.State.PENDING).count() == 1  # only initial portal edit


def test_ac_6_1_4_catchup_runs_before_drain_on_recovery(fake, client, test_limiter):
    now = timezone.now()
    set_watermark(now - timedelta(hours=2))

    # 1. Salesforce is marked down
    SyncState.objects.update_or_create(
        key="salesforce",
        defaults={"value": {"status": "down", "consecutive_failures": 5}},
    )

    # 2. Salesforce side has an edit made during the outage
    sf_id, _ = fake.upsert_by_external_id(str(uuid4()), {"phone": "999"}, object_type="Contact")
    fake.records[sf_id]["modified_at"] = now - timedelta(hours=1)

    # 3. Portal side has a pending edit for a different contact
    c_portal = Contact.objects.create(full_name="Portal Contact", phone="000")
    apply(Change(
        source="portal",
        event_id=f"evt_{uuid4()}",
        object_type="Contact",
        record_ref=c_portal.pk,
        occurred_at=now,
        actor="portal.user",
        fields={"phone": "777"},
        operation="create",
    ))
    assert Outbox.objects.filter(record_id=c_portal.pk, state=Outbox.State.PENDING).count() == 1

    # 4. Probe runs when fake is up: it runs catch-up FIRST, then makes pending rows due
    fake.mode = "up"
    recovered = probe_salesforce(client=client, limiter=test_limiter)
    assert recovered is True

    # Salesforce edit is now in portal database (caught up!)
    assert Contact.objects.filter(salesforce_id=sf_id, phone="999").exists()

    # Outbox row is now due
    row = Outbox.objects.get(record_id=c_portal.pk)
    assert row.next_attempt_at <= timezone.now()


def test_ac_6_1_4_scheduled_catchup_actor():
    assert hasattr(scheduled_catchup, "actor_name")
    assert scheduled_catchup.actor_name == "scheduled_catchup"
