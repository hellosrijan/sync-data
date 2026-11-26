from uuid import uuid4

import pytest

from sync.client import ClientOutcome, SalesforceClient
from sync.tests.fakes import FakeSalesforce, transport_for

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def fake():
    return FakeSalesforce()


@pytest.fixture
def client(fake):
    c = SalesforceClient(
        base_url="https://fake.salesforce.com",
        token="test_token_123",
        transport=transport_for(fake),
    )
    yield c
    c.close()


def test_ac_5_1_1_upsert_success(client, fake):
    ext_id = str(uuid4())
    outcome = client.upsert("Contact", ext_id, {"phone": "+919999999999"})
    assert outcome.outcome == "ok"
    assert outcome.salesforce_id is not None
    assert outcome.salesforce_id.startswith("003")
    assert fake.records[outcome.salesforce_id]["fields"]["phone"] == "+919999999999"


def test_ac_5_1_1_update_success(client, fake):
    sf_id, _ = fake.upsert_by_external_id(str(uuid4()), {"city": "Pune"}, object_type="Contact")
    outcome = client.update("Contact", sf_id, {"city": "Mumbai"})
    assert outcome.outcome == "ok"
    assert fake.records[sf_id]["fields"]["city"] == "Mumbai"


def test_ac_5_1_2_outcome_unreachable_on_down(client, fake):
    fake.mode = "down"
    outcome = client.upsert("Contact", str(uuid4()), {"phone": "123"})
    assert outcome.outcome == "unreachable"
    assert "fake: down" in outcome.error_message


def test_ac_5_1_2_outcome_throttled_429(client, fake):
    fake.mode = "throttle"
    outcome = client.update("Contact", "003Any", {"phone": "123"})
    assert outcome.outcome == "throttled"
    assert outcome.retry_after == 1


def test_ac_5_1_2_outcome_rejected_400(client, fake):
    fake.mode = "reject"
    fake.reject_id = "bad_ext_id"
    outcome = client.upsert("Contact", "bad_ext_id", {"phone": "123"})
    assert outcome.outcome == "rejected"
    assert "Bad request" in outcome.error_message


def test_ac_5_1_2_outcome_auth_error_401(client, fake):
    fake.mode = "auth"
    outcome = client.upsert("Customer", str(uuid4()), {"name": "Test"})
    assert outcome.outcome == "auth_error"
    assert "INVALID_SESSION_ID" in outcome.error_message


def test_ac_5_1_2_outcome_500_mapped_to_unreachable(client, fake):
    fake.mode = "error"
    outcome = client.update("Contact", "003Any", {"city": "Delhi"})
    assert outcome.outcome == "unreachable"
    assert "HTTP 500" in outcome.error_message
