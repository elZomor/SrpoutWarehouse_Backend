from inventory.views.auth import LoginView, LogoutView, MeView
from inventory.views.category import CategoryViewSet
from inventory.views.product_type import ProductTypeViewSet
from inventory.views.purchase_order import PurchaseOrderViewSet
from inventory.views.serialized_item import SerializedItemViewSet
from inventory.views.work_order import WorkOrderViewSet

__all__ = [
    "LoginView",
    "LogoutView",
    "MeView",
    "CategoryViewSet",
    "ProductTypeViewSet",
    "PurchaseOrderViewSet",
    "SerializedItemViewSet",
    "WorkOrderViewSet",
]
