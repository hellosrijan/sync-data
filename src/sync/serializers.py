from rest_framework import serializers

from sync.models import Contact, Customer, FieldChange

# camelCase output names follow docs/openapi.yml. Server-owned fields are read-only.
# ponytail: no syncState (needs an outbox lookup per row); add in phase 7 with a prefetch/annotation.


class _SyncFields(serializers.ModelSerializer):
    externalId = serializers.UUIDField(source="external_id", read_only=True)
    salesforceId = serializers.CharField(source="salesforce_id", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    createdBy = serializers.CharField(source="created_by", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    updatedBy = serializers.CharField(source="updated_by", read_only=True)


class CustomerSerializer(_SyncFields):
    class Meta:
        model = Customer
        fields = ["id", "externalId", "salesforceId", "createdAt", "createdBy", "updatedAt", "updatedBy", "name", "status", "owner"]
        read_only_fields = ["id"]


class ContactSerializer(_SyncFields):
    fullName = serializers.CharField(source="full_name", max_length=255)

    class Meta:
        model = Contact
        fields = ["id", "externalId", "salesforceId", "createdAt", "createdBy", "updatedAt", "updatedBy", "fullName", "phone", "email", "city", "owner", "customer"]
        read_only_fields = ["id"]


class FieldChangeSerializer(serializers.ModelSerializer):
    occurredAt = serializers.DateTimeField(source="occurred_at", read_only=True)
    eventId = serializers.CharField(source="event_id", read_only=True)

    class Meta:
        model = FieldChange
        fields = ["id", "field", "value", "actor", "source", "occurredAt", "status", "eventId"]
        read_only_fields = fields


class WebhookEventSerializer(serializers.Serializer):
    eventId = serializers.CharField(max_length=128)
    recordId = serializers.CharField(max_length=18)
    objectType = serializers.ChoiceField(choices=["Customer", "Contact"])
    occurredAt = serializers.DateTimeField()
    changedBy = serializers.CharField(max_length=255)
    changedFields = serializers.DictField(required=True)

    def validate_changedFields(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("changedFields must be a dictionary.")
        return value


class WebhookResultSerializer(serializers.Serializer):
    eventId = serializers.CharField()
    result = serializers.ChoiceField(choices=["applied", "duplicate", "echo", "superseded", "no_change"])
    appliedFields = serializers.ListField(child=serializers.CharField(), required=False, default=list)

