import redis
from django.conf import settings
from django.db import connection
from rest_framework import generics, status
from rest_framework.permissions import AllowAny

from utils.serializers import HealthSerializer


class HealthView(generics.RetrieveAPIView):
    """Liveness + dependency check: Postgres and Redis must both answer."""

    serializer_class = HealthSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_object(self):
        checks = {}
        try:
            with connection.cursor() as c:
                c.execute("SELECT 1")
            checks["postgres"] = "ok"
        except Exception as e:
            checks["postgres"] = f"error: {e.__class__.__name__}"
        try:
            redis.Redis.from_url(settings.DRAMATIQ_BROKER["OPTIONS"]["url"]).ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {e.__class__.__name__}"
        return checks

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        if any(v != "ok" for v in response.data.values()):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return response
