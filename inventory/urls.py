from django.urls import path
from rest_framework.routers import DefaultRouter

from inventory.views import LoginView, LogoutView, MeView

router = DefaultRouter()

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
] + router.urls
