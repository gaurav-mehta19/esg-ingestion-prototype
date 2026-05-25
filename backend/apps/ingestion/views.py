"""
Ingestion API: trigger an SAP CSV upload and renormalize.

React-developer note: APIView is the most explicit DRF base class — one
class per endpoint with HTTP-method handlers (post, get). Use it when an
endpoint doesn't fit the ModelViewSet CRUD shape (uploads, batch ops,
custom workflows). For straight list/retrieve we use ModelViewSet.
"""

from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import DataSource
from apps.ingestion.intake import (
    ingest_concur_json,
    ingest_sap_csv,
    ingest_utility_csv,
)
from apps.ingestion.models import (
    IngestionRun,
    SapGoodsMovementStaging,
    StagingStatus,
    TravelExpenseStaging,
    UtilityIntervalStaging,
)
from apps.ingestion.normalizers.sap import normalize_sap_staging_batch
from apps.ingestion.normalizers.travel import normalize_travel_staging_batch
from apps.ingestion.normalizers.utility import normalize_utility_staging_batch
from apps.ingestion.serializers import (
    IngestionRunSerializer,
    SapStagingSerializer,
    TravelStagingSerializer,
    UtilityStagingSerializer,
)


class IngestionRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = IngestionRun.objects.select_related("data_source").all()
    serializer_class = IngestionRunSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        org = self.request.query_params.get("organization")
        if org:
            qs = qs.filter(organization_id=org)
        return qs


class SapStagingIssuesView(generics.ListAPIView):
    """
    Lists staging rows that need attention: rejected, needs_uom_mapping,
    or needs_plant_mapping. The dashboard reads from this to show "what
    didn't make it through normalization and why".
    """

    serializer_class = SapStagingSerializer

    def get_queryset(self):
        qs = SapGoodsMovementStaging.objects.filter(
            staging_status__in=[
                StagingStatus.REJECTED,
                StagingStatus.NEEDS_UOM_MAPPING,
                StagingStatus.NEEDS_PLANT_MAPPING,
            ],
        ).order_by("-ingested_at")
        org = self.request.query_params.get("organization")
        if org:
            qs = qs.filter(organization_id=org)
        return qs


class SapIngestView(APIView):
    """
    POST a CSV file to ingest. Multipart upload for the prototype; in
    production the IDoc would arrive via an SFTP drop or a SAP push.

    Body: multipart/form-data
      file:           the CSV file
      data_source:    UUID of the configured DataSource
      organization:   UUID of the Organization (for now — in production this
                      comes from the authenticated user's session)
    """

    def post(self, request):
        f = request.FILES.get("file")
        data_source_id = request.data.get("data_source")
        organization_id = request.data.get("organization")
        if not f or not data_source_id or not organization_id:
            return Response(
                {"detail": "file, data_source, organization are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data_source = DataSource.objects.get(
                id=data_source_id, organization_id=organization_id
            )
        except DataSource.DoesNotExist:
            return Response(
                {"detail": "DataSource not found for this organization"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return Response(
                {"detail": "file is not UTF-8 (try exporting from SAP as UTF-8 CSV)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = ingest_sap_csv(
            organization_id=organization_id,
            data_source=data_source,
            csv_text=text,
            triggered_by="manual",
            triggered_by_user=str(request.user) if request.user.is_authenticated else "anonymous",
            raw_file_ref=f.name,
        )
        return Response(result.__dict__, status=status.HTTP_201_CREATED)


class UtilityStagingIssuesView(generics.ListAPIView):
    serializer_class = UtilityStagingSerializer

    def get_queryset(self):
        qs = UtilityIntervalStaging.objects.exclude(
            staging_status=StagingStatus.NORMALIZED
        ).order_by("-ingested_at")
        org = self.request.query_params.get("organization")
        if org:
            qs = qs.filter(organization_id=org)
        return qs


class TravelStagingIssuesView(generics.ListAPIView):
    serializer_class = TravelStagingSerializer

    def get_queryset(self):
        qs = TravelExpenseStaging.objects.exclude(
            staging_status=StagingStatus.NORMALIZED
        ).order_by("-ingested_at")
        org = self.request.query_params.get("organization")
        if org:
            qs = qs.filter(organization_id=org)
        return qs


class UtilityIngestView(APIView):
    """POST a Green Button CSV file."""

    def post(self, request):
        f = request.FILES.get("file")
        data_source_id = request.data.get("data_source")
        organization_id = request.data.get("organization")
        if not f or not data_source_id or not organization_id:
            return Response(
                {"detail": "file, data_source, organization are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data_source = DataSource.objects.get(
                id=data_source_id, organization_id=organization_id
            )
        except DataSource.DoesNotExist:
            return Response({"detail": "DataSource not found"}, status=404)
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return Response({"detail": "file is not UTF-8"}, status=400)
        result = ingest_utility_csv(
            organization_id=organization_id,
            data_source=data_source,
            csv_text=text,
            triggered_by="manual",
            raw_file_ref=f.name,
        )
        return Response(result.__dict__, status=201)


class TravelIngestView(APIView):
    """POST a Concur JSON file."""

    def post(self, request):
        f = request.FILES.get("file")
        data_source_id = request.data.get("data_source")
        organization_id = request.data.get("organization")
        if not f or not data_source_id or not organization_id:
            return Response(
                {"detail": "file, data_source, organization are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data_source = DataSource.objects.get(
                id=data_source_id, organization_id=organization_id
            )
        except DataSource.DoesNotExist:
            return Response({"detail": "DataSource not found"}, status=404)
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return Response({"detail": "file is not UTF-8"}, status=400)
        result = ingest_concur_json(
            organization_id=organization_id,
            data_source=data_source,
            json_text=text,
            triggered_by="manual",
            raw_file_ref=f.name,
        )
        return Response(result.__dict__, status=201)


class UtilityNormalizeView(APIView):
    def post(self, request):
        organization_id = request.data.get("organization")
        data_source_id = request.data.get("data_source")
        if not organization_id or not data_source_id:
            return Response({"detail": "organization and data_source required"}, status=400)
        result = normalize_utility_staging_batch(
            organization_id=organization_id, data_source_id=data_source_id
        )
        return Response(result.__dict__)


class TravelNormalizeView(APIView):
    def post(self, request):
        organization_id = request.data.get("organization")
        data_source_id = request.data.get("data_source")
        if not organization_id or not data_source_id:
            return Response({"detail": "organization and data_source required"}, status=400)
        result = normalize_travel_staging_batch(
            organization_id=organization_id, data_source_id=data_source_id
        )
        return Response(result.__dict__)


class SapNormalizeView(APIView):
    """
    POST to run the normalizer over pending staging rows for one
    (organization, data_source). Returns a summary.

    In production this would be triggered automatically after ingest (Celery
    task). For the prototype, keep it manual so the demo can show the two
    phases distinctly.
    """

    def post(self, request):
        organization_id = request.data.get("organization")
        data_source_id = request.data.get("data_source")
        if not organization_id or not data_source_id:
            return Response(
                {"detail": "organization and data_source are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = normalize_sap_staging_batch(
            organization_id=organization_id, data_source_id=data_source_id
        )
        return Response(result.__dict__, status=status.HTTP_200_OK)
