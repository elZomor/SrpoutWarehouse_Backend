import factory

from inventory.models import Category, ProductType


class ProductTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductType

    name = factory.Sequence(lambda n: f"Product Type {n}")
    model_code = ""
    description = ""


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Category {n}")
    description = ""
