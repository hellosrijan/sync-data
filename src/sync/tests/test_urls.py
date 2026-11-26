from pathlib import Path

import pytest
from django.urls import NoReverseMatch, get_resolver, resolve, reverse
from rest_framework.test import APIClient

SRC = Path(__file__).resolve().parents[2]


@pytest.mark.django_db
def test_ac_1_6_4_health_200_with_keys():
    r = APIClient().get("/api/v1/health/")
    assert r.status_code == 200
    assert {"postgres", "redis"} <= set(r.json())


def test_ac_1_6_4_old_health_route_404():
    assert APIClient().get("/health/").status_code == 404


def test_ac_1_6_2_reverse_health():
    assert reverse("utils:health") == "/api/v1/health/"


def test_ac_1_6_2_app_names():
    import sync.urls as s
    import utils.urls as u

    assert u.app_name == "utils" and s.app_name == "sync"
    assert [p.name for p in u.urlpatterns] == ["health"]
    assert "salesforce-webhook" in [p.name for p in s.urlpatterns]


def test_ac_1_6_4_every_named_route_reverses():
    import sync.urls as s
    import utils.urls as u

    for mod in (u, s):
        for p in mod.urlpatterns:
            if p.name:
                kwargs = {"pk": 1} if "<int:pk>" in str(p.pattern) else {}
                assert reverse(f"{mod.app_name}:{p.name}", kwargs=kwargs)
    # and the root resolver mounts utils under api/v1/
    assert resolve("/api/v1/health/").view_name == "utils:health"
    with pytest.raises(NoReverseMatch):
        reverse("health")


def test_ac_1_6_1_api_v1_defined_once_in_root_and_nowhere_else():
    root = SRC / "config" / "urls.py"
    assert root.read_text().count("api/v1") == 1
    others = [
        p for p in SRC.rglob("*")
        if p.suffix in {".py", ".html", ".txt", ".toml", ".yml"} and p != root
        and "tests" not in p.parts and "__pycache__" not in p.parts
        and "api/v1" in p.read_text(errors="ignore")
    ]
    assert others == []


def test_ac_1_6_1_root_includes_utils_then_sync():
    text = (SRC / "config" / "urls.py").read_text()
    assert text.index('"utils.urls"') < text.index('"sync.urls"')
    assert '"admin/"' in text
    assert get_resolver().resolve("/admin/login/")
