from unittest.mock import patch

from django.contrib.auth.models import User
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

    def test_retrieve_update_destroy_routes_are_not_registered(self):
        # WRH-31 only scopes create+list - retrieve/update/destroy are
        # separate, unscoped stories, so no detail route exists at all yet.
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

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
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
        response = self.scan(self.line_item, "SN-DOES-NOT-EXIST")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)

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
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_OUT
        )

        response = self.scan(self.line_item, item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)

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
