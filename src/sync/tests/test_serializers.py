import inspect
import re
from pathlib import Path

import pytest

from sync import serializers as mod
from sync.serializers import ContactSerializer, CustomerSerializer, FieldChangeSerializer

SPEC = (Path(__file__).resolve().parents[3] / "docs" / "openapi.yml").read_text().splitlines()


def _block(name):
    """Lines of components.schemas.<name> (no yaml dep installed: indent-based parse)."""
    start = SPEC.index(f"    {name}:")
    out = []
    for line in SPEC[start + 1:]:
        if line.strip() and not line.startswith("     "):
            break
        out.append(line)
    return out


def _props(name):
    block, props, in_props = _block(name), set(), False
    for line in block:
        if line == "      properties:":
            in_props = True
        elif in_props and re.match(r"^        (\w+):", line):
            props.add(re.match(r"^        (\w+):", line).group(1))
        elif re.match(r"^      \w", line):
            in_props = False
        m = re.search(r'\$ref: "#/components/schemas/(\w+)"', line)
        if m:
            props |= _props(m.group(1))
    return props


def test_spec_parser_sanity():
    assert {"id", "name", "externalId", "createdBy"} <= _props("Customer")


# syncState is not in the contract or the serializers yet: it needs an outbox lookup per row (phase 7).
@pytest.mark.parametrize(
    "ser,schema",
    [(CustomerSerializer, "Customer"), (ContactSerializer, "Contact"), (FieldChangeSerializer, "FieldChange")],
)
def test_ac_1_6_3_field_set_equals_openapi(ser, schema):
    assert set(ser().fields) == _props(schema)


@pytest.mark.parametrize("ser", [CustomerSerializer, ContactSerializer])
def test_ac_1_6_3_sync_state_absent_on_purpose(ser):
    assert "syncState" not in ser().fields


@pytest.mark.parametrize(
    "ser,ro",
    [(CustomerSerializer, ["id", "externalId", "salesforceId", "updatedAt"]),
     (ContactSerializer, ["id", "externalId", "salesforceId", "updatedAt"]),
     (FieldChangeSerializer, list(FieldChangeSerializer().fields))],
)
def test_ac_1_6_3_server_owned_fields_read_only(ser, ro):
    s = ser()
    for n in ro:
        assert s.fields[n].read_only, n


def test_ac_1_6_3_explicit_fields_no_all():
    for ser in (CustomerSerializer, ContactSerializer, FieldChangeSerializer):
        assert ser.Meta.fields != "__all__"


@pytest.mark.django_db
def test_ac_1_6_3_output_camel_case_and_customer_is_int():
    from sync.models import Contact, Customer

    cust = Customer.objects.create(name="n", status="s", owner="o")
    c = Contact.objects.create(full_name="f", phone="1", email="a@b.co", city="c", owner="o", customer=cust)
    data = ContactSerializer(c).data
    assert data["fullName"] == "f" and data["customer"] == cust.pk and isinstance(data["customer"], int)
    assert {"externalId", "salesforceId", "updatedAt"} <= set(data)
    assert not any("_" in k for k in data)
    assert not any("_" in k for k in CustomerSerializer(cust).data)


def test_ac_1_6_3_serializers_never_call_save():
    assert ".save(" not in inspect.getsource(mod)


# AC-1.7.2 / AC-1.7.4
AUDIT = ["createdAt", "createdBy", "updatedAt", "updatedBy"]


@pytest.mark.parametrize("ser", [CustomerSerializer, ContactSerializer])
def test_ac_1_7_audit_fields_present_and_read_only(ser):
    s = ser()
    for n in AUDIT:
        assert n in s.fields and s.fields[n].read_only, n
    assert "syncState" not in s.fields


@pytest.mark.django_db
def test_ac_1_7_output_has_empty_audit_strings():
    from sync.models import Contact, Customer

    cust = Customer.objects.create(name="n", status="s", owner="o")
    c = Contact.objects.create(full_name="f", phone="1", email="a@b.co", city="c", owner="o")
    for obj, ser in ((cust, CustomerSerializer), (c, ContactSerializer)):
        d = ser(obj).data
        assert set(AUDIT) <= set(d)
        assert d["createdBy"] == "" and d["updatedBy"] == ""


@pytest.mark.django_db
def test_ac_1_7_write_payload_cannot_set_audit_fields():
    from sync.models import Customer

    cust = Customer.objects.create(name="n", status="s", owner="o")
    s = CustomerSerializer(cust, data={"name": "m", "status": "s", "owner": "o",
                                       "createdBy": "x", "updatedBy": "y"}, partial=True)
    assert s.is_valid(), s.errors
    assert not {"createdBy", "updatedBy", "created_by", "updated_by"} & set(s.validated_data)
    cust.refresh_from_db()
    assert (cust.created_by, cust.updated_by) == ("", "")


# Gap: the serializers are the trust boundary for portal writes (drf.md: "Validate everything
# from outside"), but no existing test exercises rejection of bad input. Only valid payloads
# were tested above.
def test_ac_1_6_3_customer_missing_required_field_rejected():
    s = CustomerSerializer(data={"status": "Active", "owner": "o"})
    assert not s.is_valid()
    assert "name" in s.errors


def test_ac_1_6_3_contact_invalid_email_rejected():
    s = ContactSerializer(data={"fullName": "f", "phone": "1", "email": "not-an-email", "city": "c", "owner": "o"})
    assert not s.is_valid()
    assert "email" in s.errors


@pytest.mark.django_db
def test_ac_1_6_3_contact_nonexistent_customer_rejected():
    s = ContactSerializer(data={"fullName": "f", "phone": "1", "email": "a@b.co", "city": "c", "owner": "o", "customer": 999999})
    assert not s.is_valid()
    assert "customer" in s.errors
