"""
Django admin registration — gives us a built-in CRUD UI for reference data
at /admin/. Not the analyst-facing dashboard (that's the React app); this
is for ops/devs to set up orgs, sources, and mappings without writing SQL.
"""

from django.contrib import admin

from .models import (
    DataSource,
    ExpenseTypeCategoryMapping,
    Facility,
    MeterLocationMapping,
    Organization,
    PlantLocationMapping,
    UomNormalizationRule,
)

admin.site.register(Organization)
admin.site.register(DataSource)
admin.site.register(Facility)
admin.site.register(PlantLocationMapping)
admin.site.register(MeterLocationMapping)
admin.site.register(ExpenseTypeCategoryMapping)
admin.site.register(UomNormalizationRule)
