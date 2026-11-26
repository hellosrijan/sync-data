from django.urls import path

from utils.views import HealthView

app_name = "utils"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
]
