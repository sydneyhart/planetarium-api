from django.db.models import Count, F, Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import AstronomyShow, AstronomyTheme, PlanetariumDome, Reservation, ShowSession
from .permissions import IsAdminOrAuthenticatedReadOnly
from .serializers import (
    AstronomyShowSerializer,
    AstronomyThemeSerializer,
    AvailabilitySerializer,
    PlanetariumDomeSerializer,
    ReservationCreateSerializer,
    ReservationSerializer,
    SessionFilters,
    ShowSessionSerializer,
)
from .services import cancel_reservation


class ProtectedCatalogViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminOrAuthenticatedReadOnly]

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError(
                {"detail": "This item is referenced by sessions or reservations."}
            ) from None


class AstronomyThemeViewSet(ProtectedCatalogViewSet):
    queryset = AstronomyTheme.objects.all()
    serializer_class = AstronomyThemeSerializer


class AstronomyShowViewSet(ProtectedCatalogViewSet):
    queryset = AstronomyShow.objects.prefetch_related("themes")
    serializer_class = AstronomyShowSerializer

    def get_queryset(self):
        query = super().get_queryset()
        if self.action == "list":
            if title := self.request.query_params.get("title"):
                query = query.filter(title__icontains=title)
            if theme := self.request.query_params.get("theme"):
                if not theme.isdecimal() or len(theme) > 18:
                    raise ValidationError({"theme": "Use a numeric theme ID."})
                query = query.filter(themes__id=theme)
        return query.distinct()

    @extend_schema(parameters=[OpenApiParameter("title", str), OpenApiParameter("theme", int)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class PlanetariumDomeViewSet(ProtectedCatalogViewSet):
    queryset = PlanetariumDome.objects.all()
    serializer_class = PlanetariumDomeSerializer


class ShowSessionViewSet(ProtectedCatalogViewSet):
    serializer_class = ShowSessionSerializer
    queryset = ShowSession.objects.none()

    def get_queryset(self):
        query = (
            ShowSession.objects.select_related("astronomy_show", "planetarium_dome")
            .annotate(
                tickets_available=F("planetarium_dome__rows") * F("planetarium_dome__seats_in_row")
                - Count("tickets", filter=Q(tickets__is_active=True))
            )
            .order_by("show_time", "pk")
        )
        if self.action == "list":
            filters = SessionFilters(data=self.request.query_params)
            filters.is_valid(raise_exception=True)
            params = filters.validated_data
            if "date" in params:
                query = query.filter(show_time__date=params["date"])
            if "show" in params:
                query = query.filter(astronomy_show_id=params["show"])
            if "dome" in params:
                query = query.filter(planetarium_dome_id=params["dome"])
            if params.get("upcoming"):
                query = query.filter(show_time__gt=timezone.now())
        return query

    @extend_schema(parameters=[SessionFilters])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = serializer.save()
        return Response(
            self.get_serializer(self.get_queryset().get(pk=session.pk)).data, status=201
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=kwargs.pop("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(self.get_serializer(self.get_queryset().get(pk=instance.pk)).data)

    @extend_schema(responses=AvailabilitySerializer)
    @action(detail=True, methods=["get"])
    def availability(self, request, pk=None):
        session = self.get_object()
        dome = session.planetarium_dome
        return Response(
            {
                "session": session.pk,
                "rows": dome.rows,
                "seats_in_row": dome.seats_in_row,
                "capacity": dome.capacity,
                "available": session.tickets_available,
                "booked_seats": list(session.tickets.filter(is_active=True).values("row", "seat")),
            }
        )


class ReservationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ReservationSerializer
    queryset = Reservation.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Reservation.objects.none()
        return Reservation.objects.filter(user=self.request.user).prefetch_related(
            "tickets__show_session__astronomy_show"
        )

    def get_serializer_class(self):
        if self.action == "create":
            return ReservationCreateSerializer
        return ReservationSerializer

    @extend_schema(request=ReservationCreateSerializer, responses={201: ReservationSerializer})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reservation = serializer.save()
        return Response(
            ReservationSerializer(
                self.get_queryset().get(pk=reservation.pk), context=self.get_serializer_context()
            ).data,
            status=201,
        )

    @extend_schema(request=None, responses=ReservationSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        reservation = cancel_reservation(self.get_object())
        return Response(self.get_serializer(self.get_queryset().get(pk=reservation.pk)).data)
