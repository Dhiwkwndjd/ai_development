from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("bookings", views.BookingViewSet, basename="booking")
router.register("queue", views.QueueViewSet, basename="queue")

urlpatterns = router.urls
