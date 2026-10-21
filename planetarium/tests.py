import io
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from .models import AstronomyShow, AstronomyTheme, PlanetariumDome, Reservation, ShowSession, Ticket
from .services import create_reservation

User = get_user_model()
BASE = "/api/planetarium/"


class PlanetariumTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = tempfile.mkdtemp()
        cls.media_override = override_settings(MEDIA_ROOT=cls.media)
        cls.media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.media_override.disable()
        shutil.rmtree(cls.media)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("visitor@example.com", "password")
        self.other = User.objects.create_user("other@example.com", "password")
        self.admin = User.objects.create_superuser("admin@example.com", "password")
        self.theme = AstronomyTheme.objects.create(name="Solar system")
        self.show = AstronomyShow.objects.create(
            title="Journey to Mars", description="Explore Mars.", duration=60
        )
        self.show.themes.add(self.theme)
        self.dome = PlanetariumDome.objects.create(name="Orion Dome", rows=4, seats_in_row=5)
        self.start = (timezone.now() + timedelta(days=2)).replace(microsecond=0)
        self.session = ShowSession.objects.create(
            astronomy_show=self.show,
            planetarium_dome=self.dome,
            show_time=self.start,
            price="12.50",
        )
        self.client.force_authenticate(self.user)

    def ticket(self, row=1, seat=1, session=None):
        return {"show_session": (session or self.session).pk, "row": row, "seat": seat}

    def book(self, tickets=None):
        return self.client.post(
            BASE + "reservations/", {"tickets": tickets if tickets is not None else [self.ticket()]}
        )

    def test_anonymous_catalogue_booking_and_availability_denied(self):
        self.client.force_authenticate(None)
        for url in (
            "shows/",
            "domes/",
            "sessions/",
            "reservations/",
            f"sessions/{self.session.pk}/availability/",
        ):
            self.assertEqual(self.client.get(BASE + url).status_code, 401)
        self.assertEqual(self.book().status_code, 401)

    def test_authenticated_visitors_read_but_cannot_write_catalogue(self):
        for endpoint in ("themes", "shows", "domes", "sessions"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(BASE + endpoint + "/").status_code, 200)
                self.assertEqual(self.client.post(BASE + endpoint + "/", {}).status_code, 403)
        self.assertEqual(
            self.client.patch(BASE + f"shows/{self.show.pk}/", {"title": "Hacked"}).status_code, 403
        )
        self.assertEqual(self.client.delete(BASE + f"shows/{self.show.pk}/").status_code, 403)

    def test_staff_can_create_catalogue_items(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(BASE + "themes/", {"name": "Galaxies"}).status_code, 201)
        response = self.client.post(
            BASE + "shows/",
            {"title": "Stars", "description": "A tour.", "duration": 45, "themes": [self.theme.pk]},
        )
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(
            BASE + "domes/", {"name": "New dome", "rows": 3, "seats_in_row": 4}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["capacity"], 12)

    def test_show_title_and_theme_filters(self):
        AstronomyShow.objects.create(title="Other show", description="Other.", duration=30)
        for params in ({"title": "MARS"}, {"theme": self.theme.pk}):
            response = self.client.get(BASE + "shows/", params)
            self.assertEqual([item["id"] for item in response.data["results"]], [self.show.pk])
        self.assertEqual(self.client.get(BASE + "shows/", {"theme": "abc"}).status_code, 400)

    def test_catalogue_field_validation(self):
        self.client.force_authenticate(self.admin)
        for rows in (0, -1, 101):
            self.assertEqual(
                self.client.post(
                    BASE + "domes/", {"name": "Invalid", "rows": rows, "seats_in_row": 4}
                ).status_code,
                400,
            )
        self.assertEqual(
            self.client.post(
                BASE + "shows/", {"title": "Invalid", "description": "Bad", "duration": 0}
            ).status_code,
            400,
        )

    def test_show_poster_upload_and_invalid_image(self):
        self.client.force_authenticate(self.admin)
        image = io.BytesIO()
        Image.new("RGB", (10, 10)).save(image, format="PNG")
        image.seek(0)
        image.name = "poster.png"
        url = BASE + f"shows/{self.show.pk}/"
        response = self.client.patch(url, {"image": image}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.show.refresh_from_db()
        self.assertTrue(self.show.image.storage.exists(self.show.image.name))
        self.assertEqual(
            self.client.patch(url, {"image": "bad"}, format="multipart").status_code, 400
        )

    def test_schedule_create_availability_count(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            BASE + "sessions/",
            {
                "astronomy_show": self.show.pk,
                "planetarium_dome": self.dome.pk,
                "show_time": self.start + timedelta(hours=2),
                "price": "9.00",
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["tickets_available"], 20)
        self.assertEqual(response.data["show_title"], self.show.title)

    def test_overlapping_session_rejected_and_back_to_back_allowed(self):
        self.client.force_authenticate(self.admin)
        payload = {
            "astronomy_show": self.show.pk,
            "planetarium_dome": self.dome.pk,
            "price": "10.00",
        }
        for offset in (-30, 0, 30):
            response = self.client.post(
                BASE + "sessions/", payload | {"show_time": self.start + timedelta(minutes=offset)}
            )
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(
            BASE + "sessions/", payload | {"show_time": self.start + timedelta(minutes=60)}
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_overlapping_times_allowed_in_different_domes(self):
        self.client.force_authenticate(self.admin)
        dome = PlanetariumDome.objects.create(name="Second", rows=2, seats_in_row=3)
        response = self.client.post(
            BASE + "sessions/",
            {"astronomy_show": self.show.pk, "planetarium_dome": dome.pk, "show_time": self.start},
        )
        self.assertEqual(response.status_code, 201)

    def test_past_session_and_negative_price_rejected(self):
        self.client.force_authenticate(self.admin)
        payload = {
            "astronomy_show": self.show.pk,
            "planetarium_dome": self.dome.pk,
            "show_time": self.start + timedelta(hours=2),
        }
        self.assertEqual(
            self.client.post(BASE + "sessions/", payload | {"price": "-1.00"}).status_code, 400
        )
        self.assertEqual(
            self.client.post(
                BASE + "sessions/", payload | {"show_time": timezone.now() - timedelta(hours=1)}
            ).status_code,
            400,
        )

    def test_session_filters_and_invalid_date(self):
        for params in (
            {"show": self.show.pk},
            {"dome": self.dome.pk},
            {"date": self.start.date().isoformat()},
            {"upcoming": "true"},
        ):
            response = self.client.get(BASE + "sessions/", params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["count"], 1)
        for params in ({"date": "bad"}, {"show": "abc"}, {"dome": "-1"}, {"upcoming": "not-bool"}):
            self.assertEqual(self.client.get(BASE + "sessions/", params).status_code, 400)

    def test_successful_reservation_and_server_side_price(self):
        response = self.client.post(
            BASE + "reservations/",
            {
                "tickets": [self.ticket(1, 1), self.ticket(1, 2)],
                "user": self.other.pk,
                "status": "cancelled",
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total_amount"], "25.00")
        self.assertEqual(response.data["status"], "confirmed")
        self.assertEqual(Reservation.objects.get().user_id, self.user.pk)
        self.assertEqual(Ticket.objects.count(), 2)
        self.assertEqual(
            self.client.get(BASE + f"sessions/{self.session.pk}/").data["tickets_available"], 18
        )

    def test_out_of_range_seats_rejected(self):
        for row, seat in ((0, 1), (5, 1), (1, 0), (1, 6), (-1, 1)):
            with self.subTest(row=row, seat=seat):
                self.assertEqual(self.book([self.ticket(row, seat)]).status_code, 400)
        self.assertFalse(Reservation.objects.exists())

    def test_empty_oversized_and_duplicate_ticket_payloads(self):
        for tickets in ([], [self.ticket()] * 2, [self.ticket()] * 21):
            self.assertEqual(self.book(tickets).status_code, 400)
        self.assertFalse(Reservation.objects.exists())

    def test_missing_session_rejected(self):
        response = self.book([self.ticket() | {"show_session": 9999}])
        self.assertEqual(response.status_code, 400)

    def test_double_booking_rejected_without_partial_reservation(self):
        self.assertEqual(self.book().status_code, 201)
        self.client.force_authenticate(self.other)
        response = self.book([self.ticket(1, 2), self.ticket(1, 1)])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_booking_started_session_rejected(self):
        ShowSession.objects.filter(pk=self.session.pk).update(
            show_time=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(self.book().status_code, 400)
        self.assertFalse(Reservation.objects.exists())

    def test_availability_does_not_expose_customer_data(self):
        self.book()
        response = self.client.get(BASE + f"sessions/{self.session.pk}/availability/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["available"], 19)
        self.assertEqual(response.data["booked_seats"], [{"row": 1, "seat": 1}])
        self.assertNotIn(self.user.email, str(response.data))

    def test_reservation_lists_and_details_are_private_even_for_staff(self):
        reservation = self.book().data
        for user in (self.other, self.admin):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(BASE + "reservations/").data["count"], 0)
            self.assertEqual(
                self.client.get(BASE + f"reservations/{reservation['id']}/").status_code, 404
            )
            self.assertEqual(
                self.client.post(BASE + f"reservations/{reservation['id']}/cancel/").status_code,
                404,
            )

    def test_cancellation_releases_seats_and_preserves_history(self):
        reservation = self.book().data
        url = BASE + f"reservations/{reservation['id']}/cancel/"
        for _ in range(2):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["status"], "cancelled")
            self.assertEqual(response.data["total_amount"], "12.50")
            self.assertFalse(response.data["tickets"][0]["is_active"])
        self.assertEqual(
            self.client.get(BASE + f"sessions/{self.session.pk}/availability/").data["available"],
            20,
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(self.book().status_code, 201)
        self.assertEqual(Ticket.objects.count(), 2)

    def test_cancellation_after_start_rejected(self):
        reservation = self.book().data
        ShowSession.objects.filter(pk=self.session.pk).update(
            show_time=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(
            self.client.post(BASE + f"reservations/{reservation['id']}/cancel/").status_code, 400
        )
        self.assertTrue(Ticket.objects.get().is_active)

    def test_reservation_update_and_delete_are_disabled(self):
        reservation = self.book().data
        url = BASE + f"reservations/{reservation['id']}/"
        self.assertEqual(self.client.patch(url, {"status": "cancelled"}).status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_booked_session_cannot_be_moved_or_repriced(self):
        self.book()
        self.client.force_authenticate(self.admin)
        url = BASE + f"sessions/{self.session.pk}/"
        for payload in ({"price": "999.00"}, {"show_time": self.start + timedelta(hours=3)}):
            self.assertEqual(self.client.patch(url, payload).status_code, 400)

    def test_existing_schedule_protects_duration_and_dome_layout(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.patch(BASE + f"shows/{self.show.pk}/", {"duration": 90}).status_code, 400
        )
        self.assertEqual(
            self.client.patch(BASE + f"domes/{self.dome.pk}/", {"rows": 2}).status_code, 400
        )

    def test_protected_catalogue_deletion_returns_validation_error(self):
        self.book()
        self.client.force_authenticate(self.admin)
        for resource, pk in (
            ("shows", self.show.pk),
            ("domes", self.dome.pk),
            ("sessions", self.session.pk),
        ):
            self.assertEqual(self.client.delete(BASE + f"{resource}/{pk}/").status_code, 400)

    def test_database_prevents_duplicate_active_seats(self):
        reservation = Reservation.objects.create(user=self.user)
        Ticket.objects.create(
            reservation=reservation,
            show_session=self.session,
            row=1,
            seat=1,
            unit_price=Decimal("12.50"),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.create(
                reservation=reservation,
                show_session=self.session,
                row=1,
                seat=1,
                unit_price=Decimal("12.50"),
            )

    def test_multi_session_reservation_is_atomic(self):
        other = ShowSession.objects.create(
            astronomy_show=self.show,
            planetarium_dome=self.dome,
            show_time=self.start + timedelta(hours=2),
            price="20.00",
        )
        response = self.book([self.ticket(), self.ticket(session=other)])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["total_amount"], "32.50")

    def test_pagination_and_public_documentation(self):
        AstronomyTheme.objects.bulk_create([AstronomyTheme(name=f"Theme {i}") for i in range(22)])
        response = self.client.get(BASE + "themes/")
        self.assertEqual(response.data["count"], 23)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertIsNotNone(response.data["next"])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)


@skipUnless(connection.vendor == "postgresql", "Real concurrency requires PostgreSQL row locks.")
class ConcurrentBookingTests(TransactionTestCase):
    def test_two_visitors_cannot_book_the_same_seat(self):
        users = [User.objects.create_user(f"user{i}@example.com", "password") for i in range(2)]
        show = AstronomyShow.objects.create(title="Concurrency", description="Test", duration=60)
        dome = PlanetariumDome.objects.create(name="Concurrent dome", rows=1, seats_in_row=1)
        session = ShowSession.objects.create(
            astronomy_show=show,
            planetarium_dome=dome,
            show_time=timezone.now() + timedelta(days=1),
            price="10.00",
        )
        barrier = Barrier(2)

        def reserve(user):
            try:
                barrier.wait(timeout=10)
                create_reservation(user, [{"show_session": session, "row": 1, "seat": 1}])
                return "booked"
            except ValidationError:
                return "unavailable"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, users))
        self.assertCountEqual(results, ["booked", "unavailable"])
        self.assertEqual(Ticket.objects.filter(is_active=True).count(), 1)
        self.assertEqual(Reservation.objects.count(), 1)
