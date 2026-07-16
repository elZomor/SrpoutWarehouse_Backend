from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import WorkOrder
from inventory.tests.factories import ProductTypeFactory


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
