from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from planetarium.models import AstronomyShow, AstronomyTheme, PlanetariumDome, ShowSession

DEMO_PASSWORD = "Planetarium-demo-2026!"


class Command(BaseCommand):
    help = "Create sample shows and future sessions; demo accounts require --with-users."

    def add_arguments(self, parser):
        parser.add_argument("--with-users", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        themes = [
            AstronomyTheme.objects.get_or_create(name=name)[0]
            for name in ["Solar system", "Deep space", "Space exploration"]
        ]
        dome, _ = PlanetariumDome.objects.get_or_create(
            name="Orion Dome", defaults={"rows": 8, "seats_in_row": 12}
        )
        tomorrow = (timezone.now() + timedelta(days=1)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
        for index, (title, description, duration) in enumerate(
            [
                (
                    "Journey to Mars",
                    "Travel across the red planet and discover the science of future missions.",
                    50,
                ),
                (
                    "Beyond the Milky Way",
                    "Explore distant galaxies, nebulae, and the expanding universe.",
                    60,
                ),
                (
                    "Our Cosmic Neighbourhood",
                    "A family-friendly tour of the Sun, planets, and moons.",
                    40,
                ),
            ]
        ):
            show, _ = AstronomyShow.objects.get_or_create(
                title=title, defaults={"description": description, "duration": duration}
            )
            show.themes.add(themes[index])
            ShowSession.objects.get_or_create(
                astronomy_show=show,
                planetarium_dome=dome,
                show_time=tomorrow + timedelta(hours=2 * index),
                defaults={"price": Decimal("15.00")},
            )
        if options["with_users"]:
            User = get_user_model()
            for email, staff in [("admin@example.com", True), ("visitor@example.com", False)]:
                if not User.objects.filter(email=email).exists():
                    User.objects.create_user(
                        email, DEMO_PASSWORD, is_staff=staff, is_superuser=staff, first_name="Demo"
                    )
            self.stdout.write("Local demo accounts: admin@example.com and visitor@example.com")
            self.stdout.write("Password: Planetarium-demo-2026! (existing passwords are unchanged)")
        self.stdout.write(self.style.SUCCESS("Demo shows and future sessions are ready."))
