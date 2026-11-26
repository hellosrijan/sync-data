from django.urls import path

from sync.views import WebhookView

app_name = "sync"

# Planned routes (build plan "Endpoints"; views land in phases 3 and 7):
#   customer/                  customer/<int:pk>/                customer/<int:pk>/history/
#   contact/                   contact/<int:pk>/                 contact/<int:pk>/history/
#   sync/status/
urlpatterns = [
    path("webhooks/salesforce/", WebhookView.as_view(), name="salesforce-webhook"),
]
