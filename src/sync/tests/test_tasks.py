from datetime import timedelta
from django.utils import timezone
import pytest

from sync.models import ChangeEvent
from sync.tasks import DEDUPE_RETENTION_DAYS, purge_expired_change_events

pytestmark = [pytest.mark.django_db]


def test_ac_3_2_1_purge_deletes_records_older_than_cutoff():
    now = timezone.now()
    cutoff = now - timedelta(days=DEDUPE_RETENTION_DAYS)

    # 1. Row older than 7 days (e.g. 8 days old)
    old_event = ChangeEvent.objects.create(event_id="evt_old_8d", source="salesforce")
    ChangeEvent.objects.filter(pk=old_event.pk).update(received_at=now - timedelta(days=8))

    # 2. Row newer than 7 days (e.g. 6 days old)
    recent_event = ChangeEvent.objects.create(event_id="evt_recent_6d", source="salesforce")
    ChangeEvent.objects.filter(pk=recent_event.pk).update(received_at=now - timedelta(days=6))

    # 3. Row from right now
    fresh_event = ChangeEvent.objects.create(event_id="evt_fresh_0d", source="salesforce")

    # Run purge with injected cutoff
    deleted = purge_expired_change_events.fn(cutoff=cutoff)

    assert deleted == 1
    assert not ChangeEvent.objects.filter(event_id="evt_old_8d").exists()
    assert ChangeEvent.objects.filter(event_id="evt_recent_6d").exists()
    assert ChangeEvent.objects.filter(event_id="evt_fresh_0d").exists()


def test_ac_3_2_1_default_cutoff_calculation():
    now = timezone.now()
    old_event = ChangeEvent.objects.create(event_id="evt_default_8d", source="salesforce")
    ChangeEvent.objects.filter(pk=old_event.pk).update(received_at=now - timedelta(days=8))

    recent_event = ChangeEvent.objects.create(event_id="evt_default_6d", source="salesforce")
    ChangeEvent.objects.filter(pk=recent_event.pk).update(received_at=now - timedelta(days=6))

    deleted = purge_expired_change_events.fn()  # default cutoff: timezone.now() - 7 days

    assert deleted == 1
    assert not ChangeEvent.objects.filter(event_id="evt_default_8d").exists()
    assert ChangeEvent.objects.filter(event_id="evt_default_6d").exists()


def test_ac_3_2_1_task_actor_attributes():
    assert hasattr(purge_expired_change_events, "actor_name")
    assert purge_expired_change_events.actor_name == "purge_expired_change_events"
    assert hasattr(purge_expired_change_events, "fn")
