from datetime import timedelta
from uuid import uuid4

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sync.models import Contact, Customer, FieldChange, Outbox

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def auth_client():
    user = User.objects.create_user(username="portal_finance_user")
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


@pytest.fixture
def admin_client():
    user = User.objects.create_superuser(username="admin_user", password="password")
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


# ==============================================================================
# Customer API tests (7.1)
# ==============================================================================

def test_ac_7_1_customer_list_create_and_detail(auth_client):
    list_url = reverse("sync:customer-list")

    # 1. Create customer
    payload = {"name": "Test Customer Co", "status": "Active", "owner": "Owner A"}
    r1 = auth_client.post(list_url, payload, format="json")
    assert r1.status_code == status.HTTP_201_CREATED
    data = r1.json()
    assert data["name"] == "Test Customer Co"
    assert data["syncState"] == "pending"
    assert data["createdBy"] == "portal_finance_user"
    cust_id = data["id"]

    # Outbox row created automatically for portal create
    assert Outbox.objects.filter(object_type="Customer", record_id=cust_id, state=Outbox.State.PENDING).exists()

    # 2. Detail view
    detail_url = reverse("sync:customer-detail", kwargs={"pk": cust_id})
    r2 = auth_client.get(detail_url)
    assert r2.status_code == status.HTTP_200_OK
    assert r2.json()["name"] == "Test Customer Co"

    # 3. Patch customer
    r3 = auth_client.patch(detail_url, {"name": "Updated Customer Co"}, format="json")
    assert r3.status_code == status.HTTP_200_OK
    assert r3.json()["name"] == "Updated Customer Co"
    assert r3.json()["updatedBy"] == "portal_finance_user"


# ==============================================================================
# Contact API tests (7.1)
# ==============================================================================

def test_ac_7_1_contact_list_create_and_patch(auth_client):
    cust = Customer.objects.create(name="Parent Corp")
    list_url = reverse("sync:contact-list")

    # 1. Create contact
    payload = {
        "fullName": "John Doe",
        "phone": "+919876543210",
        "email": "john.doe@example.com",
        "city": "Bengaluru",
        "owner": "Owner B",
        "customer": cust.pk,
    }
    r1 = auth_client.post(list_url, payload, format="json")
    assert r1.status_code == status.HTTP_201_CREATED
    contact_id = r1.json()["id"]
    assert r1.json()["fullName"] == "John Doe"
    assert r1.json()["syncState"] == "pending"

    # Outbox row created
    assert Outbox.objects.filter(object_type="Contact", record_id=contact_id, state=Outbox.State.PENDING).exists()

    # 2. Patch contact
    detail_url = reverse("sync:contact-detail", kwargs={"pk": contact_id})
    r2 = auth_client.patch(detail_url, {"city": "Hyderabad"}, format="json")
    assert r2.status_code == status.HTTP_200_OK
    assert r2.json()["city"] == "Hyderabad"

    # History row recorded
    assert FieldChange.objects.filter(object_type="Contact", record_id=contact_id, field="city", value="Hyderabad").exists()


# ==============================================================================
# History endpoints (7.2)
# ==============================================================================

def test_ac_7_2_history_endpoints(auth_client):
    cust = Customer.objects.create(name="History Cust")
    FieldChange.objects.create(
        object_type="Customer", record_id=cust.pk, field="name", value="Old Name",
        actor="user1", source="portal", occurred_at=timezone.now() - timedelta(minutes=5),
        status="superseded"
    )
    FieldChange.objects.create(
        object_type="Customer", record_id=cust.pk, field="name", value="History Cust",
        actor="user2", source="portal", occurred_at=timezone.now(),
        status="applied"
    )

    url = reverse("sync:customer-history", kwargs={"pk": cust.pk})
    r = auth_client.get(url)
    assert r.status_code == status.HTTP_200_OK
    results = r.json()["results"]
    assert len(results) == 2
    assert results[0]["value"] == "History Cust"
    assert results[1]["value"] == "Old Name"


# ==============================================================================
# Sync status endpoint (7.3)
# ==============================================================================

def test_ac_7_3_sync_status_endpoint(admin_client, auth_client):
    url = reverse("sync:sync-status")

    # Regular user gets 403 Forbidden
    r_forbidden = auth_client.get(url)
    assert r_forbidden.status_code == status.HTTP_403_FORBIDDEN

    # Admin user gets 200 OK with full status schema
    r_ok = admin_client.get(url)
    assert r_ok.status_code == status.HTTP_200_OK
    data = r_ok.json()
    assert data["salesforce"] in ("up", "down")
    assert "pendingOutboxCount" in data
    assert "limiterRate" in data
    assert "portalEditsPerSecond" in data
