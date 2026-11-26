import re

import pytest
from django.contrib import admin
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from sync.admin import ContactAdmin, CustomerAdmin
from sync.models import ChangeEvent, Contact, Customer, FieldChange, Outbox, SyncState

pytestmark = pytest.mark.django_db


def _customer(**kw):
    return Customer.objects.create(name="n", status="active", owner="o", **kw)


def _contact(customer=None, **kw):
    return Contact.objects.create(
        full_name="f", phone="1", email="a@b.co", city="c", owner="o", customer=customer, **kw
    )


def _outbox(record_id, state, **kw):
    return Outbox.objects.create(
        object_type="customer", record_id=record_id, operation="update", fields=["name"],
        state=state, next_attempt_at=timezone.now(), **kw,
    )


def _rows():
    """One row per model; returns {model: obj}."""
    cust = _customer()
    return {
        Customer: cust,
        Contact: _contact(cust),
        ChangeEvent: ChangeEvent.objects.create(event_id="e1", source="salesforce"),
        FieldChange: FieldChange.objects.create(
            object_type="customer", record_id=cust.pk, field="name", value="x", actor="a",
            source="portal", occurred_at=timezone.now(), status="applied", event_id="e1",
        ),
        Outbox: _outbox(cust.pk, "needs_review"),
        SyncState: SyncState.objects.create(key="k", value={}),
    }


ALL = [Customer, Contact, ChangeEvent, FieldChange, Outbox, SyncState]
READ_ONLY = [ChangeEvent, FieldChange, Outbox]


def _url(model, name, *args):
    m = model._meta
    return reverse(f"admin:{m.app_label}_{m.model_name}_{name}", args=args)


@pytest.mark.parametrize("model", ALL)
def test_ac_1_4_3_every_model_registered_changelist_search_change_200(admin_client, model):
    obj = _rows()[model]
    assert model in admin.site._registry
    assert admin_client.get(_url(model, "changelist")).status_code == 200
    assert admin_client.get(_url(model, "changelist"), {"q": "zzz"}).status_code == 200
    assert admin_client.get(_url(model, "change", obj.pk)).status_code == 200


@pytest.mark.parametrize("model", [Customer, Contact])
def test_ac_1_4_1_add_page_200(admin_client, model):
    assert admin_client.get(_url(model, "add")).status_code == 200


@pytest.mark.parametrize("model", READ_ONLY)
def test_ac_1_4_2_add_page_403_for_read_only(admin_client, model):
    assert admin_client.get(_url(model, "add")).status_code == 403


def test_ac_1_4_1_search_fields_are_indexed_columns_only():
    assert CustomerAdmin.search_fields == ("external_id", "salesforce_id")
    assert ContactAdmin.search_fields == ("email", "salesforce_id", "customer_salesforce_id", "external_id")
    for f in CustomerAdmin.search_fields + ContactAdmin.search_fields:
        assert f not in ("name", "city", "full_name")


def test_ac_1_4_1_options_set():
    for a in (CustomerAdmin, ContactAdmin):
        assert a.list_display and a.ordering and a.show_full_result_count is False
    assert ContactAdmin.list_select_related == ("customer",)
    assert ContactAdmin.raw_id_fields == ("customer",)


def test_ac_1_4_1_contact_change_page_uses_raw_id_widget(admin_client):
    c = _contact(_customer())
    html = admin_client.get(_url(Contact, "change", c.pk)).content.decode()
    assert not re.search(r"<select[^>]*name=\"customer\"", html)
    assert re.search(r"<input[^>]*name=\"customer\"", html)


def test_ac_1_4_1_contact_changelist_query_count_is_fixed(admin_client):
    def count():
        with CaptureQueriesContext(connection) as ctx:
            assert admin_client.get(_url(Contact, "changelist")).status_code == 200
        return len(ctx)

    for _ in range(3):
        _contact(_customer())
    n3 = count()
    for _ in range(7):
        _contact(_customer())
    assert Contact.objects.count() == 10
    assert count() == n3  # no N+1 on customer


@pytest.mark.parametrize("model", READ_ONLY)
def test_ac_1_4_2_perms_false_for_superuser(admin_client, rf, model):
    from django.contrib.auth import get_user_model

    req = rf.get("/")
    req.user = get_user_model().objects.get(is_superuser=True)
    ma = admin.site._registry[model]
    obj = _rows()[model]
    assert not ma.has_add_permission(req)
    assert not ma.has_change_permission(req, obj)
    assert not ma.has_delete_permission(req, obj)
    assert ma.list_display and ma.ordering and ma.show_full_result_count is False


@pytest.mark.parametrize("model", READ_ONLY)
def test_ac_1_4_2_post_change_and_delete_leave_row_untouched(admin_client, model):
    obj = _rows()[model]
    before = list(model.objects.filter(pk=obj.pk).values())
    r1 = admin_client.post(_url(model, "change", obj.pk), {"source": "hacked", "state": "sent"})
    r2 = admin_client.post(_url(model, "delete", obj.pk), {"post": "yes"})
    assert r1.status_code == 403 and r2.status_code == 403
    assert list(model.objects.filter(pk=obj.pk).values()) == before


def test_ac_1_4_3_requeue_action(admin_client):
    nr, sent, pend, nr2, blocker = (
        _outbox(1, "needs_review", attempts=5, lease_until=timezone.now(), last_error="boom"),
        _outbox(2, "sent"),
        _outbox(3, "pending", attempts=2),
        _outbox(4, "needs_review", attempts=7),
        _outbox(4, "pending"),
    )
    sent_before = list(Outbox.objects.filter(pk__in=[sent.pk, pend.pk, blocker.pk]).values())
    nr2_before = list(Outbox.objects.filter(pk=nr2.pk).values())
    start = timezone.now()
    r = admin_client.post(
        _url(Outbox, "changelist"),
        {"action": "requeue", "_selected_action": [o.pk for o in (nr, sent, pend, nr2)]},
        follow=True,
    )
    assert r.status_code == 200
    nr.refresh_from_db()
    assert (nr.state, nr.attempts, nr.lease_until) == ("pending", 0, None)
    assert nr.next_attempt_at >= start
    assert list(Outbox.objects.filter(pk__in=[sent.pk, pend.pk, blocker.pk]).values()) == sent_before
    assert list(Outbox.objects.filter(pk=nr2.pk).values()) == nr2_before  # skipped
    msgs = [(m.level_tag, str(m)) for m in r.context["messages"]]
    assert any(t == "warning" and "Skipped 1" in m for t, m in msgs)
    assert any("Requeued 1" in m for _, m in msgs)


# AC-1.7.2 / AC-1.7.4
@pytest.mark.parametrize("model", [Customer, Contact])
def test_ac_1_7_admin_lists_and_shows_audit_columns_read_only(admin_client, model):
    ma = admin.site._registry[model]
    assert {"created_by", "updated_by"} <= set(ma.list_display)
    assert {"created_by", "updated_by"} <= set(ma.readonly_fields)
    assert "created_by" not in ma.search_fields and "updated_by" not in ma.search_fields
    obj = _rows()[model]
    assert admin_client.get(_url(model, "changelist")).status_code == 200
    r = admin_client.get(_url(model, "change", obj.pk))
    assert r.status_code == 200
    html = r.content.decode()
    assert not re.search(r"<(input|select|textarea)[^>]*name=\"(created|updated)_by\"", html)
    assert "field-created_by" in html and "field-updated_by" in html


def test_ac_1_7_search_fields_unchanged():
    assert CustomerAdmin.search_fields == ("external_id", "salesforce_id")
    assert ContactAdmin.search_fields == ("email", "salesforce_id", "customer_salesforce_id", "external_id")
