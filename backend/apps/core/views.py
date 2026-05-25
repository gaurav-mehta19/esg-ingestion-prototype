"""
Read-only ViewSets for reference data.

React-developer note: ModelViewSet generates the standard REST endpoints
(list/retrieve/create/update/delete) from one class. ReadOnlyModelViewSet
omits the mutating actions — appropriate here because reference data is
maintained via Django admin in the prototype, not the analyst UI.
"""

from rest_framework import serializers, viewsets

from .models import DataSource, Facility, Organization, PlantLocationMapping


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "created_at"]


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer


class DataSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSource
        fields = [
            "id",
            "organization",
            "source_type",
            "display_name",
            "is_active",
            "created_at",
        ]


class DataSourceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DataSource.objects.all()
    serializer_class = DataSourceSerializer


class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = [
            "id",
            "organization",
            "name",
            "city",
            "country_code",
            "grid_region",
        ]


class FacilityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Facility.objects.all()
    serializer_class = FacilitySerializer


class PlantLocationMappingSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source="facility.name", read_only=True)

    class Meta:
        model = PlantLocationMapping
        fields = [
            "id",
            "werks",
            "sap_plant_name",
            "facility",
            "facility_name",
            "valid_from",
            "valid_to",
        ]


class PlantLocationMappingViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PlantLocationMapping.objects.select_related("facility").all()
    serializer_class = PlantLocationMappingSerializer
