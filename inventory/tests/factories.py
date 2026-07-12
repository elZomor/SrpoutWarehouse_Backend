import factory

from inventory.models import Category, ProductType


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
