import datetime

import factory

from inventory.models import (
    Category,
    ProductType,
    PurchaseOrder,
    PurchaseOrderLineItem,
    SerializedItem,
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
