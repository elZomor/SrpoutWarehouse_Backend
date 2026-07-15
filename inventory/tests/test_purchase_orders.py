from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models.query import QuerySet
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import PurchaseOrder, SerializedItem
from inventory.tests.factories import (
    ProductTypeFactory,
    PurchaseOrderFactory,
    PurchaseOrderLineItemFactory,
)


class PurchaseOrderTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_create_purchase_order_with_one_line_item(self):
        # TC-01/AC-1: a PO with supplier, date, and one line item is saved
        # with status "pending".
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["supplier_name"], "Acme Lighting Co")
        self.assertEqual(response.data["order_date"], "2026-07-01")
        self.assertEqual(response.data["status"], PurchaseOrder.STATUS_PENDING)
        self.assertEqual(len(response.data["line_items"]), 1)
        self.assertEqual(
            response.data["line_items"][0]["product_type"], self.product_type.id
        )
        self.assertEqual(response.data["line_items"][0]["expected_quantity"], 5)

    def test_create_purchase_order_with_multiple_line_items(self):
        # TC-02: a PO with 3 line items for different product types is
        # created with all 3, each with its own expected quantity.
        other_type = ProductTypeFactory()
        third_type = ProductTypeFactory()

        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5},
                    {"product_type": other_type.id, "expected_quantity": 2},
                    {"product_type": third_type.id, "expected_quantity": 10},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["line_items"]), 3)
        quantities_by_product_type = {
            item["product_type"]: item["expected_quantity"]
            for item in response.data["line_items"]
        }
        self.assertEqual(quantities_by_product_type[self.product_type.id], 5)
        self.assertEqual(quantities_by_product_type[other_type.id], 2)
        self.assertEqual(quantities_by_product_type[third_type.id], 10)

    def test_created_purchase_order_is_persisted_with_its_line_items(self):
        self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        purchase_order = PurchaseOrder.objects.get()
        self.assertEqual(purchase_order.supplier_name, "Acme Lighting Co")
        self.assertEqual(purchase_order.line_items.count(), 1)
        self.assertEqual(purchase_order.line_items.get().expected_quantity, 5)

    def test_response_includes_product_type_name_per_line_item(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(
            response.data["line_items"][0]["product_type_name"],
            self.product_type.name,
        )

    def test_create_purchase_order_without_supplier_name_is_rejected(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["supplier_name"], ["Supplier name is required."])

    def test_create_purchase_order_without_order_date_is_rejected(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("order_date", response.data)

    def test_create_purchase_order_without_line_items_is_rejected(self):
        # AC-1: "one or more line items" - an empty list isn't a valid PO.
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_create_purchase_order_line_item_without_product_type_is_rejected(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [{"expected_quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)

    def test_create_purchase_order_line_item_without_expected_quantity_is_rejected(
        self,
    ):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [{"product_type": self.product_type.id}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)

    def test_status_cannot_be_overridden_on_create(self):
        # status is a read_only field - AC-1 guarantees every new PO is
        # "pending" regardless of what the client sends.
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "status": "received",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], PurchaseOrder.STATUS_PENDING)

    def test_created_purchase_order_appears_in_list(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )
        created_id = response.data["id"]

        list_response = self.client.get(reverse("purchaseorder-list"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(created_id, [item["id"] for item in list_response.data])
        listed = next(item for item in list_response.data if item["id"] == created_id)
        self.assertEqual(len(listed["line_items"]), 1)

    def test_retrieve_update_destroy_routes_are_not_registered(self):
        # WRH-29 only scopes create+list - retrieve/update/destroy are
        # separate, unscoped stories, so no detail route exists at all yet.
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )
        detail_url = f"/api/purchase-orders/{response.data['id']}/"

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )


class PurchaseOrderReceiveTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()
        self.purchase_order = PurchaseOrderFactory()
        self.line_item = PurchaseOrderLineItemFactory(
            purchase_order=self.purchase_order,
            product_type=self.product_type,
            expected_quantity=2,
        )

    def receive(self, line_item, serial_number):
        return self.client.post(
            reverse("purchaseorder-receive", args=[self.purchase_order.id]),
            {"line_item": line_item.id, "serial_number": serial_number},
            format="json",
        )

    def test_receive_creates_available_serialized_item_linked_to_po(self):
        # AC-1: a scanned serial becomes a new "available" SerializedItem
        # linked to this PO's line item.
        response = self.receive(self.line_item, "SN-1001")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = SerializedItem.objects.get(serial_number="SN-1001")
        self.assertEqual(item.status, SerializedItem.STATUS_AVAILABLE)
        self.assertEqual(item.product_type_id, self.product_type.id)
        self.assertEqual(item.purchase_order_line_item_id, self.line_item.id)

    def test_receiving_full_expected_quantity_marks_po_received(self):
        # AC-1/TC-01
        self.receive(self.line_item, "SN-1001")
        response = self.receive(self.line_item, "SN-1002")

        self.assertEqual(response.data["status"], PurchaseOrder.STATUS_RECEIVED)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_RECEIVED)

    def test_partial_receipt_marks_po_partially_received_with_remaining_qty(self):
        # AC-3/TC-03
        line_item = PurchaseOrderLineItemFactory(
            purchase_order=self.purchase_order,
            product_type=self.product_type,
            expected_quantity=50,
        )
        for n in range(29):
            self.receive(line_item, f"SN-P{n:03d}")

        response = self.receive(line_item, "SN-P029")

        self.assertEqual(
            response.data["status"], PurchaseOrder.STATUS_PARTIALLY_RECEIVED
        )
        received_line_item = next(
            li for li in response.data["line_items"] if li["id"] == line_item.id
        )
        self.assertEqual(received_line_item["received_quantity"], 30)
        self.assertEqual(received_line_item["remaining_quantity"], 20)

    def test_completing_a_partial_receipt_marks_po_received(self):
        # AC-4/TC-04
        purchase_order = PurchaseOrderFactory()
        line_item = PurchaseOrderLineItemFactory(
            purchase_order=purchase_order,
            product_type=self.product_type,
            expected_quantity=2,
        )
        response = self.client.post(
            reverse("purchaseorder-receive", args=[purchase_order.id]),
            {"line_item": line_item.id, "serial_number": "SN-C001"},
            format="json",
        )
        self.assertEqual(
            response.data["status"], PurchaseOrder.STATUS_PARTIALLY_RECEIVED
        )

        response = self.client.post(
            reverse("purchaseorder-receive", args=[purchase_order.id]),
            {"line_item": line_item.id, "serial_number": "SN-C002"},
            format="json",
        )

        self.assertEqual(response.data["status"], PurchaseOrder.STATUS_RECEIVED)

    def test_multiple_line_items_mixed_completion_stays_partially_received(self):
        # AC-5/TC-05
        other_line_item = PurchaseOrderLineItemFactory(
            purchase_order=self.purchase_order,
            product_type=ProductTypeFactory(),
            expected_quantity=2,
        )
        self.receive(self.line_item, "SN-M001")
        self.receive(self.line_item, "SN-M002")

        response = self.receive(other_line_item, "SN-M003")

        self.assertEqual(
            response.data["status"], PurchaseOrder.STATUS_PARTIALLY_RECEIVED
        )

    def test_receive_rejects_line_item_from_a_different_purchase_order(self):
        other_po_line_item = PurchaseOrderLineItemFactory(
            product_type=self.product_type, expected_quantity=1
        )

        response = self.receive(other_po_line_item, "SN-X001")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_item", response.data)
        self.assertFalse(
            SerializedItem.objects.filter(serial_number="SN-X001").exists()
        )

    def test_receive_rejects_archived_product_type(self):
        # Mirrors WRH-21/AC-6's guard on the generic register flow - an
        # archived product type shouldn't be able to receive new stock
        # through the PO receive path either.
        self.product_type.archived = True
        self.product_type.save(update_fields=["archived"])

        response = self.receive(self.line_item, "SN-ARCH001")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_item", response.data)
        self.assertFalse(
            SerializedItem.objects.filter(serial_number="SN-ARCH001").exists()
        )

    def test_receive_rejects_scan_past_expected_quantity(self):
        # A line item that has already received its full expected_quantity
        # shouldn't silently accept further scans (over-receiving).
        self.receive(self.line_item, "SN-CAP001")
        self.receive(self.line_item, "SN-CAP002")

        response = self.receive(self.line_item, "SN-CAP003")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_item", response.data)
        self.assertFalse(
            SerializedItem.objects.filter(serial_number="SN-CAP003").exists()
        )

    def test_receive_rechecks_archived_status_after_lock_is_acquired(self):
        # PurchaseOrderReceiveSerializer's queryset filter only catches an
        # already-archived product type - it can't catch one archived in
        # the window between that validation and receive()'s
        # select_for_update()-guarded re-fetch. Simulate a concurrent
        # archive landing in exactly that window by hooking the first
        # select_for_update() call inside the atomic block (receive()'s
        # only call site for it).
        original_select_for_update = QuerySet.select_for_update
        product_type = self.product_type

        def archive_then_lock(self, *args, **kwargs):
            product_type.archived = True
            product_type.save(update_fields=["archived"])
            return original_select_for_update(self, *args, **kwargs)

        with patch.object(QuerySet, "select_for_update", archive_then_lock):
            response = self.receive(self.line_item, "SN-RACE001")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_item", response.data)
        self.assertFalse(
            SerializedItem.objects.filter(serial_number="SN-RACE001").exists()
        )

    def test_receive_rejects_duplicate_serial_number(self):
        SerializedItem.objects.create(
            serial_number="SN-DUP", product_type=self.product_type
        )

        response = self.receive(self.line_item, "SN-DUP")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)

    def test_receive_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.receive(self.line_item, "SN-1001")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PurchaseOrderRecomputeStatusTests(APITestCase):
    def test_recompute_status_on_a_po_with_no_line_items_stays_pending(self):
        # No API path can produce this today (create() requires at least
        # one line item, and there's no line-item-delete route), but
        # Django admin's inline can delete every line item off a saved PO -
        # recompute_status() must not treat an empty PO as "received" just
        # because all([]) is vacuously True.
        purchase_order = PurchaseOrderFactory()
        line_item = PurchaseOrderLineItemFactory(purchase_order=purchase_order)
        line_item.delete()

        purchase_order.recompute_status()

        self.assertEqual(purchase_order.status, PurchaseOrder.STATUS_PENDING)
