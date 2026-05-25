"""
State-transition service for ActivityRecord.

This is the single chokepoint for all review_status changes. Every code
path that wants to flag, approve, or lock a record MUST go through
`transition()`. That guarantees the ReviewEvent row is written.

Why a service module (not signals, not on the model):
  - Signals fire silently and out-of-band; auditors should not have to
    trace receivers across the codebase to find where audit rows are
    written.
  - A `save()` override would couple presentation (the model) to workflow
    (the audit log). Different actors (system normalizer, analyst via API)
    need different rules, which a save() override can't cleanly express.
  - A free function with explicit arguments makes it obvious: nothing can
    change review_status without an actor and (where required) a note.
"""

from datetime import datetime
from typing import Optional

from django.db import transaction
from django.utils import timezone

from .models import ActivityRecord, ReviewEvent, ReviewStatus


class InvalidTransitionError(ValueError):
    """Raised when a requested transition isn't allowed by the lifecycle."""


# Allowed (from_status → to_status) transitions per MODEL.md §11.1.
# Anything not listed is an InvalidTransitionError.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ReviewStatus.INGESTED: {ReviewStatus.FLAGGED, ReviewStatus.ANALYST_REVIEWED},
    ReviewStatus.FLAGGED: {ReviewStatus.INGESTED, ReviewStatus.ANALYST_REVIEWED},
    ReviewStatus.ANALYST_REVIEWED: {ReviewStatus.APPROVED, ReviewStatus.FLAGGED},
    ReviewStatus.APPROVED: {ReviewStatus.LOCKED, ReviewStatus.FLAGGED},
    ReviewStatus.LOCKED: set(),  # locked is terminal; unlocking is a separate privileged op
}


@transaction.atomic
def transition(
    *,
    record: ActivityRecord,
    to_status: str,
    actor_id: str,
    actor_display_name: str = "",
    event_type: str,
    note: str = "",
    field_changes: Optional[list] = None,
) -> ReviewEvent:
    """
    Change a record's review_status AND write the audit row in one transaction.

    Why @transaction.atomic: if the audit write fails, the status change
    must roll back. Otherwise the table-of-truth (review_status) would
    diverge from the audit log.
    """

    from_status = record.review_status

    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise InvalidTransitionError(
            f"Cannot move record {record.id} from {from_status} to {to_status}"
        )

    now = timezone.now()
    record.review_status = to_status

    # Side-effects on the record per state. These are denormalized copies
    # of the latest relevant event — they live on the record for fast
    # filtering ("approved by whom?") without joining review_events.
    if to_status == ReviewStatus.ANALYST_REVIEWED:
        record.reviewed_by = actor_id
        record.reviewed_at = now
    elif to_status == ReviewStatus.APPROVED:
        record.approved_by = actor_id
        record.approved_at = now
    elif to_status == ReviewStatus.LOCKED:
        record.locked_by = actor_id
        record.locked_at = now
    elif to_status == ReviewStatus.FLAGGED and note:
        record.flagged_reason = note

    record.save()

    event = ReviewEvent.objects.create(
        organization=record.organization,
        activity_record=record,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        actor_display_name=actor_display_name,
        note=note,
        field_changes=field_changes or [],
    )
    return event


def record_ingestion_event(
    *,
    record: ActivityRecord,
    actor_id: str = "system",
    note: str = "",
) -> ReviewEvent:
    """
    Write the initial 'ingested' event for a freshly normalized record.

    Not a transition (the record is created at status='ingested'), so we
    write the event directly. This is the only ReviewEvent that doesn't
    correspond to a state CHANGE.
    """
    return ReviewEvent.objects.create(
        organization=record.organization,
        activity_record=record,
        event_type="ingested",
        from_status="",
        to_status=ReviewStatus.INGESTED,
        actor_id=actor_id,
        note=note,
    )


def auto_flag(
    *,
    record: ActivityRecord,
    reason: str,
    actor_id: str = "system",
) -> ReviewEvent:
    """
    System auto-flag — used by the normalizer when it detects something an
    analyst should look at (estimated read, suspicious unit conversion,
    quantity outside 3σ of site history, etc.).
    """
    return transition(
        record=record,
        to_status=ReviewStatus.FLAGGED,
        actor_id=actor_id,
        event_type="auto_flagged",
        note=reason,
    )
