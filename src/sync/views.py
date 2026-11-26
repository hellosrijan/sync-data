from datetime import datetime
from uuid import uuid4

from django.db import models
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from sync.limiter import get_rate_limiter
from sync.models import Contact, Customer, FieldChange, Outbox, SyncState
from sync.pagination import HistoryCursorPagination, RecordCursorPagination
from sync.permissions import IsSalesforceIntegration
from sync.serializers import (
    ContactSerializer,
    CustomerSerializer,
    FieldChangeSerializer,
    SyncStatusSerializer,
    WebhookEventSerializer,
    WebhookResultSerializer,
)
from sync.services import Change, apply
from sync.tasks import get_salesforce_state, get_watermark

ALLOWED_WEBHOOK_FIELDS = {
    "Customer": {"name", "status", "owner"},
    "Contact": {"full_name", "phone", "email", "city", "owner", "customer"},
}


class WebhookView(generics.CreateAPIView):
    """Receive one Salesforce change event over HTTPS POST (design §5, openapi.yml)."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsSalesforceIntegration]
    serializer_class = WebhookEventSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        event_id = data["eventId"]
        record_id = data["recordId"]
        object_type = data["objectType"]
        occurred_at = data["occurredAt"]
        changed_by = data["changedBy"]
        raw_fields = data["changedFields"]

        allowed = ALLOWED_WEBHOOK_FIELDS[object_type]
        fields = {}
        for k, v in raw_fields.items():
            mapped_k = "full_name" if k == "fullName" else k
            if mapped_k in allowed:
                fields[mapped_k] = v

        model = Customer if object_type == "Customer" else Contact
        operation = "update" if model.objects.filter(salesforce_id=record_id).exists() else "create"

        change = Change(
            source="salesforce",
            event_id=event_id,
            object_type=object_type,
            record_ref=record_id,
            occurred_at=occurred_at,
            actor=changed_by,
            fields=fields,
            operation=operation,
        )
        result = apply(change)

        outcome = result.outcome
        if outcome == "no_change":
            if FieldChange.objects.filter(event_id=event_id, status=FieldChange.Status.SUPERSEDED).exists():
                outcome = "superseded"

        response_data = {
            "eventId": event_id,
            "result": outcome,
            "appliedFields": result.applied_fields,
        }
        result_serializer = WebhookResultSerializer(data=response_data)
        result_serializer.is_valid(raise_exception=True)
        return Response(result_serializer.data, status=status.HTTP_200_OK)


def _annotate_sync_state(queryset, object_type: str):
    """Annotate records with their syncState from Outbox to avoid N+1 queries."""
    pending_ids = set(
        Outbox.objects.filter(object_type=object_type, state=Outbox.State.PENDING).values_list("record_id", flat=True)
    )
    review_ids = set(
        Outbox.objects.filter(object_type=object_type, state=Outbox.State.NEEDS_REVIEW).values_list("record_id", flat=True)
    )

    records = list(queryset)
    for r in records:
        if r.pk in pending_ids:
            r._sync_state = "pending"
        elif r.pk in review_ids:
            r._sync_state = "needs_review"
        else:
            r._sync_state = "synced"
    return records


# ==============================================================================
# Phase 7: Customer Endpoints
# ==============================================================================

class CustomerListCreateView(generics.ListCreateAPIView):
    serializer_class = CustomerSerializer
    pagination_class = RecordCursorPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Customer.objects.all().order_by("id")
        sf_id = self.request.query_params.get("salesforceId")
        if sf_id:
            qs = qs.filter(salesforce_id=sf_id)
        owner = self.request.query_params.get("owner")
        if owner:
            qs = qs.filter(owner=owner)
        return qs

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        # 1. Create instance locally first
        customer = Customer.objects.create(**data)
        # 2. Route write through apply() to create ChangeEvent, FieldChange, and Outbox (create)
        change = Change(
            source="portal",
            event_id=f"evt_portal_{uuid4()}",
            object_type="Customer",
            record_ref=customer.pk,
            occurred_at=timezone.now(),
            actor=self.request.user.username or "portal-user",
            fields=data,
            operation="create",
        )
        apply(change)
        customer.refresh_from_db()
        serializer.instance = customer


class CustomerDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]
    queryset = Customer.objects.all()
    http_method_names = ["get", "patch", "head", "options"]

    def perform_update(self, serializer):
        customer = serializer.instance
        data = dict(serializer.validated_data)
        change = Change(
            source="portal",
            event_id=f"evt_portal_{uuid4()}",
            object_type="Customer",
            record_ref=customer.pk,
            occurred_at=timezone.now(),
            actor=self.request.user.username or "portal-user",
            fields=data,
            operation="update",
        )
        apply(change)
        customer.refresh_from_db()
        serializer.instance = customer


class CustomerHistoryListView(generics.ListAPIView):
    serializer_class = FieldChangeSerializer
    pagination_class = HistoryCursorPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        customer = get_object_or_404(Customer, pk=self.kwargs["pk"])
        qs = FieldChange.objects.filter(object_type="Customer", record_id=customer.pk).order_by("-occurred_at")
        field = self.request.query_params.get("field")
        if field:
            qs = qs.filter(field=field)
        return qs


# ==============================================================================
# Phase 7: Contact Endpoints
# ==============================================================================

class ContactListCreateView(generics.ListCreateAPIView):
    serializer_class = ContactSerializer
    pagination_class = RecordCursorPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Contact.objects.select_related("customer").order_by("id")
        sf_id = self.request.query_params.get("salesforceId")
        if sf_id:
            qs = qs.filter(salesforce_id=sf_id)
        owner = self.request.query_params.get("owner")
        if owner:
            qs = qs.filter(owner=owner)
        return qs

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        customer_inst = data.get("customer")
        cust_sf_id = customer_inst.salesforce_id if customer_inst and customer_inst.salesforce_id else ""
        contact = Contact.objects.create(customer_salesforce_id=cust_sf_id, **data)
        change_fields = {}
        for k, v in data.items():
            if k == "customer" and v:
                change_fields["customer"] = v.pk
            else:
                change_fields[k] = v

        change = Change(
            source="portal",
            event_id=f"evt_portal_{uuid4()}",
            object_type="Contact",
            record_ref=contact.pk,
            occurred_at=timezone.now(),
            actor=self.request.user.username or "portal-user",
            fields=change_fields,
            operation="create",
        )
        apply(change)
        contact.refresh_from_db()
        serializer.instance = contact


class ContactDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]
    queryset = Contact.objects.select_related("customer").all()
    http_method_names = ["get", "patch", "head", "options"]

    def perform_update(self, serializer):
        contact = serializer.instance
        data = dict(serializer.validated_data)
        change_fields = {}
        for k, v in data.items():
            if k == "customer" and v:
                change_fields["customer"] = v.pk
            else:
                change_fields[k] = v

        change = Change(
            source="portal",
            event_id=f"evt_portal_{uuid4()}",
            object_type="Contact",
            record_ref=contact.pk,
            occurred_at=timezone.now(),
            actor=self.request.user.username or "portal-user",
            fields=change_fields,
            operation="update",
        )
        apply(change)
        contact.refresh_from_db()
        serializer.instance = contact


class ContactHistoryListView(generics.ListAPIView):
    serializer_class = FieldChangeSerializer
    pagination_class = HistoryCursorPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        contact = get_object_or_404(Contact, pk=self.kwargs["pk"])
        qs = FieldChange.objects.filter(object_type="Contact", record_id=contact.pk).order_by("-occurred_at")
        field = self.request.query_params.get("field")
        if field:
            qs = qs.filter(field=field)
        return qs


# ==============================================================================
# Phase 7: Sync Status Endpoint
# ==============================================================================

class SyncStatusView(generics.RetrieveAPIView):
    serializer_class = SyncStatusSerializer
    permission_classes = [IsAdminUser]

    def get_object(self):
        sf_state = get_salesforce_state()
        pending_count = Outbox.objects.filter(state=Outbox.State.PENDING).count()
        oldest_pending = Outbox.objects.filter(state=Outbox.State.PENDING).order_by("created_at").first()
        oldest_age = (timezone.now() - oldest_pending.created_at).total_seconds() if oldest_pending else None
        needs_review_count = Outbox.objects.filter(state=Outbox.State.NEEDS_REVIEW).count()

        # Edits in last minute
        one_min_ago = timezone.now() - timezone.timedelta(minutes=1)
        edits_last_min = FieldChange.objects.filter(source="portal", occurred_at__gte=one_min_ago).count()
        portal_rate = round(edits_last_min / 60.0, 2)

        limiter = get_rate_limiter()
        current_rate = limiter.get_current_rate()
        watermark = get_watermark()

        return {
            "salesforce": sf_state.get("status", "up"),
            "pendingOutboxCount": pending_count,
            "oldestPendingAgeSeconds": oldest_age,
            "needsReviewCount": needs_review_count,
            "portalEditsPerSecond": portal_rate,
            "limiterRate": current_rate,
            "lastCatchupAt": watermark,
        }
