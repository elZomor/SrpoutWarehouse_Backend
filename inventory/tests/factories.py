import factory

from inventory.models import Category, ProductType, SerializedItem


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
