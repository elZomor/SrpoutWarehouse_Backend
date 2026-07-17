from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SerializedItem, WorkOrder
from inventory.tests.factories import (
    ProductTypeFactory,
    SerializedItemFactory,
    WorkOrderFactory,
    WorkOrderLineItemFactory,
)


class WorkOrderTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_create_work_order_with_one_line_item(self):
        # TC-01/AC-1: a WO with job name, date, and one line item is saved
        # with status "draft" and the creating user recorded.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["job_name"], "Summer Gala")
        self.assertEqual(response.data["expected_date_out"], "2026-08-01")
        self.assertEqual(response.data["status"], WorkOrder.STATUS_DRAFT)
        self.assertEqual(response.data["created_by"], self.user.id)
        self.assertEqual(response.data["created_by_username"], "jane")
        self.assertEqual(len(response.data["line_items"]), 1)
        self.assertEqual(
            response.data["line_items"][0]["product_type"], self.product_type.id
        )
        self.assertEqual(response.data["line_items"][0]["quantity"], 5)

    def test_create_work_order_with_client_name(self):
        # TC-02: optional client name is saved and returned when provided.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "client_name": "Acme Events",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["client_name"], "Acme Events")

    def test_create_work_order_without_client_name(self):
        # AC-1: client name is optional.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["client_name"], "")

    def test_create_work_order_with_multiple_line_items(self):
        other_type = ProductTypeFactory()
        third_type = ProductTypeFactory()

        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [
                    {"product_type": self.product_type.id, "quantity": 5},
                    {"product_type": other_type.id, "quantity": 2},
                    {"product_type": third_type.id, "quantity": 10},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["line_items"]), 3)
        quantities_by_product_type = {
            item["product_type"]: item["quantity"]
            for item in response.data["line_items"]
        }
        self.assertEqual(quantities_by_product_type[self.product_type.id], 5)
        self.assertEqual(quantities_by_product_type[other_type.id], 2)
        self.assertEqual(quantities_by_product_type[third_type.id], 10)

    def test_created_work_order_is_persisted_with_its_line_items(self):
        self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        work_order = WorkOrder.objects.get()
        self.assertEqual(work_order.job_name, "Summer Gala")
        self.assertEqual(work_order.created_by, self.user)
        self.assertEqual(work_order.line_items.count(), 1)
        self.assertEqual(work_order.line_items.get().quantity, 5)

    def test_response_includes_product_type_name_per_line_item(self):
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(
            response.data["line_items"][0]["product_type_name"],
            self.product_type.name,
        )

    def test_create_work_order_without_job_name_is_rejected(self):
        # TC-02/AC-1: field validation itself is refined in WRH-32, but the
        # underlying model field is still non-blank by default.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("job_name", response.data)

    def test_create_work_order_without_expected_date_out_is_rejected(self):
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expected_date_out", response.data)

    def test_create_work_order_rejects_archived_product_type(self):
        # Mirrors WRH-21/AC-6's guard on the generic register flow and
        # PurchaseOrderReceiveSerializer's identical restriction - an
        # archived product type shouldn't be requestable on a new WO.
        self.product_type.archived = True
        self.product_type.save(update_fields=["archived"])

        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_create_work_order_without_line_items_is_rejected(self):
        # AC-1: "with job name, ... and line items" - an empty list isn't a
        # valid WO.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_create_work_order_rejects_zero_quantity_line_item(self):
        # WRH-32/AC-4/TC-04: zero isn't a positive quantity.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 0}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            str(response.data["line_items"][0]["quantity"][0]),
            "Quantity must be greater than zero.",
        )
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_create_work_order_rejects_negative_quantity_line_item(self):
        # WRH-32/AC-4/TC-05 (boundary).
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": -5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            str(response.data["line_items"][0]["quantity"][0]),
            "Quantity must be greater than zero.",
        )
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_create_work_order_rejects_missing_product_type_on_line_item(self):
        # WRH-32/AC-5/TC-06.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            str(response.data["line_items"][0]["product_type"][0]),
            "Product type is required.",
        )
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_create_work_order_rejects_null_product_type_on_line_item(self):
        # WRH-32/AC-5: explicit null must match the same spec'd message as
        # an omitted key, not DRF's generic "may not be null" default.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": None, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            str(response.data["line_items"][0]["product_type"][0]),
            "Product type is required.",
        )
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_create_work_order_rejects_missing_quantity_on_line_item(self):
        # WRH-32/AC-4: an omitted quantity key must match the same spec'd
        # message as an out-of-range value, not DRF's generic default.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            str(response.data["line_items"][0]["quantity"][0]),
            "Quantity must be greater than zero.",
        )
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_status_cannot_be_overridden_on_create(self):
        # status is a read_only field - AC-1 guarantees every new WO is
        # "draft" regardless of what the client sends.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "status": "fulfilled",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_DRAFT)

    def test_created_by_cannot_be_overridden_on_create(self):
        # AC-1: "the creating user is recorded" - always the authenticated
        # requester, never a client-supplied value.
        other_user = User.objects.create_user(username="bob", password="pw")

        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "created_by": other_user.id,
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["created_by"], self.user.id)

    def test_created_work_order_appears_in_list(self):
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )
        created_id = response.data["id"]

        list_response = self.client.get(reverse("workorder-list"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(created_id, [item["id"] for item in list_response.data])
        listed = next(item for item in list_response.data if item["id"] == created_id)
        self.assertEqual(listed["job_name"], "Summer Gala")
        self.assertEqual(len(listed["line_items"]), 1)

    def test_update_destroy_routes_are_not_registered(self):
        # WRH-55 adds retrieve (AC-3) but update/destroy remain separate,
        # unscoped stories - PUT/DELETE on the detail route still 405.
        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )
        detail_url = f"/api/work-orders/{response.data['id']}/"

        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.put(detail_url, {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_create_work_order_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("workorder-list"),
            {
                "job_name": "Summer Gala",
                "expected_date_out": "2026-08-01",
                "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class WorkOrderStartFulfillmentTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.work_order = WorkOrderFactory()
        WorkOrderLineItemFactory(work_order=self.work_order)

    def start(self):
        return self.client.post(reverse("workorder-start", args=[self.work_order.id]))

    def test_start_moves_draft_wo_to_in_progress(self):
        # AC-1/TC-01
        response = self.start()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_IN_PROGRESS)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.status, WorkOrder.STATUS_IN_PROGRESS)

    def test_start_rejects_a_wo_that_is_already_in_progress(self):
        self.start()

        response = self.start()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_start_rejects_a_fulfilled_wo(self):
        self.work_order.status = WorkOrder.STATUS_FULFILLED
        self.work_order.save(update_fields=["status"])

        response = self.start()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_start_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.start()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class WorkOrderScanTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()
        self.work_order = WorkOrderFactory(status=WorkOrder.STATUS_IN_PROGRESS)
        self.line_item = WorkOrderLineItemFactory(
            work_order=self.work_order,
            product_type=self.product_type,
            quantity=3,
        )

    def scan(self, line_item, serial_number):
        return self.client.post(
            reverse("workorder-scan", args=[self.work_order.id]),
            {"line_item": line_item.id, "serial_number": serial_number},
            format="json",
        )

    def test_scan_reserves_an_available_item_matching_the_line_items_product_type(self):
        # AC-2
        item = SerializedItemFactory(product_type=self.product_type)

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_RESERVED)
        self.assertEqual(item.work_order_line_item_id, self.line_item.id)

    def test_live_counter_updates_as_items_are_scanned(self):
        # AC-2/TC-02
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(2)
        ]

        self.scan(self.line_item, items[0].serial_number)
        response = self.scan(self.line_item, items[1].serial_number)

        line_item_data = next(
            li for li in response.data["line_items"] if li["id"] == self.line_item.id
        )
        self.assertEqual(line_item_data["scanned_quantity"], 2)
        self.assertEqual(line_item_data["remaining_quantity"], 1)

    def test_scan_rejects_a_nonexistent_serial_number(self):
        # AC-4/TC-04
        response = self.scan(self.line_item, "SN-DOES-NOT-EXIST")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["serial_number"], ["Serial not found"])

    def test_scan_rejects_an_item_with_a_different_product_type(self):
        other_type = ProductTypeFactory()
        item = SerializedItemFactory(product_type=other_type)

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_AVAILABLE)

    def test_scan_rejects_an_item_already_reserved(self):
        item = SerializedItemFactory(product_type=self.product_type)
        self.scan(self.line_item, item.serial_number)

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)

    def test_scan_rejects_an_item_already_out(self):
        # AC-1/TC-01: rejected with the specific "currently out on WO-N"
        # error, naming the *other* WO it's already out on.
        other_work_order = WorkOrderFactory()
        other_line_item = WorkOrderLineItemFactory(
            work_order=other_work_order, product_type=self.product_type
        )
        item = SerializedItemFactory(
            product_type=self.product_type,
            status=SerializedItem.STATUS_OUT,
            work_order_line_item=other_line_item,
        )

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["serial_number"],
            [f"{item.serial_number} is currently out on" f" WO-{other_work_order.id}"],
        )
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_OUT)

    def test_scan_rejects_a_damaged_item(self):
        # AC-3/TC-03
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_DAMAGED
        )

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["serial_number"],
            [f"{item.serial_number} is damaged and cannot be issued"],
        )

    def test_scan_rejects_a_missing_item(self):
        # AC-3/TC-03
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_MISSING
        )

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["serial_number"],
            [f"{item.serial_number} is missing and cannot be issued"],
        )

    def test_scan_rejects_scan_past_requested_quantity(self):
        # AC-2/AC-4: self.line_item has quantity=3 - a 4th distinct,
        # otherwise-valid scan must not silently over-fulfil it, matching
        # PurchaseOrderViewSet.receive()'s identical WRH-30/AC-4 guard.
        for _ in range(3):
            item = SerializedItemFactory(product_type=self.product_type)
            self.scan(self.line_item, item.serial_number)
        extra_item = SerializedItemFactory(product_type=self.product_type)

        response = self.scan(self.line_item, extra_item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_item", response.data)
        extra_item.refresh_from_db()
        self.assertEqual(extra_item.status, SerializedItem.STATUS_AVAILABLE)

    def test_scan_rejects_a_line_item_from_a_different_work_order(self):
        other_line_item = WorkOrderLineItemFactory(product_type=self.product_type)
        item = SerializedItemFactory(product_type=self.product_type)

        response = self.scan(other_line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_item", response.data)

    def test_scan_rejects_when_the_work_order_has_not_been_started(self):
        draft_work_order = WorkOrderFactory()
        draft_line_item = WorkOrderLineItemFactory(
            work_order=draft_work_order, product_type=self.product_type
        )
        item = SerializedItemFactory(product_type=self.product_type)

        response = self.client.post(
            reverse("workorder-scan", args=[draft_work_order.id]),
            {"line_item": draft_line_item.id, "serial_number": item.serial_number},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def _assert_scan_blocked_for_status(self, wo_status):
        # AC-5/TC-05: fulfillment scanning only applies to draft ->
        # in_progress WOs - "returned"/"closed" aren't reachable through any
        # status transition yet (no return/close flow exists - see
        # WorkOrder.STATUS_CHOICES's comment), so they're set directly here
        # the same way the model itself would tolerate a value outside
        # STATUS_CHOICES (Django doesn't enforce choices at save() time).
        work_order = WorkOrderFactory(status=wo_status)
        line_item = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type
        )
        item = SerializedItemFactory(product_type=self.product_type)

        response = self.client.post(
            reverse("workorder-scan", args=[work_order.id]),
            {"line_item": line_item.id, "serial_number": item.serial_number},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_AVAILABLE)

    def test_scan_rejects_when_work_order_is_fulfilled(self):
        self._assert_scan_blocked_for_status(WorkOrder.STATUS_FULFILLED)

    def test_scan_rejects_when_work_order_is_returned(self):
        self._assert_scan_blocked_for_status("returned")

    def test_scan_rejects_when_work_order_is_closed(self):
        self._assert_scan_blocked_for_status("closed")

    def test_scan_requires_authentication(self):
        self.client.force_authenticate(user=None)
        item = SerializedItemFactory(product_type=self.product_type)

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_scan_rechecks_work_order_status_after_the_lock_is_acquired(self):
        # Simulates a concurrent complete() landing in the window between
        # scan()'s pre-lock status check and its select_for_update() -
        # matches the same race-simulation technique as
        # PurchaseOrderReceiveTests.test_receive_rechecks_archived_status_after_lock_is_acquired.
        original_select_for_update = WorkOrder.objects.select_for_update
        work_order = self.work_order

        def fulfill_then_lock(*args, **kwargs):
            work_order.status = WorkOrder.STATUS_FULFILLED
            work_order.save(update_fields=["status"])
            return original_select_for_update(*args, **kwargs)

        item = SerializedItemFactory(product_type=self.product_type)
        with patch.object(WorkOrder.objects, "select_for_update", fulfill_then_lock):
            response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_AVAILABLE)


class WorkOrderCompleteFulfillmentTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()
        self.work_order = WorkOrderFactory(status=WorkOrder.STATUS_IN_PROGRESS)
        self.line_item = WorkOrderLineItemFactory(
            work_order=self.work_order,
            product_type=self.product_type,
            quantity=2,
        )

    def scan(self, line_item, serial_number):
        return self.client.post(
            reverse("workorder-scan", args=[self.work_order.id]),
            {"line_item": line_item.id, "serial_number": serial_number},
            format="json",
        )

    def complete(self):
        return self.client.post(
            reverse("workorder-complete", args=[self.work_order.id])
        )

    def test_complete_rejects_when_a_line_item_is_not_fully_scanned(self):
        item = SerializedItemFactory(product_type=self.product_type)
        self.scan(self.line_item, item.serial_number)

        response = self.complete()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.status, WorkOrder.STATUS_IN_PROGRESS)

    def test_complete_marks_wo_fulfilled_and_scanned_items_out(self):
        # AC-4/TC-04
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(2)
        ]
        for item in items:
            self.scan(self.line_item, item.serial_number)

        response = self.complete()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_FULFILLED)
        for item in items:
            item.refresh_from_db()
            self.assertEqual(item.status, SerializedItem.STATUS_OUT)
            self.assertEqual(item.last_work_order_reference, str(self.work_order))

    def test_complete_requires_every_line_item_to_reach_its_quantity_independently(
        self,
    ):
        # AC-5/TC-05
        other_type = ProductTypeFactory()
        other_line_item = WorkOrderLineItemFactory(
            work_order=self.work_order, product_type=other_type, quantity=1
        )
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(2)
        ]
        for item in items:
            self.scan(self.line_item, item.serial_number)

        response = self.complete()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        other_item = SerializedItemFactory(product_type=other_type)
        self.scan(other_line_item, other_item.serial_number)
        response = self.complete()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_FULFILLED)

    def test_complete_rejects_a_draft_work_order(self):
        draft_work_order = WorkOrderFactory()

        response = self.client.post(
            reverse("workorder-complete", args=[draft_work_order.id])
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_complete_rejects_an_already_fulfilled_work_order(self):
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(2)
        ]
        for item in items:
            self.scan(self.line_item, item.serial_number)
        self.complete()

        response = self.complete()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_complete_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.complete()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class WorkOrderReturnItemTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()
        self.work_order = WorkOrderFactory(status=WorkOrder.STATUS_FULFILLED)
        self.line_item = WorkOrderLineItemFactory(
            work_order=self.work_order,
            product_type=self.product_type,
            quantity=2,
        )

    def return_item(self, serial_number):
        return self.client.post(
            reverse("workorder-return-item", args=[self.work_order.id]),
            {"serial_number": serial_number},
            format="json",
        )

    def _out_item(self):
        return SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=self.line_item,
            status=SerializedItem.STATUS_OUT,
        )

    def test_return_all_issued_items_marks_wo_returned(self):
        # AC-1/TC-01
        items = [self._out_item() for _ in range(2)]

        self.return_item(items[0].serial_number)
        response = self.return_item(items[1].serial_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_RETURNED)
        for item in items:
            item.refresh_from_db()
            self.assertEqual(item.status, SerializedItem.STATUS_AVAILABLE)
        line_item_data = response.data["line_items"][0]
        self.assertEqual(line_item_data["returned_quantity"], 2)
        self.assertEqual(line_item_data["still_out_quantity"], 0)

    def test_partial_return_marks_wo_partially_returned(self):
        # AC-2/TC-02
        items = [self._out_item() for _ in range(2)]

        response = self.return_item(items[0].serial_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_PARTIALLY_RETURNED)
        items[0].refresh_from_db()
        items[1].refresh_from_db()
        self.assertEqual(items[0].status, SerializedItem.STATUS_AVAILABLE)
        self.assertEqual(items[1].status, SerializedItem.STATUS_OUT)
        line_item_data = response.data["line_items"][0]
        self.assertEqual(line_item_data["returned_quantity"], 1)
        self.assertEqual(line_item_data["still_out_quantity"], 1)

    def test_completing_a_partially_returned_wo_reaches_returned(self):
        # AC-4/TC-04
        items = [self._out_item() for _ in range(2)]
        self.return_item(items[0].serial_number)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.status, WorkOrder.STATUS_PARTIALLY_RETURNED)

        response = self.return_item(items[1].serial_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_RETURNED)

    def test_return_rejects_a_nonexistent_serial_number(self):
        response = self.return_item("SN-DOES-NOT-EXIST")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["serial_number"], ["Serial not found"])

    def test_return_rejects_an_item_not_currently_out(self):
        item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=self.line_item,
            status=SerializedItem.STATUS_AVAILABLE,
        )

        response = self.return_item(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)

    def test_return_rejects_an_item_issued_on_a_different_work_order(self):
        other_work_order = WorkOrderFactory(status=WorkOrder.STATUS_FULFILLED)
        other_line_item = WorkOrderLineItemFactory(
            work_order=other_work_order, product_type=self.product_type
        )
        item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=other_line_item,
            status=SerializedItem.STATUS_OUT,
        )

        response = self.return_item(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_OUT)

    def test_return_rejects_when_work_order_is_draft(self):
        draft_work_order = WorkOrderFactory()
        line_item = WorkOrderLineItemFactory(
            work_order=draft_work_order, product_type=self.product_type
        )
        item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_OUT,
        )

        response = self.client.post(
            reverse("workorder-return-item", args=[draft_work_order.id]),
            {"serial_number": item.serial_number},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_return_rejects_when_work_order_already_returned(self):
        self.work_order.status = WorkOrder.STATUS_RETURNED
        self.work_order.save(update_fields=["status"])
        item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=self.line_item,
            status=SerializedItem.STATUS_OUT,
        )

        response = self.return_item(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_return_requires_authentication(self):
        self.client.force_authenticate(user=None)
        item = self._out_item()

        response = self.return_item(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_return_rechecks_work_order_status_after_the_lock_is_acquired(self):
        # Simulates a concurrent second return() call landing in the window
        # between the pre-lock status check and select_for_update() -
        # matches WorkOrderScanTests's identical race-simulation technique.
        original_select_for_update = WorkOrder.objects.select_for_update
        work_order = self.work_order

        def return_then_lock(*args, **kwargs):
            work_order.status = WorkOrder.STATUS_RETURNED
            work_order.save(update_fields=["status"])
            return original_select_for_update(*args, **kwargs)

        item = self._out_item()
        with patch.object(WorkOrder.objects, "select_for_update", return_then_lock):
            response = self.return_item(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_OUT)


class WorkOrderActiveListTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_active_list_is_empty_when_no_work_orders_exist(self):
        # TC-04/AC-4: empty state, not an error.
        response = self.client.get(reverse("workorder-active"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_active_list_nests_supplementaries_beneath_their_primary(self):
        # TC-01/AC-1: Primary + 2 supplementaries - supplementaries nested
        # beneath their Primary, not returned as their own top-level rows.
        primary = WorkOrderFactory(created_by=self.user, job_name="Primary Job")
        supplementary_1 = WorkOrderFactory(
            created_by=self.user, job_name="Supp 1", parent_work_order=primary
        )
        supplementary_2 = WorkOrderFactory(
            created_by=self.user, job_name="Supp 2", parent_work_order=primary
        )

        response = self.client.get(reverse("workorder-active"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], primary.id)
        supplementary_ids = {s["id"] for s in response.data[0]["supplementaries"]}
        self.assertEqual(supplementary_ids, {supplementary_1.id, supplementary_2.id})

    def test_active_list_reports_per_type_returned_vs_still_out_counts(self):
        # TC-02/AC-2: each product type on the row shows returned/still-out
        # counts, derived from SerializedItem.status (not scan progress).
        work_order = WorkOrderFactory(
            created_by=self.user, status=WorkOrder.STATUS_FULFILLED
        )
        line_item = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type, quantity=3
        )
        out_items = [
            SerializedItemFactory(
                product_type=self.product_type,
                work_order_line_item=line_item,
                status=SerializedItem.STATUS_OUT,
            )
            for _ in range(2)
        ]
        SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_AVAILABLE,
        )

        response = self.client.get(reverse("workorder-active"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row_line_item = response.data[0]["line_items"][0]
        self.assertEqual(row_line_item["still_out_quantity"], len(out_items))
        self.assertEqual(row_line_item["returned_quantity"], 1)

    def test_active_list_excludes_supplementaries_from_the_top_level(self):
        primary = WorkOrderFactory(created_by=self.user)
        WorkOrderFactory(created_by=self.user, parent_work_order=primary)

        response = self.client.get(reverse("workorder-active"))

        self.assertEqual(len(response.data), 1)

    def test_active_list_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(reverse("workorder-active"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class WorkOrderDetailTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_retrieve_lists_exact_serials_and_their_statuses(self):
        # TC-03/AC-3: drill into a WO for the exact serials issued and
        # their current statuses, not just aggregate counts.
        work_order = WorkOrderFactory(created_by=self.user)
        line_item = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type, quantity=2
        )
        item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_RESERVED,
        )

        response = self.client.get(reverse("workorder-detail", args=[work_order.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_line_item = response.data["line_items"][0]
        self.assertEqual(len(response_line_item["serialized_items"]), 1)
        self.assertEqual(
            response_line_item["serialized_items"][0]["serial_number"],
            item.serial_number,
        )
        self.assertEqual(
            response_line_item["serialized_items"][0]["status"],
            SerializedItem.STATUS_RESERVED,
        )

    def test_retrieve_requires_authentication(self):
        work_order = WorkOrderFactory(created_by=self.user)
        self.client.force_authenticate(user=None)

        response = self.client.get(reverse("workorder-detail", args=[work_order.id]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class WorkOrderSupplementaryHierarchyTests(TestCase):
    def test_supplementary_work_order_cannot_be_a_parent(self):
        # AC-6/TC-06: "WO-0042-S1" (a supplementary) can't itself be the
        # parent of another supplementary. No create endpoint sets
        # parent_work_order yet, so this exercises the model's own clean()
        # guard directly - the same validation Django admin's ModelForm
        # would trigger via full_clean().
        primary = WorkOrderFactory()
        supplementary = WorkOrderFactory(parent_work_order=primary)
        # save() doesn't call clean() - construct via the factory (so every
        # other required field is already valid) and call full_clean()
        # separately to isolate the guard under test.
        second_supplementary = WorkOrderFactory(parent_work_order=supplementary)

        with self.assertRaises(ValidationError) as ctx:
            second_supplementary.full_clean()

        self.assertIn("parent_work_order", ctx.exception.message_dict)

    def test_supplementary_of_a_primary_is_allowed(self):
        primary = WorkOrderFactory()
        supplementary = WorkOrderFactory(parent_work_order=primary)

        supplementary.full_clean()
