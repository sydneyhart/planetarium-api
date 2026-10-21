from django.contrib import admin

from .models import AstronomyShow, AstronomyTheme, PlanetariumDome, Reservation, ShowSession, Ticket


@admin.register(AstronomyShow)
class AstronomyShowAdmin(admin.ModelAdmin):
    list_display = ["title", "duration", "age_limit"]
    search_fields = ["title"]
    filter_horizontal = ["themes"]


admin.site.register([AstronomyTheme, PlanetariumDome, ShowSession])


class TicketInline(admin.TabularInline):
    model = Ticket
    extra = 0
    can_delete = False
    readonly_fields = ["show_session", "row", "seat", "unit_price", "is_active"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "created_at", "status"]
    readonly_fields = ["user", "created_at", "status"]
    inlines = [TicketInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
