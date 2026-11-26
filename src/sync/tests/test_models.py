import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from sync.models import ChangeEvent, Contact, Customer, FieldChange, Outbox

pytestmark = pytest.mark.django_db


def _ob(record_id=1, object_type="customer", state="pending"):
    return Outbox.objects.create(
        object_type=object_type, record_id=record_id, operation="update",
        fields=["name"], state=state, next_attempt_at=timezone.now(),
    )


def _customer(**kw):
    kw.setdefault("name", "n")
    kw.setdefault("status", "active")
    kw.setdefault("owner", "o")
    return Customer.objects.create(**kw)


def _contact(**kw):
    kw.setdefault("full_name", "f")
    kw.setdefault("phone", "1")
    kw.setdefault("email", "a@b.co")
    kw.setdefault("city", "c")
    kw.setdefault("owner", "o")
    return Contact.objects.create(**kw)


def _constraints(model):
    with connection.cursor() as c:
        return connection.introspection.get_constraints(c, model._meta.db_table)


def _indexed_cols(model):
    return {tuple(v["columns"]) for v in _constraints(model).values() if v["index"]}


# AC-1.3.1
def test_ac_1_3_1_defaults():
    c = _customer()
    assert isinstance(c.external_id, uuid.UUID)
    assert c.salesforce_id is None and c.field_meta == {}


@pytest.mark.parametrize("make", [_customer, _contact])
def test_ac_1_3_1_duplicate_external_id_raises(make):
    ext = uuid.uuid4()
    make(external_id=ext)
    with pytest.raises(IntegrityError), transaction.atomic():
        make(external_id=ext)


def test_ac_1_3_1_null_salesforce_ids_coexist():
    _customer(); _customer()
    _contact(); _contact()
    assert Customer.objects.filter(salesforce_id__isnull=True).count() == 2


def test_ac_1_3_1_same_salesforce_id_raises():
    _customer(salesforce_id="001AAA")
    with pytest.raises(IntegrityError), transaction.atomic():
        _customer(salesforce_id="001AAA")


def test_ac_1_3_1_contact_same_salesforce_id_raises():
    _contact(salesforce_id="003AAA")
    with pytest.raises(IntegrityError), transaction.atomic():
        _contact(salesforce_id="003AAA")


def test_ac_1_3_1_delete_customer_with_contact_protected():
    c = _customer()
    _contact(customer=c)
    with pytest.raises(ProtectedError):
        c.delete()


def test_ac_1_3_1_contact_without_customer_keeps_sf_id():
    ct = _contact(customer_salesforce_id="001ZZZ")
    assert ct.customer is None and ct.customer_salesforce_id == "001ZZZ"


# AC-1.3.2
def test_ac_1_3_2_duplicate_event_id_raises():
    ChangeEvent.objects.create(event_id="e1", source="salesforce")
    with pytest.raises(IntegrityError), transaction.atomic():
        ChangeEvent.objects.create(event_id="e1", source="salesforce")


def test_ac_1_3_2_field_change_and_outbox_defaults():
    fc = FieldChange.objects.create(
        object_type="customer", record_id=1, field="name", value=None, actor="a",
        source="portal", occurred_at=timezone.now(), status="applied",
    )
    assert fc.value is None and fc.event_id == ""
    o = _ob()
    assert o.state == "pending" and o.attempts == 0
    assert o.lease_until is None and o.sent_at is None
    assert o.fields == ["name"]


# AC-1.3.3
def test_ac_1_3_3_second_pending_raises():
    _ob()
    with pytest.raises(IntegrityError), transaction.atomic():
        _ob()


def test_ac_1_3_3_non_pending_plus_one_pending_allowed():
    _ob(state="sent"); _ob(state="sent"); _ob(state="needs_review"); _ob()
    assert Outbox.objects.count() == 4


def test_ac_1_3_3_other_record_or_type_allowed():
    _ob(1); _ob(2); _ob(1, object_type="contact")
    assert Outbox.objects.filter(state="pending").count() == 3


def test_ac_1_3_3_constraint_is_partial_in_db():
    with connection.cursor() as c:
        c.execute("select indexdef from pg_indexes where indexname = 'outbox_one_pending_per_record'")
        row = c.fetchone()
    assert row and "UNIQUE" in row[0] and "pending" in row[0]


def test_ac_1_3_3_indexes_exist():
    assert ("state", "next_attempt_at") in _indexed_cols(Outbox)
    assert ("object_type", "record_id", "occurred_at") in _indexed_cols(FieldChange)
    assert ("received_at",) in _indexed_cols(ChangeEvent)
    cols = _indexed_cols(Contact)
    assert {("customer_id",), ("customer_salesforce_id",), ("email",)} <= cols


# AC-1.3.4
def test_ac_1_3_4_times_are_timezone_aware():
    o = _ob()
    o.refresh_from_db()
    assert timezone.is_aware(o.created_at) and timezone.is_aware(o.next_attempt_at)
    c = _customer()
    assert timezone.is_aware(c.created_at) and timezone.is_aware(c.updated_at)
    assert ChangeEvent.objects.create(event_id="x", source="s").received_at.tzinfo is not None


# AC-1.7.1 / AC-1.7.4
@pytest.mark.parametrize("model", [Customer, Contact])
@pytest.mark.parametrize("name", ["created_by", "updated_by"])
def test_ac_1_7_audit_columns_meta_and_db(model, name):
    from django.db import models as m

    f = model._meta.get_field(name)
    assert isinstance(f, m.CharField) and f.max_length == 255
    assert f.blank is True and f.default == "" and not f.null
    assert not f.unique and not f.db_index
    with connection.cursor() as c:
        cols = {d.name: d for d in connection.introspection.get_table_description(c, model._meta.db_table)}
        cons = connection.introspection.get_constraints(c, model._meta.db_table)
    assert name in cols and cols[name].display_size == 255 and cols[name].type_code == 1043  # varchar(255)
    assert not any(v["unique"] and name in v["columns"] for v in cons.values())


@pytest.mark.parametrize("make", [_customer, _contact])
def test_ac_1_7_created_record_stores_empty_strings(make):
    obj = make()
    obj.refresh_from_db()
    assert (obj.created_by, obj.updated_by) == ("", "")


def test_ac_1_7_two_records_same_audit_value_allowed():
    _customer(created_by="u"); _customer(created_by="u")
    assert Customer.objects.filter(created_by="u").count() == 2


# AC-1.7.3
def test_ac_1_7_3_model_docstrings_cite_design_and_no_help_text():
    from pathlib import Path

    from sync import models as mod

    for n in ("SyncRecord", "Customer", "Contact", "ChangeEvent", "FieldChange", "Outbox", "SyncState"):
        doc = getattr(mod, n).__doc__
        assert doc and doc.strip() and "§" in doc, n
    assert "help_text" not in Path(mod.__file__).read_text()
