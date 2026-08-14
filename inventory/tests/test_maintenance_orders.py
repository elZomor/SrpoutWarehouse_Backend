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

    def test_create_mo_accepts_a_damaged_item_that_is_still_boxed(self):
        # Box membership is a permanent, orthogonal physical tag (nothing
        # ever clears SerializedItem.box), not a competing claim - a boxed
        # item marked damaged mid-box (WorkOrderViewSet.return_box()'s own
        # handling) must still be eligible for an MO. Regression test for a
        # bug caught in review: box_id's mere presence must not be mistaken
        # for a live claim, the same class of bug fixed for
        # work_order_line_item_id above.
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

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_IN_MAINTENANCE)
        self.assertEqual(item.box_id, box.id)

    def test_create_mo_rejects_an_item_currently_out_on_a_work_order(self):
        work_order = WorkOrderFactory()
        line_item = WorkOrderLineItemFactory(work_order=work_order)
        item = SerializedItemFactory(
            product_type=line_item.product_type,
            status=SerializedItem.STATUS_OUT,
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
            [f"{item.serial_number} is currently claimed on a work order"],
        )

    def test_create_mo_accepts_a_damaged_item_with_past_work_order_history(self):
        # work_order_line_item is a "current claim only, no history" FK that
        # return_item() never clears - a damaged item that was issued and
        # returned on a WO long ago still carries that FK, but is fully
        # eligible for an MO per AC-1 (status "damaged" is all that
        # matters). Regression test for a bug caught in review: the FK's
        # mere presence must not be mistaken for a live claim.
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

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_IN_MAINTENANCE)

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
        original_select_for_update = SerializedItem.objects.select_for_update

        def claim_then_lock(*args, **kwargs):
            item.status = SerializedItem.STATUS_OUT
            item.save(update_fields=["status"])
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


class MaintenanceOrderResolveTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _resolve_url(maintenance_order):
        return f"/api/maintenance-orders/{maintenance_order.id}/resolve/"

    def _create_mo(self, count):
        items = [
            SerializedItemFactory(status=SerializedItem.STATUS_DAMAGED)
            for _ in range(count)
        ]
        response = self.client.post(
            reverse("maintenanceorder-list"),
            {"item_ids": [item.id for item in items]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        maintenance_order = MaintenanceOrder.objects.get(pk=response.data["id"])
        return maintenance_order, items

    def test_mark_a_line_item_as_fixed(self):
        # TC-01/AC-1/AC-3: item -> "available"; MO -> "in_progress" since
        # only one of three items is resolved.
        maintenance_order, items = self._create_mo(3)

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": items[0].id, "resolution": "fixed"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MaintenanceOrder.STATUS_IN_PROGRESS)
        items[0].refresh_from_db()
        self.assertEqual(items[0].status, SerializedItem.STATUS_AVAILABLE)
        maintenance_order.refresh_from_db()
        self.assertEqual(maintenance_order.status, MaintenanceOrder.STATUS_IN_PROGRESS)

    def test_mark_a_line_item_as_not_fixable(self):
        # TC-02/AC-2: item -> "written_off".
        maintenance_order, items = self._create_mo(2)

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": items[0].id, "resolution": "not_fixable"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items[0].refresh_from_db()
        self.assertEqual(items[0].status, SerializedItem.STATUS_WRITTEN_OFF)

    def test_mo_completes_when_all_line_items_resolved(self):
        # TC-03/AC-3: resolving the final remaining item flips the MO to
        # "completed".
        maintenance_order, items = self._create_mo(3)
        for item in items[:2]:
            self.client.post(
                self._resolve_url(maintenance_order),
                {"item_id": item.id, "resolution": "fixed"},
                format="json",
            )

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": items[2].id, "resolution": "fixed"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MaintenanceOrder.STATUS_COMPLETED)
        maintenance_order.refresh_from_db()
        self.assertEqual(maintenance_order.status, MaintenanceOrder.STATUS_COMPLETED)

    def test_mixed_resolution_outcomes_completes_the_mo(self):
        # TC-04/AC-3: one item fixed, one not_fixable - both outcomes count
        # as "resolved" so the MO still reaches "completed".
        maintenance_order, items = self._create_mo(2)
        self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": items[0].id, "resolution": "fixed"},
            format="json",
        )

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": items[1].id, "resolution": "not_fixable"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MaintenanceOrder.STATUS_COMPLETED)
        items[0].refresh_from_db()
        items[1].refresh_from_db()
        self.assertEqual(items[0].status, SerializedItem.STATUS_AVAILABLE)
        self.assertEqual(items[1].status, SerializedItem.STATUS_WRITTEN_OFF)

    def test_resolve_rejects_an_item_not_on_this_maintenance_order(self):
        maintenance_order, _ = self._create_mo(1)
        other_item = SerializedItemFactory(status=SerializedItem.STATUS_AVAILABLE)

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": other_item.id, "resolution": "fixed"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["item_id"],
            [
                f"{other_item.serial_number} is not a line item on"
                f" {maintenance_order.reference}"
            ],
        )

    def test_resolve_rejects_an_unknown_item_id(self):
        maintenance_order, _ = self._create_mo(1)

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": 999999, "resolution": "fixed"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["item_id"], ["Item not found."])

    def test_resolve_rejects_an_invalid_resolution_value(self):
        maintenance_order, items = self._create_mo(1)

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": items[0].id, "resolution": "bogus"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("resolution", response.data)

    def test_resolve_requires_authentication(self):
        maintenance_order, items = self._create_mo(1)
        self.client.logout()

        response = self.client.post(
            self._resolve_url(maintenance_order),
            {"item_id": items[0].id, "resolution": "fixed"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
