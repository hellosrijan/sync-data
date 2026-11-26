import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import path
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from sync.permissions import INTEGRATION_GROUP, IsSalesforceIntegration

pytestmark = pytest.mark.django_db
User = get_user_model()
ROOT = Path(settings.BASE_DIR).parent


class Protected(APIView):  # throwaway view: default auth + permission classes
    def get(self, request):
        return Response({"ok": True})


urlpatterns = [path("t/protected/", Protected.as_view())]


# AC-1.2.1
def test_ac_1_2_1_token_rows_can_be_created():
    u = User.objects.create_user("u1")
    assert Token.objects.create(user=u).key


# AC-1.2.2
def test_ac_1_2_2_drf_defaults():
    r = settings.REST_FRAMEWORK
    assert r["DEFAULT_AUTHENTICATION_CLASSES"] == ["rest_framework.authentication.TokenAuthentication"]
    assert r["DEFAULT_PERMISSION_CLASSES"] == ["rest_framework.permissions.IsAuthenticated"]


@pytest.mark.urls("sync.tests.test_auth")
def test_ac_1_2_2_no_token_is_401_not_403():
    assert APIClient().get("/t/protected/").status_code == 401


@pytest.mark.urls("sync.tests.test_auth")
def test_ac_1_2_2_bad_token_is_401():
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Token nope")
    assert c.get("/t/protected/").status_code == 401


@pytest.mark.urls("sync.tests.test_auth")
def test_ac_1_2_2_good_token_is_200():
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Token " + Token.objects.create(user=User.objects.create_user("g")).key)
    assert c.get("/t/protected/").status_code == 200


def test_ac_1_2_2_health_needs_no_token():
    from utils.views import HealthView

    assert HealthView.permission_classes == [AllowAny]
    # call the view directly so the test survives the Slice B URL move
    r = HealthView.as_view()(APIRequestFactory().get("/"))
    assert r.status_code in (200, 503)


# AC-1.2.3
def _call(user):
    req = APIRequestFactory().get("/")
    req.user = user
    return IsSalesforceIntegration().has_permission(req, None)


def test_ac_1_2_3_permission_allows_group_member():
    u = User.objects.create_user("sf")
    u.groups.add(Group.objects.create(name="salesforce-integration"))
    assert INTEGRATION_GROUP == "salesforce-integration"
    assert _call(u) is True


def test_ac_1_2_3_permission_denies_portal_user():
    assert not _call(User.objects.create_user("portal"))


def test_ac_1_2_3_permission_denies_anonymous():
    from django.contrib.auth.models import AnonymousUser

    assert not _call(AnonymousUser())


def test_ac_1_2_3_command_creates_one_user_group_token(capsys):
    call_command("create_integration_user")
    first = capsys.readouterr().out.strip()
    users = User.objects.filter(groups__name="salesforce-integration")
    assert users.count() == 1
    assert Token.objects.filter(user=users[0]).count() == 1
    assert first == Token.objects.get(user=users[0]).key


def test_ac_1_2_3_command_rerun_is_idempotent(capsys):
    call_command("create_integration_user")
    call_command("create_integration_user")
    assert User.objects.filter(groups__name="salesforce-integration").count() == 1
    assert Group.objects.filter(name="salesforce-integration").count() == 1
    assert Token.objects.count() == 1


# AC-1.2.4
def test_ac_1_2_4_httpx_importable_and_locked():
    import httpx  # noqa: F401

    assert "httpx" in (ROOT / "pyproject.toml").read_text()
    assert 'name = "httpx"' in (ROOT / "uv.lock").read_text()


def test_ac_1_2_4_rate_limit_is_int_with_default_90():
    assert isinstance(settings.SALESFORCE_RATE_LIMIT, int)
    # .env may set it, so check the declared default in the settings source
    src = (ROOT / "src/config/settings.py").read_text()
    assert re.search(r'env\.int\(\s*"SALESFORCE_RATE_LIMIT",\s*default=90\s*\)', src)


def _keys(p):
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)=", p.read_text(), re.M))


def test_ac_1_2_4_env_and_example_have_same_key_names():
    assert _keys(ROOT / ".env") == _keys(ROOT / ".env.example")
    assert {"SALESFORCE_BASE_URL", "SALESFORCE_TOKEN", "SALESFORCE_RATE_LIMIT"} <= _keys(ROOT / ".env.example")
