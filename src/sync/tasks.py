from datetime import datetime, timedelta
from typing import Optional

import dramatiq
from django.utils import timezone
from dramatiq_crontab import cron

from sync.models import ChangeEvent

DEDUPE_RETENTION_DAYS = 7


@cron("0 2 * * *")  # daily at 02:00
@dramatiq.actor
def purge_expired_change_events(cutoff: Optional[datetime] = None) -> int:
    """Delete ChangeEvent records older than 7 days (design §8, build-plan 3.2.1).

    Accepts an explicit `cutoff` datetime for deterministic testing with an injected clock.
    """
    if cutoff is None:
        cutoff = timezone.now() - timedelta(days=DEDUPE_RETENTION_DAYS)
    deleted_count, _ = ChangeEvent.objects.filter(received_at__lt=cutoff).delete()
    return deleted_count
