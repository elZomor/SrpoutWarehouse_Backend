from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SerializedItem, Transaction, WorkOrder
from inventory.tests.factories import (
    BoxFactory,
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


class WorkOrderSupplementaryCreationTests(APITestCase):
    # WRH-53/US-012a: creating a supplementary WO linked to an existing
    # Primary via the same create endpoint WorkOrderTests exercises above -
    # parent_work_order is just an optional field on it.
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def _create_work_order(self, **overrides):
        payload = {
            "job_name": "Summer Gala",
            "expected_date_out": "2026-08-01",
            "line_items": [{"product_type": self.product_type.id, "quantity": 5}],
        }
        payload.update(overrides)
        return self.client.post(reverse("workorder-list"), payload, format="json")

    def test_primary_work_order_reference_is_wo_plus_id(self):
        response = self._create_work_order()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["reference"], f"WO-{response.data['id']}")
        self.assertIsNone(response.data["parent_work_order"])

    def test_create_supplementary_is_saved_as_primary_reference_plus_s1(self):
        # AC-1/TC-01: "WO-0042" -> its first supplementary is "WO-0042-S1".
        primary = WorkOrderFactory(created_by=self.user)

        response = self._create_work_order(parent_work_order=primary.id)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["reference"], f"WO-{primary.id}-S1")
        self.assertEqual(response.data["parent_work_order"], primary.id)

    def test_create_second_supplementary_is_saved_as_s2(self):
        # AC-2/TC-02: a second supplementary on the same Primary is "-S2",
        # both nested under the same Primary.
        primary = WorkOrderFactory(created_by=self.user)
        self._create_work_order(parent_work_order=primary.id)

        response = self._create_work_order(parent_work_order=primary.id)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["reference"], f"WO-{primary.id}-S2")
        self.assertEqual(WorkOrder.objects.filter(parent_work_order=primary).count(), 2)

    def test_supplementary_has_its_own_line_items_separate_from_primary(self):
        # TC-03: the supplementary's own line items, not the Primary's.
        primary = WorkOrderFactory(created_by=self.user)
        WorkOrderLineItemFactory(
            work_order=primary, product_type=self.product_type, quantity=3
        )
        other_type = ProductTypeFactory()

        create_response = self._create_work_order(
            parent_work_order=primary.id,
            line_items=[{"product_type": other_type.id, "quantity": 2}],
        )
        supplementary_id = create_response.data["id"]

        detail_response = self.client.get(
            reverse("workorder-detail", args=[supplementary_id])
        )

        self.assertEqual(len(detail_response.data["line_items"]), 1)
        self.assertEqual(
            detail_response.data["line_items"][0]["product_type"], other_type.id
        )
        self.assertEqual(detail_response.data["line_items"][0]["quantity"], 2)

    def test_supplementary_appears_nested_under_primary_in_active_list(self):
        # AC-1: "appears nested under the Primary in the WO list."
        primary = WorkOrderFactory(created_by=self.user)

        create_response = self._create_work_order(parent_work_order=primary.id)

        active_response = self.client.get(reverse("workorder-active"))

        self.assertEqual(active_response.status_code, status.HTTP_200_OK)
        row = next(r for r in active_response.data if r["id"] == primary.id)
        self.assertEqual(len(row["supplementaries"]), 1)
        self.assertEqual(row["supplementaries"][0]["id"], create_response.data["id"])
        self.assertEqual(
            row["supplementaries"][0]["reference"], create_response.data["reference"]
        )

    def test_create_supplementary_rejects_a_supplementary_as_parent(self):
        # WRH-33/AC-6/TC-06: the rule was already enforced at the model
        # level with no reachable API path - this ticket adds the path, so
        # confirm it's actually enforced through it now.
        primary = WorkOrderFactory(created_by=self.user)
        supplementary = WorkOrderFactory(
            created_by=self.user,
            parent_work_order=primary,
            supplementary_sequence=1,
        )

        response = self._create_work_order(parent_work_order=supplementary.id)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent_work_order", response.data)
        self.assertEqual(
            WorkOrder.objects.filter(parent_work_order=supplementary).count(), 0
        )

    def test_create_supplementary_rejects_nonexistent_parent(self):
        response = self._create_work_order(parent_work_order=999999)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent_work_order", response.data)

    def test_create_supplementary_sequence_is_race_safe_under_concurrent_creates(
        self,
    ):
        # Simulates a second create() landing between reading the sibling
        # count and the lock being acquired - matches
        # WorkOrderScanTests/WorkOrderReturnItemTests's identical
        # race-simulation technique (WRH-56 lesson).
        primary = WorkOrderFactory(created_by=self.user)
        original_select_for_update = WorkOrder.objects.select_for_update

        def create_sibling_then_lock(*args, **kwargs):
            WorkOrder.objects.create(
                job_name="Racing supplementary",
                expected_date_out="2026-08-01",
                created_by=self.user,
                parent_work_order=primary,
                supplementary_sequence=1,
            )
            return original_select_for_update(*args, **kwargs)

        with patch.object(
            WorkOrder.objects, "select_for_update", create_sibling_then_lock
        ):
            response = self._create_work_order(parent_work_order=primary.id)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["reference"], f"WO-{primary.id}-S2")

    def test_create_supplementary_requires_authentication(self):
        primary = WorkOrderFactory(created_by=self.user)
        self.client.force_authenticate(user=None)

        response = self._create_work_order(parent_work_order=primary.id)

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

    def return_item(self, serial_number, damaged=False):
        payload = {"serial_number": serial_number}
        if damaged:
            payload["damaged"] = True
        return self.client.post(
            reverse("workorder-return-item", args=[self.work_order.id]),
            payload,
            format="json",
        )

    def _out_item(self):
        return SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=self.line_item,
            status=SerializedItem.STATUS_OUT,
        )

    def test_return_item_route_is_hyphenated(self):
        # Matches this repo's multi-word-action convention (see
        # SerializedItemViewSet's qr-code/qr-pdf actions) rather than DRF's
        # default underscored method-name routing.
        item = self._out_item()

        response = self.client.post(
            f"/api/work-orders/{self.work_order.id}/return-item/",
            {"serial_number": item.serial_number},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

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

    def test_return_of_an_already_damaged_item_reflects_state_without_error(self):
        # AC-6/TC-06
        item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=self.line_item,
            status=SerializedItem.STATUS_DAMAGED,
        )

        response = self.return_item(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_DAMAGED)
        self.assertEqual(Transaction.objects.filter(serialized_item=item).count(), 0)
        line_item_data = response.data["line_items"][0]
        self.assertEqual(line_item_data["damaged_quantity"], 1)
        self.assertEqual(line_item_data["still_out_quantity"], 0)

    def test_return_marks_a_returning_unit_as_damaged(self):
        # AC-1/TC-01: marking a returning unit as damaged flips it to
        # "damaged" (not "available"), links it to the returning WO, and
        # doesn't add it back to available stock.
        item = self._out_item()

        response = self.return_item(item.serial_number, damaged=True)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_DAMAGED)
        self.assertEqual(item.work_order_line_item_id, self.line_item.id)
        transaction = Transaction.objects.get(serialized_item=item)
        self.assertEqual(transaction.transaction_type, Transaction.TYPE_DAMAGED)
        self.assertEqual(transaction.work_order_id, self.work_order.id)

    def test_return_summary_separates_returned_damaged_and_still_missing(self):
        # AC-2/TC-02: a return session with returned, damaged, and still-out
        # items reports each as its own count.
        line_item = WorkOrderLineItemFactory(
            work_order=self.work_order, product_type=self.product_type, quantity=20
        )
        returned_items = [
            SerializedItemFactory(
                product_type=self.product_type,
                work_order_line_item=line_item,
                status=SerializedItem.STATUS_OUT,
            )
            for _ in range(15)
        ]
        damaged_item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_OUT,
        )
        for _ in range(4):
            SerializedItemFactory(
                product_type=self.product_type,
                work_order_line_item=line_item,
                status=SerializedItem.STATUS_OUT,
            )

        for item in returned_items:
            self.return_item(item.serial_number)
        response = self.return_item(damaged_item.serial_number, damaged=True)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line_item_data = next(
            row for row in response.data["line_items"] if row["id"] == line_item.id
        )
        self.assertEqual(line_item_data["returned_quantity"], 15)
        self.assertEqual(line_item_data["damaged_quantity"], 1)
        self.assertEqual(line_item_data["still_out_quantity"], 4)

    def test_wo_reaches_returned_status_when_remaining_item_is_marked_damaged(self):
        # AC-3/TC-03: a damaged item is excluded from both "still missing"
        # and "returned to available stock" - once every other item is
        # accounted for, the WO still reaches "returned".
        items = [self._out_item() for _ in range(2)]
        self.return_item(items[0].serial_number)

        response = self.return_item(items[1].serial_number, damaged=True)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], WorkOrder.STATUS_RETURNED)
        items[1].refresh_from_db()
        self.assertEqual(items[1].status, SerializedItem.STATUS_DAMAGED)

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
        self.assertEqual(row_line_item["damaged_quantity"], 0)

    def test_still_out_count_treats_a_missing_item_as_not_yet_returned(self):
        # Regression: still_out_quantity must count anything but "available"
        # or "damaged" (out/reserved/missing), not just STATUS_OUT -
        # otherwise a missing item vanishes from the summary instead of
        # counting as unaccounted-for.
        work_order = WorkOrderFactory(
            created_by=self.user, status=WorkOrder.STATUS_FULFILLED
        )
        line_item = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type, quantity=1
        )
        SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_MISSING,
        )

        response = self.client.get(reverse("workorder-active"))

        row_line_item = response.data[0]["line_items"][0]
        self.assertEqual(row_line_item["still_out_quantity"], 1)
        self.assertEqual(row_line_item["returned_quantity"], 0)
        self.assertEqual(row_line_item["damaged_quantity"], 0)

    def test_damaged_item_has_its_own_category_excluded_from_still_out(self):
        # WRH-57/AC-3: a damaged item counts neither as "still missing" nor
        # as "returned to available stock" - it has its own damaged_quantity
        # category.
        work_order = WorkOrderFactory(
            created_by=self.user, status=WorkOrder.STATUS_FULFILLED
        )
        line_item = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type, quantity=1
        )
        SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_DAMAGED,
        )

        response = self.client.get(reverse("workorder-active"))

        row_line_item = response.data[0]["line_items"][0]
        self.assertEqual(row_line_item["damaged_quantity"], 1)
        self.assertEqual(row_line_item["still_out_quantity"], 0)
        self.assertEqual(row_line_item["returned_quantity"], 0)

    def test_active_list_excludes_a_fully_returned_work_order(self):
        # WRH-38: once return_item() closes a WO out, it shouldn't stay on
        # the Active tab forever - "partially_returned" still needs
        # attention, so only the fully "returned" status is excluded.
        WorkOrderFactory(created_by=self.user, status=WorkOrder.STATUS_RETURNED)
        still_active = WorkOrderFactory(
            created_by=self.user, status=WorkOrder.STATUS_PARTIALLY_RETURNED
        )

        response = self.client.get(reverse("workorder-active"))

        ids = [row["id"] for row in response.data]
        self.assertEqual(ids, [still_active.id])

    def test_active_list_excludes_a_fully_returned_supplementary(self):
        # Same exclusion, one level deeper: a supplementary can
        # independently reach STATUS_RETURNED and shouldn't keep nesting
        # under its still-active primary once it does.
        primary = WorkOrderFactory(created_by=self.user)
        WorkOrderFactory(
            created_by=self.user,
            parent_work_order=primary,
            status=WorkOrder.STATUS_RETURNED,
        )
        still_active_supplementary = WorkOrderFactory(
            created_by=self.user,
            parent_work_order=primary,
            status=WorkOrder.STATUS_PARTIALLY_RETURNED,
        )

        response = self.client.get(reverse("workorder-active"))

        supplementary_ids = [s["id"] for s in response.data[0]["supplementaries"]]
        self.assertEqual(supplementary_ids, [still_active_supplementary.id])

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


class WorkOrderPackingListTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def _packing_list(self, work_order):
        with patch("inventory.views.work_order.HTML") as mock_html_class:
            mock_html_class.return_value.write_pdf.return_value = b"%PDF-fake"
            response = self.client.get(
                reverse("workorder-packing-list", args=[work_order.id])
            )
        return response, mock_html_class

    def test_packing_list_for_primary_with_no_supplementaries(self):
        # TC-01/AC-1: WO reference, job name, client name, date out, and
        # per line item the product type + requested qty + serials issued.
        work_order = WorkOrderFactory(
            job_name="Summer Gala",
            client_name="Acme Events",
            expected_date_out="2026-08-01",
            status=WorkOrder.STATUS_FULFILLED,
        )
        line_item = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type, quantity=2
        )
        SerializedItemFactory(
            serial_number="SN-0001",
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_OUT,
        )
        SerializedItemFactory(
            serial_number="SN-0002",
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_OUT,
        )

        response, mock_html_class = self._packing_list(work_order)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(
            f'filename="packing-list-WO-{work_order.id}.pdf"',
            response["Content-Disposition"],
        )
        rendered_html = mock_html_class.call_args.kwargs["string"]
        self.assertIn(f"WO-{work_order.id}", rendered_html)
        self.assertIn("Summer Gala", rendered_html)
        self.assertIn("Acme Events", rendered_html)
        self.assertIn("2026-08-01", rendered_html)
        self.assertIn(self.product_type.name, rendered_html)
        self.assertIn("SN-0001", rendered_html)
        self.assertIn("SN-0002", rendered_html)

    def test_packing_list_consolidates_primary_and_supplementaries(self):
        # TC-02/AC-2: downloading from the Primary bundles its own +
        # every supplementary's reference and line items into one PDF.
        primary = WorkOrderFactory(status=WorkOrder.STATUS_FULFILLED)
        supp1 = WorkOrderFactory(
            parent_work_order=primary,
            supplementary_sequence=1,
            status=WorkOrder.STATUS_FULFILLED,
        )
        supp2 = WorkOrderFactory(
            parent_work_order=primary,
            supplementary_sequence=2,
            status=WorkOrder.STATUS_FULFILLED,
        )
        primary_line_item = WorkOrderLineItemFactory(
            work_order=primary, product_type=self.product_type
        )
        supp1_line_item = WorkOrderLineItemFactory(
            work_order=supp1, product_type=self.product_type
        )
        supp2_line_item = WorkOrderLineItemFactory(
            work_order=supp2, product_type=self.product_type
        )
        SerializedItemFactory(
            serial_number="SN-PRIMARY",
            product_type=self.product_type,
            work_order_line_item=primary_line_item,
            status=SerializedItem.STATUS_OUT,
        )
        SerializedItemFactory(
            serial_number="SN-SUPP1",
            product_type=self.product_type,
            work_order_line_item=supp1_line_item,
            status=SerializedItem.STATUS_OUT,
        )
        SerializedItemFactory(
            serial_number="SN-SUPP2",
            product_type=self.product_type,
            work_order_line_item=supp2_line_item,
            status=SerializedItem.STATUS_OUT,
        )

        response, mock_html_class = self._packing_list(primary)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rendered_html = mock_html_class.call_args.kwargs["string"]
        self.assertIn(f"WO-{primary.id}", rendered_html)
        self.assertIn(f"WO-{primary.id}-S1", rendered_html)
        self.assertIn(f"WO-{primary.id}-S2", rendered_html)
        self.assertIn("SN-PRIMARY", rendered_html)
        self.assertIn("SN-SUPP1", rendered_html)
        self.assertIn("SN-SUPP2", rendered_html)

    def test_packing_list_available_immediately_when_in_progress(self):
        # TC-03/AC-3: reflects whatever has been scanned so far, even
        # before the line item's requested quantity is fully reached.
        work_order = WorkOrderFactory(status=WorkOrder.STATUS_IN_PROGRESS)
        line_item = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type, quantity=5
        )
        SerializedItemFactory(
            serial_number="SN-SCANNED",
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_RESERVED,
        )

        response, mock_html_class = self._packing_list(work_order)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rendered_html = mock_html_class.call_args.kwargs["string"]
        self.assertIn("SN-SCANNED", rendered_html)

    def test_packing_list_downloadable_at_later_statuses(self):
        # TC-04/AC-4: still generates correctly once fulfilled or returned
        # (there is no STATUS_CLOSED on this model - see the plan note in
        # the ticket cache; "not draft" already covers every later status).
        for later_status in (
            WorkOrder.STATUS_FULFILLED,
            WorkOrder.STATUS_PARTIALLY_RETURNED,
            WorkOrder.STATUS_RETURNED,
        ):
            with self.subTest(status=later_status):
                work_order = WorkOrderFactory(status=later_status)

                response, _ = self._packing_list(work_order)

                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_packing_list_lists_serials_under_their_own_line_item(self):
        # TC-05: each line item's section lists only its own product
        # type's serials, not another line item's.
        work_order = WorkOrderFactory(status=WorkOrder.STATUS_FULFILLED)
        other_type = ProductTypeFactory()
        line_item_a = WorkOrderLineItemFactory(
            work_order=work_order, product_type=self.product_type
        )
        line_item_b = WorkOrderLineItemFactory(
            work_order=work_order, product_type=other_type
        )
        SerializedItemFactory(
            serial_number="SN-TYPE-A",
            product_type=self.product_type,
            work_order_line_item=line_item_a,
            status=SerializedItem.STATUS_OUT,
        )
        SerializedItemFactory(
            serial_number="SN-TYPE-B",
            product_type=other_type,
            work_order_line_item=line_item_b,
            status=SerializedItem.STATUS_OUT,
        )

        with patch("inventory.views.work_order.HTML") as mock_html_class, patch(
            "inventory.views.work_order.render_to_string",
            wraps=render_to_string,
        ) as mock_render:
            mock_html_class.return_value.write_pdf.return_value = b"%PDF-fake"
            response = self.client.get(
                reverse("workorder-packing-list", args=[work_order.id])
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        context = mock_render.call_args.args[1]
        line_items_by_type = {
            li["product_type_name"]: li["serial_numbers"]
            for li in context["sections"][0]["line_items"]
        }
        self.assertEqual(line_items_by_type[self.product_type.name], ["SN-TYPE-A"])
        self.assertEqual(line_items_by_type[other_type.name], ["SN-TYPE-B"])

    def test_packing_list_rejects_draft_work_order(self):
        work_order = WorkOrderFactory()

        response, _ = self._packing_list(work_order)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "Packing list is available once fulfillment has started.",
        )

    def test_packing_list_rejects_when_requested_on_a_supplementary(self):
        primary = WorkOrderFactory(status=WorkOrder.STATUS_FULFILLED)
        supplementary = WorkOrderFactory(
            parent_work_order=primary,
            supplementary_sequence=1,
            status=WorkOrder.STATUS_FULFILLED,
        )

        response, _ = self._packing_list(supplementary)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "Download the packing list from the Primary work order instead.",
        )


class WorkOrderScanBoxTests(APITestCase):
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
            quantity=5,
        )

    def scan_box(self, box_code):
        return self.client.post(
            reverse("workorder-scan-box", args=[self.work_order.id]),
            {"box_code": box_code},
            format="json",
        )

    def test_scan_box_reserves_every_item_inside(self):
        # WRH-26/AC-2/AC-3
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(3)
        ]
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(id__in=[item.id for item in items]).update(
            box=box
        )

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["box_summary"]["added"], 3)
        for item in items:
            item.refresh_from_db()
            self.assertEqual(item.status, SerializedItem.STATUS_RESERVED)
            self.assertEqual(item.work_order_line_item_id, self.line_item.id)
        line_item_data = next(
            li
            for li in response.data["work_order"]["line_items"]
            if li["id"] == self.line_item.id
        )
        self.assertEqual(line_item_data["scanned_quantity"], 3)

    def test_scan_box_reports_an_unavailable_item_without_blocking_the_rest(self):
        # WRH-26/AC-2: "each validated individually" - one already-out item
        # doesn't stop its box-mates from being added.
        available_item = SerializedItemFactory(product_type=self.product_type)
        out_item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_OUT
        )
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(id__in=[available_item.id, out_item.id]).update(
            box=box
        )

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["box_summary"]["added"], 1)
        results = {
            result["serial_number"]: result
            for result in response.data["box_summary"]["results"]
        }
        self.assertTrue(results[available_item.serial_number]["added"])
        self.assertFalse(results[out_item.serial_number]["added"])

    def test_scan_box_rejects_unknown_box_code(self):
        response = self.scan_box("BX-DOES-NOT-EXIST")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scan_box_rejects_when_no_line_item_matches_product_type(self):
        other_product_type = ProductTypeFactory()
        box = BoxFactory(product_type=other_product_type)

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scan_box_rejects_when_work_order_has_not_been_started(self):
        self.work_order.status = WorkOrder.STATUS_DRAFT
        self.work_order.save(update_fields=["status"])
        box = BoxFactory(product_type=self.product_type)

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scan_box_requires_authentication(self):
        self.client.logout()
        box = BoxFactory(product_type=self.product_type)

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_scan_box_rejects_an_item_whose_actual_product_type_differs_from_the_box(
        self,
    ):
        # WRH-27/AC-1 isn't built yet, so a box's own items aren't
        # guaranteed to match its declared product_type at this data layer
        # (e.g. pre-existing data, admin-created boxes) - scan_box() must
        # not trust that invariant, matching scan()'s identical per-item
        # check.
        matching_item = SerializedItemFactory(product_type=self.product_type)
        mismatched_item = SerializedItemFactory(product_type=ProductTypeFactory())
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(
            id__in=[matching_item.id, mismatched_item.id]
        ).update(box=box)

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["box_summary"]["added"], 1)
        results = {
            result["serial_number"]: result
            for result in response.data["box_summary"]["results"]
        }
        self.assertTrue(results[matching_item.serial_number]["added"])
        self.assertFalse(results[mismatched_item.serial_number]["added"])
        mismatched_item.refresh_from_db()
        self.assertEqual(mismatched_item.status, SerializedItem.STATUS_AVAILABLE)

    def test_scan_box_rejects_when_the_matching_line_items_product_type_is_archived(
        self,
    ):
        # Matches WorkOrderScanSerializer.line_item's identical archived-
        # product-type restriction (WRH-21) - a box scan shouldn't be able
        # to claim against a line item a single scan() would reject.
        self.product_type.archived = True
        self.product_type.save(update_fields=["archived"])
        box = BoxFactory(product_type=self.product_type)

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scan_box_rejects_when_two_line_items_share_the_boxs_product_type(self):
        # scan_box() has no way to ask the caller which of two same-
        # product-type line items it means (unlike scan(), which always
        # takes an explicit line_item id) - it must refuse rather than
        # silently picking one.
        WorkOrderLineItemFactory(
            work_order=self.work_order,
            product_type=self.product_type,
            quantity=2,
        )
        box = BoxFactory(product_type=self.product_type)

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scan_box_stops_adding_once_the_line_items_quantity_cap_is_reached(self):
        # AC-2/AC-4: matches scan()'s identical
        # test_scan_rejects_scan_past_requested_quantity boundary, applied to
        # a box scan claiming multiple items in one call - self.line_item's
        # quantity is 5, so a 6-item box should add exactly 5 and reject the
        # 6th on the cap, not silently over-claim.
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(6)
        ]
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(id__in=[item.id for item in items]).update(
            box=box
        )

        response = self.scan_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["box_summary"]["added"], 5)
        results = response.data["box_summary"]["results"]
        added_count = sum(1 for result in results if result["added"])
        rejected = [result for result in results if not result["added"]]
        self.assertEqual(added_count, 5)
        self.assertEqual(len(rejected), 1)
        self.assertIn("requested quantity", rejected[0]["reason"])


class WorkOrderReturnBoxTests(APITestCase):
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

    def return_box(self, box_code):
        return self.client.post(
            reverse("workorder-return-box", args=[self.work_order.id]),
            {"box_code": box_code},
            format="json",
        )

    def _out_item(self):
        return SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=self.line_item,
            status=SerializedItem.STATUS_OUT,
        )

    def test_return_box_returns_every_item_inside_and_marks_wo_returned(self):
        # WRH-26/AC-2/AC-3
        items = [self._out_item() for _ in range(2)]
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(id__in=[item.id for item in items]).update(
            box=box
        )

        response = self.return_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["box_summary"]["added"], 2)
        for item in items:
            item.refresh_from_db()
            self.assertEqual(item.status, SerializedItem.STATUS_AVAILABLE)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.status, WorkOrder.STATUS_RETURNED)

    def test_return_box_reports_an_item_not_issued_on_this_wo_without_blocking_rest(
        self,
    ):
        returnable_item = self._out_item()
        foreign_item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(
            id__in=[returnable_item.id, foreign_item.id]
        ).update(box=box)

        response = self.return_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = {
            result["serial_number"]: result
            for result in response.data["box_summary"]["results"]
        }
        self.assertTrue(results[returnable_item.serial_number]["added"])
        self.assertFalse(results[foreign_item.serial_number]["added"])

    def test_return_box_flags_an_already_damaged_item_without_counting_it_as_returned(
        self,
    ):
        # WRH-28/AC-3/TC-05: a box-mate externally marked damaged mid-box
        # must be flagged (not silently folded into the returned count) and
        # left untouched, while the rest of the box returns normally.
        returnable_item = self._out_item()
        damaged_item = self._out_item()
        damaged_item.status = SerializedItem.STATUS_DAMAGED
        damaged_item.save(update_fields=["status"])
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(
            id__in=[returnable_item.id, damaged_item.id]
        ).update(box=box)

        response = self.return_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["box_summary"]["added"], 1)
        results = {
            result["serial_number"]: result
            for result in response.data["box_summary"]["results"]
        }
        self.assertTrue(results[returnable_item.serial_number]["added"])
        self.assertFalse(results[damaged_item.serial_number]["added"])
        self.assertIn(
            damaged_item.serial_number, results[damaged_item.serial_number]["reason"]
        )
        returnable_item.refresh_from_db()
        damaged_item.refresh_from_db()
        self.assertEqual(returnable_item.status, SerializedItem.STATUS_AVAILABLE)
        self.assertEqual(damaged_item.status, SerializedItem.STATUS_DAMAGED)

    def test_return_box_rejects_unknown_box_code(self):
        response = self.return_box("BX-DOES-NOT-EXIST")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_return_box_rejects_when_work_order_is_draft(self):
        self.work_order.status = WorkOrder.STATUS_DRAFT
        self.work_order.save(update_fields=["status"])
        box = BoxFactory(product_type=self.product_type)

        response = self.return_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_return_box_requires_authentication(self):
        self.client.logout()
        box = BoxFactory(product_type=self.product_type)

        response = self.return_box(box.code)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
