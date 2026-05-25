"""
Top-level URL routing.

React-developer note: Django's URLConf is the equivalent of React Router's route
table, but for HTTP endpoints. Each `path()` matches a URL prefix and delegates
to either a view function or another included URLConf (sub-router).
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.ingestion.urls")),
    path("api/", include("apps.activity.urls")),
]
