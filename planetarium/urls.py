from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AstronomyShowViewSet,
    AstronomyThemeViewSet,
    PlanetariumDomeViewSet,
    ReservationViewSet,
    ShowSessionViewSet,
)

router = DefaultRouter()
router.register("themes", AstronomyThemeViewSet)
router.register("shows", AstronomyShowViewSet)
router.register("domes", PlanetariumDomeViewSet)
router.register("sessions", ShowSessionViewSet, basename="session")
router.register("reservations", ReservationViewSet, basename="reservation")
app_name = "planetarium"
urlpatterns = [path("", include(router.urls))]
