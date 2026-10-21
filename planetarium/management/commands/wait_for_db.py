import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import InterfaceError, OperationalError


class Command(BaseCommand):
    help = "Wait for database queries to succeed."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=60)

    def handle(self, *args, **options):
        if options["timeout"] <= 0:
            raise CommandError("Timeout must be positive.")
        deadline = time.monotonic() + options["timeout"]
        database = connections["default"]
        self.stdout.write("Waiting for database...")
        while True:
            try:
                with database.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except (InterfaceError, OperationalError):
                database.close()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CommandError(
                        "Database is unavailable. Check configuration and credentials."
                    ) from None
                time.sleep(min(1, remaining))
            else:
                self.stdout.write(self.style.SUCCESS("Database available!"))
                return
