import uuid
from datetime import timedelta
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sync.models import ChangeEvent, Contact, Customer, FieldChange, Outbox
from sync.permissions import INTEGRATION_GROUP
from sync.serializers import WebhookEventSerializer

T0 = timezone.now()
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def integration_user():
    user = User.objects.create_user(username="sf-integration-user")
    group, _ = Group.objects.get_or_create(name=INTEGRATION_GROUP)
    user.groups.add(group)
    token, _ = Token.objects.get_or_create(user=user)
    return user, token.key


@pytest.fixture
def portal_user():
    user = User.objects.create_user(username="portal-finance-user")
    token, _ = Token.objects.get_or_create(user=user)
    return user, token.key


@pytest.fixture
def sf_client(integration_user):
    _, token_key = integration_user
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token_key}")
    return client


def make_payload(*, event_id=None, record_id="003A000001", object_type="Contact",
                 occurred_at=None, changed_by="sales.user@client.example", changed_fields=None, **extra):
    at_str = occurred_at.isoformat() if hasattr(occurred_at, "isoformat") else (occurred_at or T1.isoformat())
    return {
        "eventId": event_id or f"evt_{uuid.uuid4()}",
        "recordId": record_id,
        "objectType": object_type,
        "occurredAt": at_str,
        "changedBy": changed_by,
        "changedFields": changed_fields if changed_fields is not None else {"phone": "+919876543210"},
        **extra,
    }


# ==============================================================================
# AC-3.1.1: WebhookEventSerializer validation
# ==============================================================================

def test_ac_3_1_1_valid_payload():
    payload = make_payload()
    ser = WebhookEventSerializer(data=payload)
    assert ser.is_valid(), ser.errors


def test_ac_3_1_1_missing_required_fields_rejected():
    for req in ["eventId", "recordId", "objectType", "occurredAt", "changedBy", "changedFields"]:
        payload = make_payload()
        del payload[req]
        ser = WebhookEventSerializer(data=payload)
        assert not ser.is_valid()
        assert req in ser.errors


def test_ac_3_1_1_invalid_object_type_rejected():
    payload = make_payload(object_type="Opportunity")
    ser = WebhookEventSerializer(data=payload)
    assert not ser.is_valid()
    assert "objectType" in ser.errors


def test_ac_3_1_1_invalid_occurred_at_rejected():
    payload = make_payload(occurred_at="not-a-date")
    payload["occurredAt"] = "invalid-date"
    ser = WebhookEventSerializer(data=payload)
    assert not ser.is_valid()
    assert "occurredAt" in ser.errors


def test_ac_3_1_1_changed_fields_must_be_dict():
    payload = make_payload()
    payload["changedFields"] = ["not", "a", "dict"]
    ser = WebhookEventSerializer(data=payload)
    assert not ser.is_valid()
    assert "changedFields" in ser.errors


def test_ac_3_1_1_extra_fields_ignored():
    payload = make_payload(extra_unscoped_field="ignored_value")
    ser = WebhookEventSerializer(data=payload)
    assert ser.is_valid()


# ==============================================================================
# AC-3.1.3: Authentication & Permissions
# ==============================================================================

def test_ac_3_1_3_missing_token_returns_401():
    client = APIClient()
    url = reverse("sync:salesforce-webhook")
    response = client.post(url, make_payload(), format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert ChangeEvent.objects.count() == 0
    assert Contact.objects.count() == 0


def test_ac_3_1_3_invalid_token_returns_401():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token invalid_key_1234567890")
    url = reverse("sync:salesforce-webhook")
    response = client.post(url, make_payload(), format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert ChangeEvent.objects.count() == 0


def test_ac_3_1_3_portal_user_token_returns_403(portal_user):
    _, token_key = portal_user
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token_key}")
    url = reverse("sync:salesforce-webhook")
    response = client.post(url, make_payload(), format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert ChangeEvent.objects.count() == 0
    assert Contact.objects.count() == 0


def test_ac_3_1_3_malformed_payload_returns_400_and_stores_nothing(sf_client):
    url = reverse("sync:salesforce-webhook")
    bad_payload = make_payload()
    del bad_payload["eventId"]
    response = sf_client.post(url, bad_payload, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert ChangeEvent.objects.count() == 0
    assert Contact.objects.count() == 0
    assert FieldChange.objects.count() == 0


# ==============================================================================
# AC-3.1.2 & AC-3.1.5: WebhookView execution, outcomes, and brief sample
# ==============================================================================

@pytest.mark.brief
def test_ac_3_1_5_brief_sample_webhook_end_to_end(sf_client):
    url = reverse("sync:salesforce-webhook")
    payload = {
        "eventId": "evt_1001",
        "recordId": "0031A00000Abc",
        "objectType": "Contact",
        "occurredAt": "2026-11-02T11:30:04.000Z",
        "changedBy": "sales.user@client.example",
        "changedFields": {
            "phone": "+919876543210"
        }
    }
    # First delivery: creates record, applies phone
    response = sf_client.post(url, payload, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "eventId": "evt_1001",
        "result": "applied",
        "appliedFields": ["phone"],
    }
    contact = Contact.objects.get(salesforce_id="0031A00000Abc")
    assert contact.phone == "+919876543210"
    assert FieldChange.objects.filter(event_id="evt_1001", status="applied", field="phone").count() == 1

    # Second delivery (duplicate): HTTP 200 with result="duplicate"
    dup_resp = sf_client.post(url, payload, format="json")
    assert dup_resp.status_code == status.HTTP_200_OK
    assert dup_resp.data == {
        "eventId": "evt_1001",
        "result": "duplicate",
        "appliedFields": [],
    }
    assert Contact.objects.filter(salesforce_id="0031A00000Abc").count() == 1


def test_ac_3_1_2_customer_webhook_create_and_update(sf_client):
    url = reverse("sync:salesforce-webhook")
    payload1 = make_payload(
        record_id="001Cust01",
        object_type="Customer",
        changed_fields={"name": "Acme Corp", "status": "Active", "owner": "Sales Rep"}
    )
    r1 = sf_client.post(url, payload1, format="json")
    assert r1.status_code == status.HTTP_200_OK
    assert r1.data["result"] == "applied"
    assert sorted(r1.data["appliedFields"]) == ["name", "owner", "status"]
    cust = Customer.objects.get(salesforce_id="001Cust01")
    assert cust.name == "Acme Corp"

    # Update customer name
    payload2 = make_payload(
        record_id="001Cust01",
        object_type="Customer",
        occurred_at=T2,
        changed_fields={"name": "Acme Global"}
    )
    r2 = sf_client.post(url, payload2, format="json")
    assert r2.status_code == status.HTTP_200_OK
    assert r2.data["result"] == "applied"
    assert r2.data["appliedFields"] == ["name"]
    cust.refresh_from_db()
    assert cust.name == "Acme Global"


def test_ac_3_1_2_echo_webhook_returns_200_echo(sf_client, settings):
    settings.SALESFORCE_INTEGRATION_USER = "integration.bot@client.example"
    url = reverse("sync:salesforce-webhook")
    # First create contact
    sf_client.post(url, make_payload(record_id="003Echo01", changed_fields={"phone": "111"}), format="json")

    # Echo: changedBy is integration user
    echo_payload = make_payload(
        record_id="003Echo01",
        occurred_at=T2,
        changed_by="integration.bot@client.example",
        changed_fields={"phone": "222"}
    )
    resp = sf_client.post(url, echo_payload, format="json")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["result"] == "echo"
    assert resp.data["appliedFields"] == []
    contact = Contact.objects.get(salesforce_id="003Echo01")
    assert contact.phone == "111"  # Not applied


def test_ac_3_1_2_superseded_webhook_returns_200_superseded(sf_client):
    url = reverse("sync:salesforce-webhook")
    # Newer update at T2
    sf_client.post(url, make_payload(record_id="003Sup01", occurred_at=T2, changed_fields={"city": "Pune"}), format="json")
    assert Contact.objects.get(salesforce_id="003Sup01").city == "Pune"

    # Older update at T1 arriving late
    older_payload = make_payload(record_id="003Sup01", occurred_at=T1, changed_fields={"city": "Mumbai"})
    resp = sf_client.post(url, older_payload, format="json")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["result"] == "superseded"
    assert resp.data["appliedFields"] == []
    assert Contact.objects.get(salesforce_id="003Sup01").city == "Pune"


def test_ac_3_1_2_no_change_webhook_returns_200_no_change(sf_client):
    url = reverse("sync:salesforce-webhook")
    payload1 = make_payload(record_id="003NC01", occurred_at=T1, changed_fields={"city": "Delhi"})
    sf_client.post(url, payload1, format="json")

    # Resend exact same value with different eventId at T2
    payload2 = make_payload(record_id="003NC01", occurred_at=T2, changed_fields={"city": "Delhi"})
    resp = sf_client.post(url, payload2, format="json")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["result"] == "no_change"
    assert resp.data["appliedFields"] == []


# ==============================================================================
# AC-3.1.4: Contact customer linkage
# ==============================================================================

def test_ac_3_1_4_contact_links_to_existing_customer(sf_client):
    url = reverse("sync:salesforce-webhook")
    # 1. Create Customer
    sf_client.post(url, make_payload(
        record_id="001ParentA", object_type="Customer", changed_fields={"name": "Parent Corp"}
    ), format="json")
    customer = Customer.objects.get(salesforce_id="001ParentA")

    # 2. Contact arrives referencing existing Customer
    sf_client.post(url, make_payload(
        record_id="003ChildA", object_type="Contact", changed_fields={"phone": "555", "customer": "001ParentA"}
    ), format="json")
    contact = Contact.objects.get(salesforce_id="003ChildA")
    assert contact.customer == customer
    assert contact.customer_salesforce_id == "001ParentA"


def test_ac_3_1_4_contact_arrives_before_customer_and_links_on_customer_arrival(sf_client):
    url = reverse("sync:salesforce-webhook")
    # 1. Contact arrives referencing non-existent Customer
    sf_client.post(url, make_payload(
        record_id="003ChildB", object_type="Contact", changed_fields={"phone": "777", "customer": "001ParentB"}
    ), format="json")
    contact = Contact.objects.get(salesforce_id="003ChildB")
    assert contact.customer is None
    assert contact.customer_salesforce_id == "001ParentB"

    # 2. Customer arrives later
    sf_client.post(url, make_payload(
        record_id="001ParentB", object_type="Customer", changed_fields={"name": "Parent B Inc"}
    ), format="json")
    customer = Customer.objects.get(salesforce_id="001ParentB")

    # 3. Contact is now automatically linked
    contact.refresh_from_db()
    assert contact.customer == customer
    assert contact.customer_salesforce_id == "001ParentB"




