from django.urls import path

from sync.views import (
    ContactDetailView,
    ContactHistoryListView,
    ContactListCreateView,
    CustomerDetailView,
    CustomerHistoryListView,
    CustomerListCreateView,
    SyncStatusView,
    WebhookView,
)

app_name = "sync"

urlpatterns = [
    # Inbound webhook (Phase 3)
    path("webhooks/salesforce/", WebhookView.as_view(), name="salesforce-webhook"),
    # Portal API: Customer (Phase 7)
    path("customer/", CustomerListCreateView.as_view(), name="customer-list"),
    path("customer/<int:pk>/", CustomerDetailView.as_view(), name="customer-detail"),
    path("customer/<int:pk>/history/", CustomerHistoryListView.as_view(), name="customer-history"),
    # Portal API: Contact (Phase 7)
    path("contact/", ContactListCreateView.as_view(), name="contact-list"),
    path("contact/<int:pk>/", ContactDetailView.as_view(), name="contact-detail"),
    path("contact/<int:pk>/history/", ContactHistoryListView.as_view(), name="contact-history"),
    # Status (Phase 7)
    path("sync/status/", SyncStatusView.as_view(), name="sync-status"),
]
