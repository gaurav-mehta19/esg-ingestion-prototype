"""
Activity record API — list, detail, and review-lifecycle transitions.

Why custom @action methods for flag/approve/lock (not separate ViewSets):
  The standard REST CRUD verbs (POST/PUT/DELETE) don't cleanly express
  state transitions. POST /flag/ is more readable and explicit than
  PATCH with a status field — and a PATCH could change other fields,
  which the lifecycle rules forbid for some transitions.

How the audit row gets written:
  Every state-changing action calls services.transition() (or its wrappers).
  That function is the ONLY place that mutates review_status. ReviewEvent
  rows are written in the same database transaction as the status change.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import ActivityRecord, ReviewStatus
from .serializers import (
    ActivityRecordDetailSerializer,
    ActivityRecordListSerializer,
)
from .services import InvalidTransitionError, transition


class ActivityRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only CRUD plus action endpoints for lifecycle transitions.

    Filterable via query params:
      ?organization=<uuid>
      ?review_status=flagged,ingested
      ?ghg_scope=scope_1
    """

    queryset = (
        ActivityRecord.objects.select_related("facility")
        .prefetch_related("review_events")
        .all()
    )

    def get_serializer_class(self):
        if self.action == "list":
            return ActivityRecordListSerializer
        return ActivityRecordDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if org := params.get("organization"):
            qs = qs.filter(organization_id=org)
        if statuses := params.get("review_status"):
            qs = qs.filter(review_status__in=statuses.split(","))
        if scope := params.get("ghg_scope"):
            qs = qs.filter(ghg_scope=scope)
        return qs

    # --- Lifecycle transitions ---------------------------------------

    def _actor(self, request) -> tuple[str, str]:
        if request.user.is_authenticated:
            return str(request.user.pk), request.user.get_username()
        # Prototype: actor identity comes from a header. In production this
        # would be the authenticated session user.
        actor = request.headers.get("X-Analyst-Id", "anonymous")
        return actor, actor

    def _do_transition(self, request, pk, *, to_status: str, event_type: str):
        record = self.get_object()
        actor_id, actor_name = self._actor(request)
        note = request.data.get("note", "")
        try:
            transition(
                record=record,
                to_status=to_status,
                actor_id=actor_id,
                actor_display_name=actor_name,
                event_type=event_type,
                note=note,
            )
        except InvalidTransitionError as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(
            ActivityRecordDetailSerializer(record).data, status=status.HTTP_200_OK
        )

    @action(detail=True, methods=["post"])
    def flag(self, request, pk=None):
        """Manually flag a record. `note` (request body) required as the reason."""
        if not request.data.get("note"):
            return Response(
                {"detail": "note is required when flagging"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._do_transition(
            request, pk, to_status=ReviewStatus.FLAGGED, event_type="manually_flagged"
        )

    @action(detail=True, methods=["post"])
    def mark_reviewed(self, request, pk=None):
        return self._do_transition(
            request,
            pk,
            to_status=ReviewStatus.ANALYST_REVIEWED,
            event_type="analyst_reviewed",
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._do_transition(
            request, pk, to_status=ReviewStatus.APPROVED, event_type="approved"
        )

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        return self._do_transition(
            request, pk, to_status=ReviewStatus.LOCKED, event_type="locked"
        )
