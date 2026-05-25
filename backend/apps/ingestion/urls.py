from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    IngestionRunViewSet,
    SapIngestView,
    SapNormalizeView,
    SapStagingIssuesView,
    TravelIngestView,
    TravelNormalizeView,
    TravelStagingIssuesView,
    UtilityIngestView,
    UtilityNormalizeView,
    UtilityStagingIssuesView,
)

router = DefaultRouter()
router.register("ingestion-runs", IngestionRunViewSet)

urlpatterns = [
    *router.urls,
    # SAP
    path("ingest/sap/", SapIngestView.as_view(), name="sap-ingest"),
    path("normalize/sap/", SapNormalizeView.as_view(), name="sap-normalize"),
    path(
        "staging/sap/issues/",
        SapStagingIssuesView.as_view(),
        name="sap-staging-issues",
    ),
    # Utility
    path("ingest/utility/", UtilityIngestView.as_view(), name="utility-ingest"),
    path(
        "normalize/utility/",
        UtilityNormalizeView.as_view(),
        name="utility-normalize",
    ),
    path(
        "staging/utility/issues/",
        UtilityStagingIssuesView.as_view(),
        name="utility-staging-issues",
    ),
    # Travel
    path("ingest/travel/", TravelIngestView.as_view(), name="travel-ingest"),
    path(
        "normalize/travel/",
        TravelNormalizeView.as_view(),
        name="travel-normalize",
    ),
    path(
        "staging/travel/issues/",
        TravelStagingIssuesView.as_view(),
        name="travel-staging-issues",
    ),
]
