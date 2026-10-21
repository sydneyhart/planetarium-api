from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Reservation, ShowSession, Ticket


@transaction.atomic
def create_reservation(user, tickets):
    session_ids = sorted({ticket["show_session"].pk for ticket in tickets})
    sessions = {
        session.pk: session
        for session in ShowSession.objects.select_for_update(of=("self",))
        .select_related("planetarium_dome", "astronomy_show")
        .filter(pk__in=session_ids)
        .order_by("pk")
    }
    if len(sessions) != len(session_ids):
        raise ValidationError({"tickets": "A selected session no longer exists."})
    now = timezone.now()
    for ticket in tickets:
        session = sessions[ticket["show_session"].pk]
        if session.show_time <= now:
            raise ValidationError({"tickets": "Booking is only available before a session starts."})
        dome = session.planetarium_dome
        if not 1 <= ticket["row"] <= dome.rows or not 1 <= ticket["seat"] <= dome.seats_in_row:
            raise ValidationError(
                {"tickets": f"Choose a row in 1..{dome.rows} and seat in 1..{dome.seats_in_row}."}
            )
        if Ticket.objects.filter(
            show_session=session, row=ticket["row"], seat=ticket["seat"], is_active=True
        ).exists():
            raise ValidationError({"tickets": "One or more seats are already booked."})
    reservation = Reservation.objects.create(user=user)
    try:
        with transaction.atomic():
            Ticket.objects.bulk_create(
                [
                    Ticket(
                        reservation=reservation,
                        show_session=sessions[item["show_session"].pk],
                        row=item["row"],
                        seat=item["seat"],
                        unit_price=sessions[item["show_session"].pk].price,
                    )
                    for item in tickets
                ]
            )
    except IntegrityError:
        raise ValidationError(
            {"tickets": "One or more seats were just booked. Choose other seats."}
        ) from None
    return reservation


@transaction.atomic
def cancel_reservation(reservation):
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if reservation.status == Reservation.Status.CANCELLED:
        return reservation
    session_ids = reservation.tickets.values_list("show_session_id", flat=True)
    sessions = list(
        ShowSession.objects.select_for_update().filter(pk__in=session_ids).order_by("pk")
    )
    if any(session.show_time <= timezone.now() for session in sessions):
        raise ValidationError(
            {"detail": "Reservations cannot be cancelled after a session starts."}
        )
    reservation.tickets.update(is_active=False)
    reservation.status = Reservation.Status.CANCELLED
    reservation.save(update_fields=["status"])
    return reservation
