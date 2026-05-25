from rest_framework.routers import DefaultRouter

from .views import ActivityRecordViewSet

router = DefaultRouter()
router.register("activity-records", ActivityRecordViewSet)

urlpatterns = router.urls
