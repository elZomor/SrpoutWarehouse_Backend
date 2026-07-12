from django.urls import path
from rest_framework.routers import DefaultRouter

from inventory.views import (
    CategoryViewSet,
    LoginView,
    LogoutView,
    MeView,
    ProductTypeViewSet,
)

router = DefaultRouter()
router.register("product-types", ProductTypeViewSet)
router.register("categories", CategoryViewSet)

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
] + router.urls
