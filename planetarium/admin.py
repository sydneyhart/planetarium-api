from django.contrib import admin

from .models import AstronomyShow, AstronomyTheme, PlanetariumDome, Reservation, ShowSession, Ticket


@admin.register(AstronomyShow)
class AstronomyShowAdmin(admin.ModelAdmin):
    list_display = ["title", "duration", "age_limit"]
    search_fields = ["title"]
    filter_horizontal = ["themes"]

    def get_readonly_fields(self, request, obj=None):
        return ["duration"] if obj and obj.sessions.exists() else []


admin.site.register(AstronomyTheme)


@admin.register(PlanetariumDome)
class DomeAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return ["rows", "seats_in_row"] if obj and obj.sessions.exists() else []


@admin.register(ShowSession)
class SessionAdmin(admin.ModelAdmin):
    list_display = ["astronomy_show", "planetarium_dome", "show_time", "price"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
