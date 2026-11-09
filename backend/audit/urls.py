from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("logs", views.ActionLogViewSet, basename="log")

urlpatterns = router.urls
