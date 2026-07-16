from django.contrib import admin

from inventory.models import (
    Category,
    ProductType,
    PurchaseOrder,
    PurchaseOrderLineItem,
    SerializedItem,
    WorkOrder,
    WorkOrderLineItem,
)


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "model_code", "category", "archived")
    list_filter = ("archived",)
    search_fields = ProductType.SEARCH_FIELDS


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "archived")
    list_filter = ("archived",)
    search_fields = Category.SEARCH_FIELDS


@admin.register(SerializedItem)
class SerializedItemAdmin(admin.ModelAdmin):
    list_display = ("serial_number", "product_type", "status", "serial")
    list_filter = ("status", "product_type")
    search_fields = SerializedItem.SEARCH_FIELDS


class PurchaseOrderLineItemInline(admin.TabularInline):
    model = PurchaseOrderLineItem
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier_name", "order_date", "status")
    list_filter = ("status",)
    search_fields = ("supplier_name",)
    inlines = [PurchaseOrderLineItemInline]


class WorkOrderLineItemInline(admin.TabularInline):
    model = WorkOrderLineItem
    extra = 0


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "job_name", "client_name", "expected_date_out", "status")
    list_filter = ("status",)
    search_fields = ("job_name", "client_name")
    inlines = [WorkOrderLineItemInline]
