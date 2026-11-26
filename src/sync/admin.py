from django.contrib import admin, messages
from django.utils import timezone

from sync.models import ChangeEvent, Contact, Customer, FieldChange, Outbox, SyncState


class ReadOnlyAdmin(admin.ModelAdmin):
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "owner", "salesforce_id", "created_by", "updated_by", "updated_at")
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    search_fields = ("external_id", "salesforce_id")
    ordering = ("id",)
    show_full_result_count = False


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "email", "customer", "salesforce_id", "created_by", "updated_by", "updated_at")
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    search_fields = ("email", "salesforce_id", "customer_salesforce_id", "external_id")
    list_select_related = ("customer",)
    raw_id_fields = ("customer",)
    ordering = ("id",)
    show_full_result_count = False


@admin.register(ChangeEvent)
class ChangeEventAdmin(ReadOnlyAdmin):
    list_display = ("id", "event_id", "source", "received_at")
    ordering = ("-id",)


@admin.register(FieldChange)
class FieldChangeAdmin(ReadOnlyAdmin):
    list_display = ("id", "object_type", "record_id", "field", "value", "status", "occurred_at")
    ordering = ("-id",)


@admin.register(Outbox)
class OutboxAdmin(ReadOnlyAdmin):
    list_display = ("id", "object_type", "record_id", "operation", "state", "attempts", "next_attempt_at")
    list_filter = ("state",)
    ordering = ("-id",)
    actions = ["requeue"]

    @admin.action(description="Requeue selected needs_review rows")
    def requeue(self, request, queryset):
        done = skipped = 0
        for row in queryset.filter(state=Outbox.State.NEEDS_REVIEW):
            if Outbox.objects.filter(
                object_type=row.object_type, record_id=row.record_id, state=Outbox.State.PENDING
            ).exists():
                skipped += 1
                continue
            row.state, row.next_attempt_at, row.attempts, row.lease_until = (
                Outbox.State.PENDING, timezone.now(), 0, None,
            )
            row.save(update_fields=["state", "next_attempt_at", "attempts", "lease_until"])
            done += 1
        self.message_user(request, f"Requeued {done} row(s).")
        if skipped:
            self.message_user(
                request, f"Skipped {skipped} row(s): the record already has a pending row.", messages.WARNING
            )


@admin.register(SyncState)
class SyncStateAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_at")
    ordering = ("key",)
    show_full_result_count = False
