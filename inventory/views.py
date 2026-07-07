from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from rest_framework import status, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import ProductType
from inventory.serializers import LoginSerializer, ProductTypeSerializer, UserSerializer


class LoginView(APIView):
    # No session exists yet, so the browser has no CSRF cookie to send back -
    # SessionAuthentication.enforce_csrf() would otherwise 403 this request
    # anyway, since AnonymousUser.is_active is True and it runs the check
    # regardless of whether anyone is actually logged in. Login itself
    # doesn't need an authenticator.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            username = User.objects.get(
                email__iexact=serializer.validated_data["email"]
            ).username
        except User.DoesNotExist:
            username = None

        user = None
        if username is not None:
            user = authenticate(
                request,
                username=username,
                password=serializer.validated_data["password"],
            )

        if user is None:
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        login(request, user)
        # Ensure the CSRF cookie is issued alongside the session cookie, so the
        # SPA can send X-CSRFToken on subsequent authenticated, unsafe requests.
        get_token(request)
        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ProductTypeViewSet(viewsets.ModelViewSet):
    queryset = ProductType.objects.all().order_by("name")
    serializer_class = ProductTypeSerializer
    filter_backends = [SearchFilter]
    search_fields = ["name", "model_code"]
