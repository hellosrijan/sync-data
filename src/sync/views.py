from rest_framework import generics, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.response import Response

from sync.models import Contact, Customer, FieldChange
from sync.permissions import IsSalesforceIntegration
from sync.serializers import WebhookEventSerializer, WebhookResultSerializer
from sync.services import Change, apply

ALLOWED_WEBHOOK_FIELDS = {
    "Customer": {"name", "status", "owner"},
    "Contact": {"full_name", "phone", "email", "city", "owner", "customer"},
}


class WebhookView(generics.CreateAPIView):
    """Receive one Salesforce change event over HTTPS POST (design §5, openapi.yml).

    Accepts, validates, and routes the change into apply().
    Always returns HTTP 200 OK on successful processing (including duplicate, echo,
    superseded, and no_change). Returns 401 on missing/bad token, 403 on non-integration
    user, and 400 on malformed payloads.
    """

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

        # Normalize and filter to model fields in scope (ignore extra fields)
        allowed = ALLOWED_WEBHOOK_FIELDS[object_type]
        fields = {}
        for k, v in raw_fields.items():
            mapped_k = "full_name" if k == "fullName" else k
            if mapped_k in allowed:
                fields[mapped_k] = v

        # Determine operation based on local existence
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
            # Distinguish superseded from no_change (openapi.yml description)
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
