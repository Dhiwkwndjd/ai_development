from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("books", views.BookViewSet, basename="book")
router.register("copies", views.BookCopyViewSet, basename="copy")

urlpatterns = router.urls
