from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import MaintenanceOrder, SerializedItem
from inventory.tests.factories import MaintenanceOrderFactory, SerializedItemFactory


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
