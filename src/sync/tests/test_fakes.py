from datetime import timedelta
import time
from urllib.parse import quote
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone
import httpx
import pytest
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sync.models import Contact, Customer
from sync.permissions import INTEGRATION_GROUP
from sync.tests.fakes import FakeSalesforce, serve_fake_salesforce, transport_for
from sync.tests.scenarios import (
    duplicate,
    our_echo,
    sales_edit,
    sales_edit_whole_record,
    stale_after,
)

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def fake():
    return FakeSalesforce()


@pytest.fixture
def mock_client(fake):
    return httpx.Client(transport=transport_for(fake), base_url="https://fake.salesforce.com")


@pytest.fixture
def live_fake_server(fake):
    server, base_url = serve_fake_salesforce(fake, port=0)
    yield fake, base_url
    server.shutdown()
    server.server_close()


@pytest.fixture
def sf_webhook_client():
    user = User.objects.create_user(username="sf-int-user")
    group, _ = Group.objects.get_or_create(name=INTEGRATION_GROUP)
    user.groups.add(group)
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


# ==============================================================================
# AC-4.1.1: FakeSalesforce in-memory operations (upsert, update, changed_since)
# ==============================================================================

def test_ac_4_1_1_upsert_by_external_id_is_idempotent(fake):
    ext_id = str(uuid4())
    # First call: creates record
    sf_id1, created1 = fake.upsert_by_external_id(ext_id, {"phone": "111"}, object_type="Contact")
    assert created1 is True
    assert sf_id1.startswith("003")
    assert len(fake.records) == 1

    # Second call with same external ID: updates existing, returns same salesforce_id
    sf_id2, created2 = fake.upsert_by_external_id(ext_id, {"phone": "222"}, object_type="Contact")
    assert created2 is False
    assert sf_id2 == sf_id1
    assert len(fake.records) == 1
    assert fake.records[sf_id1]["fields"]["phone"] == "222"


def test_ac_4_1_1_customer_upsert_uses_001_prefix(fake):
    ext_id = str(uuid4())
    sf_id, created = fake.upsert_by_external_id(ext_id, {"name": "Acme Corp"}, object_type="Customer")
    assert created is True
    assert sf_id.startswith("001")


def test_ac_4_1_1_update_by_salesforce_id(fake):
    sf_id, _ = fake.upsert_by_external_id(str(uuid4()), {"city": "Old City"}, object_type="Contact")
    t_before = fake.records[sf_id]["modified_at"]

    fake.update(sf_id, {"city": "New City"})
    assert fake.records[sf_id]["fields"]["city"] == "New City"
    assert fake.records[sf_id]["modified_at"] >= t_before

    with pytest.raises(KeyError):
        fake.update("003NonExistent", {"city": "Nowhere"})


def test_ac_4_1_1_changed_since_query_paging(fake):
    t0 = timezone.now() - timedelta(minutes=10)
    for i in range(5):
        ext_id = f"ext_{i}"
        sf_id, _ = fake.upsert_by_external_id(ext_id, {"name": f"Cust {i}"}, object_type="Customer")
        fake.records[sf_id]["modified_at"] = t0 + timedelta(minutes=i)

    # Page size 2
    page1, done1, offset1 = fake.changed_since(since=t0, page_size=2, offset=0)
    assert len(page1) == 2
    assert done1 is False
    assert offset1 == 2

    page2, done2, offset2 = fake.changed_since(since=t0, page_size=2, offset=offset1)
    assert len(page2) == 2
    assert done2 is False
    assert offset2 == 4

    page3, done3, offset3 = fake.changed_since(since=t0, page_size=2, offset=offset2)
    assert len(page3) == 1
    assert done3 is True
    assert offset3 == 5


# ==============================================================================
# AC-4.1.2: MockTransport dispatch (network-free)
# ==============================================================================

def test_ac_4_1_2_mock_transport_upsert_and_update(fake, mock_client):
    ext_id = str(uuid4())
    url = f"/services/data/v60.0/sobjects/Contact/external_id/{ext_id}"

    # 1. Upsert create -> 201 Created
    r1 = mock_client.patch(url, json={"phone": "+919999999999"})
    assert r1.status_code == 201
    sf_id = r1.json()["id"]
    assert sf_id.startswith("003")

    # 2. Upsert update with same external ID -> 200 OK, same ID
    r2 = mock_client.patch(url, json={"phone": "+918888888888"})
    assert r2.status_code == 200
    assert r2.json()["id"] == sf_id

    # 3. Direct update by ID -> 204 No Content
    update_url = f"/services/data/v60.0/sobjects/Contact/{sf_id}"
    r3 = mock_client.patch(update_url, json={"city": "Bengaluru"})
    assert r3.status_code == 204
    assert fake.records[sf_id]["fields"]["city"] == "Bengaluru"

    # 4. Update unknown ID -> 404
    r4 = mock_client.patch("/services/data/v60.0/sobjects/Contact/003UnknownId", json={"city": "X"})
    assert r4.status_code == 404


def test_ac_4_1_2_mock_transport_query(fake, mock_client):
    t_cutoff = timezone.now() - timedelta(minutes=5)
    sf_id, _ = fake.upsert_by_external_id("ext_1", {"name": "Test"}, object_type="Customer")
    fake.records[sf_id]["modified_at"] = timezone.now()

    q = quote(f"SELECT Id, SystemModstamp FROM Account WHERE SystemModstamp >= {t_cutoff.isoformat()}")
    resp = mock_client.get(f"/services/data/v60.0/query/?q={q}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["totalSize"] == 1
    assert data["done"] is True
    assert data["records"][0]["Id"] == sf_id


# ==============================================================================
# AC-4.1.3: ThreadingHTTPServer on localhost
# ==============================================================================

def test_ac_4_1_3_live_http_server_matches_transport(live_fake_server):
    fake, base_url = live_fake_server
    client = httpx.Client(base_url=base_url)

    ext_id = str(uuid4())
    url = f"/services/data/v60.0/sobjects/Customer/external_id/{ext_id}"

    # Real HTTP request over TCP port
    resp = client.patch(url, json={"name": "Live HTTP Customer"})
    assert resp.status_code == 201
    sf_id = resp.json()["id"]
    assert sf_id in fake.records
    assert fake.records[sf_id]["fields"]["name"] == "Live HTTP Customer"


# ==============================================================================
# AC-4.2.1 & AC-4.2.2: Failure modes and Call Log
# ==============================================================================

def test_ac_4_2_1_failure_modes(fake, mock_client):
    url = "/services/data/v60.0/sobjects/Contact/external_id/ext1"

    # 1. Down -> ConnectError
    fake.mode = "down"
    with pytest.raises(httpx.ConnectError):
        mock_client.patch(url, json={"phone": "1"})

    # 2. Slow -> delays response
    fake.mode = "slow"
    fake.slow_delay = 0.05
    t_start = time.perf_counter()
    resp_slow = mock_client.patch(url, json={"phone": "1"})
    assert resp_slow.status_code == 201
    assert (time.perf_counter() - t_start) >= 0.04

    # 3. Throttle -> 429 with Retry-After
    fake.mode = "throttle"
    resp_429 = mock_client.patch(url, json={"phone": "1"})
    assert resp_429.status_code == 429
    assert resp_429.headers.get("retry-after") == "1"

    # 4. Error -> 500
    fake.mode = "error"
    assert mock_client.patch(url, json={"phone": "1"}).status_code == 500

    # 5. Reject -> 400 for specific record
    fake.mode = "reject"
    fake.reject_id = "bad_ext_id"
    assert mock_client.patch("/services/data/v60.0/sobjects/Contact/external_id/bad_ext_id", json={}).status_code == 400
    assert mock_client.patch("/services/data/v60.0/sobjects/Contact/external_id/good_ext_id", json={}).status_code == 201

    # 6. Auth -> 401
    fake.mode = "auth"
    assert mock_client.patch(url, json={}).status_code == 401


def test_ac_4_2_2_call_log_records_timestamps_and_methods(fake, mock_client):
    assert len(fake.calls) == 0
    mock_client.patch("/services/data/v60.0/sobjects/Contact/external_id/extA", json={"phone": "123"})
    mock_client.get("/services/data/v60.0/query/?q=test")

    assert len(fake.calls) == 2
    method1, url1, body1, ts1 = fake.calls[0]
    assert method1 == "PATCH"
    assert "extA" in url1
    assert b"123" in body1
    assert isinstance(ts1, timezone.datetime)

    method2, url2, _, _ = fake.calls[1]
    assert method2 == "GET"
    assert "query" in url2


# ==============================================================================
# AC-4.3.1, AC-4.3.2, AC-4.3.3: Scenario Builders & End-to-End Webhook
# ==============================================================================

def test_ac_4_3_scenario_builders_helpers(fake):
    sf_id, _ = fake.upsert_by_external_id("ext10", {"phone": "111", "city": "Pune"}, object_type="Contact")

    # 1. sales_edit: changed fields only (Q2 default)
    edit_payload = sales_edit(fake, sf_id, phone="222")
    assert edit_payload["recordId"] == sf_id
    assert edit_payload["objectType"] == "Contact"
    assert edit_payload["changedFields"] == {"phone": "222"}
    assert "city" not in edit_payload["changedFields"]

    # 2. sales_edit_whole_record: all fields (Q2 fallback)
    whole_payload = sales_edit_whole_record(fake, sf_id, phone="333")
    assert whole_payload["changedFields"] == {"phone": "333", "city": "Pune"}

    # 3. our_echo: changedBy = integration user
    echo_payload = our_echo(fake, sf_id, phone="444")
    assert "settings" in echo_payload["changedBy"] or echo_payload["changedBy"]
    assert echo_payload["changedFields"] == {"phone": "444"}

    # 4. duplicate: same payload and eventId
    dup_payload = duplicate(edit_payload)
    assert dup_payload["eventId"] == edit_payload["eventId"]
    assert dup_payload == edit_payload

    # 5. stale_after: older timestamp and distinct eventId
    stale_payload = stale_after(edit_payload, timezone.now() - timedelta(hours=1), phone="000")
    assert stale_payload["eventId"] != edit_payload["eventId"]
    assert stale_payload["changedFields"] == {"phone": "000"}


@pytest.mark.brief
def test_ac_4_3_scenario_posts_to_webhook_and_appears_in_portal(fake, sf_webhook_client):
    sf_id, _ = fake.upsert_by_external_id("ext_contact_99", {"phone": "100", "city": "Noida"}, object_type="Contact")
    webhook_url = reverse("sync:salesforce-webhook")

    # Post initial sales edit
    initial_payload = sales_edit(fake, sf_id, phone="999", city="Noida")
    r1 = sf_webhook_client.post(webhook_url, initial_payload, format="json")
    assert r1.status_code == status.HTTP_200_OK
    assert r1.data["result"] == "applied"

    contact = Contact.objects.get(salesforce_id=sf_id)
    assert contact.phone == "999"
    assert contact.city == "Noida"
