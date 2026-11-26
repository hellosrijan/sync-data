from rest_framework.permissions import IsAuthenticated

INTEGRATION_GROUP = "salesforce-integration"


class IsSalesforceIntegration(IsAuthenticated):
    """Authenticated user in the salesforce-integration group (design Q11)."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.groups.filter(name=INTEGRATION_GROUP).exists()
