from django.urls import path
from rest_framework.routers import DefaultRouter

from inventory.views import (
    BoxViewSet,
    CategoryViewSet,
    LoginView,
    LogoutView,
    MeView,
    ProductTypeViewSet,
    PurchaseOrderViewSet,
    SerializedItemViewSet,
    TransactionViewSet,
    WorkOrderViewSet,
)

router = DefaultRouter()
router.register("product-types", ProductTypeViewSet)
router.register("categories", CategoryViewSet)
router.register("serialized-items", SerializedItemViewSet)
router.register("purchase-orders", PurchaseOrderViewSet)
router.register("work-orders", WorkOrderViewSet)
router.register("transactions", TransactionViewSet)
router.register("boxes", BoxViewSet)

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
] + router.urls
