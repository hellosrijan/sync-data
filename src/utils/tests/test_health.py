from unittest import mock

import pytest
import redis
from django.db import OperationalError
from rest_framework.test import APIClient


def get_health():
    return APIClient().get("/api/v1/health/")


@pytest.mark.django_db
def test_ok_when_postgres_and_redis_answer():
    # Real Postgres (test DB) and real Redis from docker compose.
    r = get_health()
    assert r.status_code == 200
    assert r.json() == {"postgres": "ok", "redis": "ok"}


@pytest.mark.django_db
def test_503_when_postgres_fails():
    with mock.patch("utils.views.connection.cursor", side_effect=OperationalError("down")):
        r = get_health()
    assert r.status_code == 503
    assert r.json() == {"postgres": "error: OperationalError", "redis": "ok"}


@pytest.mark.django_db
def test_503_when_redis_fails():
    client = mock.Mock(**{"ping.side_effect": redis.ConnectionError("down")})
    with mock.patch("utils.views.redis.Redis.from_url", return_value=client):
        r = get_health()
    assert r.status_code == 503
    assert r.json() == {"postgres": "ok", "redis": "error: ConnectionError"}


def test_503_when_both_fail():
    client = mock.Mock(**{"ping.side_effect": redis.TimeoutError()})
    with (
        mock.patch("utils.views.connection.cursor", side_effect=OperationalError()),
        mock.patch("utils.views.redis.Redis.from_url", return_value=client),
    ):
        r = get_health()
    assert r.status_code == 503
    assert r.json() == {"postgres": "error: OperationalError", "redis": "error: TimeoutError"}
