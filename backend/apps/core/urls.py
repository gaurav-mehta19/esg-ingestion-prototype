"""
The core app exposes lookup endpoints for the dashboard to render facility
names and source labels. We keep these read-only for the prototype.
"""

from rest_framework.routers import DefaultRouter

from .views import (
    DataSourceViewSet,
    FacilityViewSet,
    OrganizationViewSet,
    PlantLocationMappingViewSet,
)

router = DefaultRouter()
router.register("organizations", OrganizationViewSet)
router.register("data-sources", DataSourceViewSet)
router.register("facilities", FacilityViewSet)
router.register("plant-mappings", PlantLocationMappingViewSet)

urlpatterns = router.urls
