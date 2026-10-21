import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import Q


def show_image_path(instance, filename):
    return f"shows/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class AstronomyTheme(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AstronomyShow(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    duration = models.PositiveIntegerField(help_text="Running time in minutes.")
    age_limit = models.PositiveSmallIntegerField(default=0)
    themes = models.ManyToManyField(AstronomyTheme, related_name="shows", blank=True)
    image = models.ImageField(upload_to=show_image_path, blank=True, null=True)

    class Meta:
        ordering = ["title", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(duration__gt=0), name="show_duration_positive")
        ]

    def __str__(self):
        return self.title


class PlanetariumDome(models.Model):
    name = models.CharField(max_length=100, unique=True)
    rows = models.PositiveSmallIntegerField()
    seats_in_row = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=Q(rows__gt=0), name="dome_rows_positive"),
            models.CheckConstraint(condition=Q(seats_in_row__gt=0), name="dome_seats_positive"),
        ]

    @property
    def capacity(self):
        return self.rows * self.seats_in_row

    def __str__(self):
        return self.name


class ShowSession(models.Model):
    astronomy_show = models.ForeignKey(
        AstronomyShow, related_name="sessions", on_delete=models.PROTECT
    )
    planetarium_dome = models.ForeignKey(
        PlanetariumDome, related_name="sessions", on_delete=models.PROTECT
    )
    show_time = models.DateTimeField(db_index=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, default="15.00")

    class Meta:
        ordering = ["show_time", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="session_price_nonnegative")
        ]
        indexes = [models.Index(fields=["planetarium_dome", "show_time"])]

    @property
    def end_time(self):
        return self.show_time + timedelta(minutes=self.astronomy_show.duration)

    def __str__(self):
        return f"{self.astronomy_show} at {self.show_time:%Y-%m-%d %H:%M}"


class Reservation(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="reservations", on_delete=models.PROTECT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Reservation #{self.pk} ({self.status})"


class Ticket(models.Model):
    reservation = models.ForeignKey(Reservation, related_name="tickets", on_delete=models.CASCADE)
    show_session = models.ForeignKey(ShowSession, related_name="tickets", on_delete=models.PROTECT)
    row = models.PositiveSmallIntegerField()
    seat = models.PositiveSmallIntegerField()
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["show_session_id", "row", "seat"]
        constraints = [
            models.UniqueConstraint(
                fields=["show_session", "row", "seat"],
                condition=Q(is_active=True),
                name="unique_active_session_seat",
            ),
            models.CheckConstraint(condition=Q(row__gt=0), name="ticket_row_positive"),
            models.CheckConstraint(condition=Q(seat__gt=0), name="ticket_seat_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="ticket_price_nonnegative"),
        ]

    def __str__(self):
        return f"{self.show_session}: row {self.row}, seat {self.seat}"
