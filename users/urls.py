from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView

from .views import LoginView, MeView, RefreshView, RegisterView

app_name = "users"
urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("token/", LoginView.as_view(), name="token"),
    path("token/refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", TokenBlacklistView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
]
