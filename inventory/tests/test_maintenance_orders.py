from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import MaintenanceOrder, SerializedItem
from inventory.tests.factories import (
    BoxFactory,
    MaintenanceOrderFactory,
    ProductTypeFactory,
    SerializedItemFactory,
    WorkOrderFactory,
    WorkOrderLineItemFactory,
)


class MaintenanceOrderCreationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)

    def test_create_mo_from_three_damaged_items(self):
        # TC-01/AC-1: MO-0001 created, status "open"; all 3 items moved to
        # "in_maintenance".
        items = [
            SerializedItemFactory(status=SerializedItem.STATUS_DAMAGED)
            for _ in range(3)
        ]

        response = self.client.post(
            reverse("maintenanceorder-list"),
            {"item_ids": [item.id for item in items]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["reference"], "MO-0001")
        self.assertEqual(response.data["status"], MaintenanceOrder.STATUS_OPEN)
        self.assertEqual(len(response.data["items"]), 3)
        maintenance_order = MaintenanceOrder.objects.get(pk=response.data["id"])
        self.assertEqual(maintenance_order.items.count(), 3)
        for item in items:
            item.refresh_from_db()
            self.assertEqual(item.status, SerializedItem.STATUS_IN_MAINTENANCE)
            self.assertEqual(item.maintenance_order_id, maintenance_order.id)

    def test_create_mo_from_a_single_damaged_item(self):
        # TC-02/AC-1: MO created with 1 line item.
        item = SerializedItemFactory(status=SerializedItem.STATUS_DAMAGED)

        response = self.client.post(
            reverse("maintenanceorder-list"),
            {"item_ids": [item.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["id"], item.id)

    def test_mo_reference_numbering_is_sequential(self):
        # TC-03: references follow MO-0001, MO-0002, ... per §6.5.
        references = []
        for _ in range(3):
            item = SerializedItemFactory(status=SerializedItem.STATUS_DAMAGED)
            response = self.client.post(
                reverse("maintenanceorder-list"),
                {"item_ids": [item.id]},
                format="json",
            )
            references.append(response.data["reference"])

        self.assertEqual(references, ["MO-0001", "MO-0002", "MO-0003"])

    def test_created_mo_appears_in_list(self):
        MaintenanceOrderFactory()

        response = self.client.get(reverse("maintenanceorder-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_create_mo_rejects_an_item_already_on_another_maintenance_order(self):
        item = SerializedItemFactory(status=SerializedItem.STATUS_DAMAGED)
        first_response = self.client.post(
            reverse("maintenanceorder-list"),
            {"item_ids": [item.id]},
            format="json",
        )
        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)

        second_item = SerializedItemFactory(status=SerializedItem.STATUS_DAMAGED)
        second_response = self.client.post(
            reverse("maintenanceorder-list"),
            {"item_ids": [item.id, second_item.id]},
            format="json",
        )

        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            second_response.data["item_ids"],
            [f"{item.serial_number} is already on maintenance order MO-0001"],
        )
        second_item.refresh_from_db()
        self.assertIsNone(second_item.maintenance_order_id)

    def test_create_mo_rejects_an_item_already_in_a_box(self):
        product_type = ProductTypeFactory()
        box = BoxFactory(product_type=product_type)
        item = SerializedItemFactory(
            product_type=product_type,
            status=SerializedItem.STATUS_DAMAGED,
            box=box,
        )

        response = self.client.post(
            reverse("maintenanceorder-list"),
            {"item_ids": [item.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["item_ids"],
            [f"{item.serial_number} is already in box {box.code}"],
        )

    def test_create_mo_rejects_an_item_claimed_on_a_work_order(self):
        work_order = WorkOrderFactory()
        line_item = WorkOrderLineItemFactory(work_order=work_order)
        item = SerializedItemFactory(
            product_type=line_item.product_type,
            status=SerializedItem.STATUS_DAMAGED,
            work_order_line_item=line_item,
        )

        response = self.client.post(
            reverse("maintenanceorder-list"),
            {"item_ids": [item.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["item_ids"],
            [f"{item.serial_number} is already claimed on a work order"],
        )

    def test_create_mo_requires_at_least_one_item(self):
        response = self.client.post(
            reverse("maintenanceorder-list"), {"item_ids": []}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["item_ids"], ["Select at least one item."])

    def test_create_mo_rechecks_claims_after_lock_is_acquired(self):
        # The item_ids field/validate() check above only catches a claim
        # that already exists pre-lock - simulate a concurrent claim landing
        # in the window between that check and create()'s
        # select_for_update()-guarded re-fetch, matching
        # BoxSerializer.create()'s identical regression test shape (WRH-27).
        item = SerializedItemFactory(status=SerializedItem.STATUS_DAMAGED)
        other_box = BoxFactory()
        original_select_for_update = SerializedItem.objects.select_for_update

        def claim_then_lock(*args, **kwargs):
            item.box = other_box
            item.save(update_fields=["box"])
            return original_select_for_update(*args, **kwargs)

        with patch.object(SerializedItem.objects, "select_for_update", claim_then_lock):
            response = self.client.post(
                reverse("maintenanceorder-list"),
                {"item_ids": [item.id]},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("item_ids", response.data)
        self.assertFalse(MaintenanceOrder.objects.exists())
        item.refresh_from_db()
        self.assertIsNone(item.maintenance_order_id)

    def test_create_mo_requires_authentication(self):
        self.client.logout()

        response = self.client.post(
            reverse("maintenanceorder-list"), {"item_ids": []}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retrieve_update_destroy_routes_are_not_registered(self):
        maintenance_order = MaintenanceOrderFactory()

        detail_url = f"/api/maintenance-orders/{maintenance_order.id}/"

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(
            self.client.put(detail_url, {}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )
