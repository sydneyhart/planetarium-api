from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import EmailTokenSerializer, UserSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class LoginView(TokenObtainPairView):
    serializer_class = EmailTokenSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class RefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"
