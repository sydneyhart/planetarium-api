from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import AstronomyShow, AstronomyTheme, PlanetariumDome, Reservation, ShowSession, Ticket
from .services import create_reservation


class AstronomyThemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AstronomyTheme
        fields = ["id", "name"]


class AstronomyShowSerializer(serializers.ModelSerializer):
    themes = serializers.PrimaryKeyRelatedField(
        queryset=AstronomyTheme.objects.all(), many=True, required=False
    )
    theme_names = serializers.SlugRelatedField(
        source="themes", many=True, slug_field="name", read_only=True
    )
    duration = serializers.IntegerField(min_value=1, max_value=600)
    age_limit = serializers.IntegerField(min_value=0, max_value=21, required=False)

    class Meta:
        model = AstronomyShow
        fields = [
            "id",
            "title",
            "description",
            "duration",
            "age_limit",
            "themes",
            "theme_names",
            "image",
        ]

    def validate_duration(self, value):
        if self.instance and value != self.instance.duration and self.instance.sessions.exists():
            raise serializers.ValidationError("Duration cannot change once sessions are scheduled.")
        return value

    def validate_image(self, image):
        if image and image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Maximum image size is 5 MiB.")
        if image and image.image.format not in {"JPEG", "PNG", "WEBP"}:
            raise serializers.ValidationError("Upload a JPEG, PNG, or WebP image.")
        return image


class PlanetariumDomeSerializer(serializers.ModelSerializer):
    rows = serializers.IntegerField(min_value=1, max_value=100)
    seats_in_row = serializers.IntegerField(min_value=1, max_value=100)
    capacity = serializers.IntegerField(read_only=True)

    class Meta:
        model = PlanetariumDome
        fields = ["id", "name", "rows", "seats_in_row", "capacity"]

    def validate(self, attrs):
        if self.instance and self.instance.sessions.exists():
            if any(
                attrs.get(field, getattr(self.instance, field)) != getattr(self.instance, field)
                for field in ("rows", "seats_in_row")
            ):
                raise serializers.ValidationError(
                    "Dome seating cannot change once sessions are scheduled."
                )
        return attrs


class ShowSessionSerializer(serializers.ModelSerializer):
    show_title = serializers.CharField(source="astronomy_show.title", read_only=True)
    dome_name = serializers.CharField(source="planetarium_dome.name", read_only=True)
    end_time = serializers.DateTimeField(read_only=True)
    capacity = serializers.IntegerField(source="planetarium_dome.capacity", read_only=True)
    tickets_available = serializers.IntegerField(read_only=True)
    price = serializers.DecimalField(
        max_digits=8, decimal_places=2, min_value=Decimal("0.00"), required=False
    )

    class Meta:
        model = ShowSession
        fields = [
            "id",
            "astronomy_show",
            "show_title",
            "planetarium_dome",
            "dome_name",
            "show_time",
            "end_time",
            "price",
            "capacity",
            "tickets_available",
        ]

    def validate(self, attrs):
        session = self.instance
        start = attrs.get("show_time", session.show_time if session else None)
        if start and start <= timezone.now():
            raise serializers.ValidationError({"show_time": "Schedule a session in the future."})
        if session and session.tickets.filter(is_active=True).exists():
            for field in ("astronomy_show", "planetarium_dome", "show_time", "price"):
                if field in attrs and attrs[field] != getattr(session, field):
                    raise serializers.ValidationError(
                        "A booked session cannot be rescheduled or repriced."
                    )
        show = attrs.get("astronomy_show", session.astronomy_show if session else None)
        dome = attrs.get("planetarium_dome", session.planetarium_dome if session else None)
        if start and show and dome:
            self.check_overlap(start, show, dome)
        return attrs

    def check_overlap(self, start, show, dome):
        end = start + timedelta(minutes=show.duration)
        query = ShowSession.objects.filter(planetarium_dome=dome, show_time__lt=end).select_related(
            "astronomy_show"
        )
        if self.instance:
            query = query.exclude(pk=self.instance.pk)
        if any(session.end_time > start for session in query):
            raise serializers.ValidationError(
                {"show_time": "This session overlaps another session in the dome."}
            )

    @transaction.atomic
    def create(self, validated_data):
        PlanetariumDome.objects.select_for_update().get(pk=validated_data["planetarium_dome"].pk)
        self.check_overlap(
            validated_data["show_time"],
            validated_data["astronomy_show"],
            validated_data["planetarium_dome"],
        )
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        locked = ShowSession.objects.select_for_update().get(pk=instance.pk)
        dome = validated_data.get("planetarium_dome", locked.planetarium_dome)
        list(
            PlanetariumDome.objects.select_for_update()
            .filter(pk__in=[locked.planetarium_dome_id, dome.pk])
            .order_by("pk")
        )
        self.instance = locked
        self.validate(validated_data)
        return super().update(locked, validated_data)


class SessionFilters(serializers.Serializer):
    date = serializers.DateField(required=False, help_text="UTC date, YYYY-MM-DD.")
    show = serializers.IntegerField(min_value=1, required=False)
    dome = serializers.IntegerField(min_value=1, required=False)
    upcoming = serializers.BooleanField(required=False)


class TicketInputSerializer(serializers.Serializer):
    show_session = serializers.PrimaryKeyRelatedField(queryset=ShowSession.objects.all())
    row = serializers.IntegerField(min_value=1)
    seat = serializers.IntegerField(min_value=1)


class TicketSerializer(serializers.ModelSerializer):
    show_title = serializers.CharField(source="show_session.astronomy_show.title", read_only=True)
    show_time = serializers.DateTimeField(source="show_session.show_time", read_only=True)

    class Meta:
        model = Ticket
        fields = [
            "id",
            "show_session",
            "show_title",
            "show_time",
            "row",
            "seat",
            "unit_price",
            "is_active",
        ]


class ReservationSerializer(serializers.ModelSerializer):
    tickets = TicketSerializer(many=True, read_only=True)
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = ["id", "created_at", "status", "total_amount", "tickets"]
        read_only_fields = fields

    def get_total_amount(self, reservation) -> str:
        return str(
            sum((ticket.unit_price for ticket in reservation.tickets.all()), Decimal("0.00"))
        )


class ReservationCreateSerializer(serializers.Serializer):
    tickets = TicketInputSerializer(many=True, allow_empty=False, max_length=20)

    def validate_tickets(self, tickets):
        keys = [(item["show_session"].pk, item["row"], item["seat"]) for item in tickets]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("A seat may only appear once in a reservation.")
        return tickets

    def create(self, validated_data):
        return create_reservation(self.context["request"].user, validated_data["tickets"])


class AvailabilitySerializer(serializers.Serializer):
    session = serializers.IntegerField()
    rows = serializers.IntegerField()
    seats_in_row = serializers.IntegerField()
    capacity = serializers.IntegerField()
    available = serializers.IntegerField()
    booked_seats = serializers.ListField(
        child=serializers.DictField(child=serializers.IntegerField())
    )
