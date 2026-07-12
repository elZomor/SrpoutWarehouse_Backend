from django.contrib import admin

from inventory.models import Category, ProductType


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "model_code", "category")
    search_fields = ProductType.SEARCH_FIELDS


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = Category.SEARCH_FIELDS
