from inventory.views.auth import LoginView, LogoutView, MeView
from inventory.views.box import BoxViewSet
from inventory.views.category import CategoryViewSet
from inventory.views.damage_report import DamageReportViewSet
from inventory.views.missing_item import MissingItemViewSet
from inventory.views.product_type import ProductTypeViewSet
from inventory.views.purchase_order import PurchaseOrderViewSet
from inventory.views.serialized_item import SerializedItemViewSet
from inventory.views.transaction import TransactionViewSet
from inventory.views.work_order import WorkOrderViewSet

__all__ = [
    "LoginView",
    "LogoutView",
    "MeView",
    "BoxViewSet",
    "CategoryViewSet",
    "DamageReportViewSet",
    "MissingItemViewSet",
    "ProductTypeViewSet",
    "PurchaseOrderViewSet",
    "SerializedItemViewSet",
    "TransactionViewSet",
    "WorkOrderViewSet",
]
