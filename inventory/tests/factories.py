import datetime

import factory
from django.contrib.auth.models import User

from inventory.models import (
    Box,
    Category,
    DamageReport,
    MaintenanceOrder,
    ProductType,
    PurchaseOrder,
    PurchaseOrderLineItem,
    SerializedItem,
    Transaction,
    WorkOrder,
    WorkOrderLineItem,
)


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Category {n}")
    description = ""


class ProductTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductType

    name = factory.Sequence(lambda n: f"Product Type {n}")
    model_code = ""
    description = ""
    category = factory.SubFactory(CategoryFactory)


class SerializedItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SerializedItem

    serial_number = factory.Sequence(lambda n: f"SN-{n:04d}")
    product_type = factory.SubFactory(ProductTypeFactory)
    notes = ""


class PurchaseOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PurchaseOrder

    supplier_name = factory.Sequence(lambda n: f"Supplier {n}")
    order_date = datetime.date(2026, 1, 1)


class PurchaseOrderLineItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PurchaseOrderLineItem

    purchase_order = factory.SubFactory(PurchaseOrderFactory)
    product_type = factory.SubFactory(ProductTypeFactory)
    expected_quantity = 1


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.Sequence(lambda n: f"user{n}@example.com")


class WorkOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WorkOrder

    job_name = factory.Sequence(lambda n: f"Job {n}")
    client_name = ""
    expected_date_out = datetime.date(2026, 1, 1)
    created_by = factory.SubFactory(UserFactory)


class WorkOrderLineItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WorkOrderLineItem

    work_order = factory.SubFactory(WorkOrderFactory)
    product_type = factory.SubFactory(ProductTypeFactory)
    quantity = 1


class BoxFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Box

    code = factory.Sequence(lambda n: f"BX-{n:04d}")
    product_type = factory.SubFactory(ProductTypeFactory)


class TransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Transaction

    transaction_type = Transaction.TYPE_RECEIVE
    serialized_item = factory.SubFactory(SerializedItemFactory)
    user = factory.SubFactory(UserFactory)
    reference_number = ""
    note = ""


class DamageReportFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DamageReport

    serialized_item = factory.SubFactory(SerializedItemFactory)
    user = factory.SubFactory(UserFactory)
    note = ""


class MaintenanceOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MaintenanceOrder
