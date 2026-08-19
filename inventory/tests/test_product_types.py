from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from inventory.models import ProductType, SerializedItem
from inventory.tests.factories import (
    CategoryFactory,
    ProductTypeFactory,
    SerializedItemFactory,
)


class ProductTypeTests(APITestCase):
    def setUp(self):
        self.password = "correct-horse-battery-staple"
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password=self.password
        )
        self.client.force_authenticate(user=self.user)
        self.category = CategoryFactory()

    def test_create_product_type_with_all_fields(self):
        # TC-01: create with name, model code, and description
        response = self.client.post(
            reverse("producttype-list"),
            {
                "name": "Bar LED Model A",
                "model_code": "BAR-LED-A",
                "description": "Moving bar light",
                "category": self.category.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Bar LED Model A")
        self.assertEqual(response.data["model_code"], "BAR-LED-A")
        self.assertEqual(response.data["description"], "Moving bar light")
        self.assertEqual(response.data["category"], self.category.id)

    def test_create_product_type_with_name_only(self):
        # TC-02/AC-3: model code and description are optional
        response = self.client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": self.category.id},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["model_code"], "")
        self.assertEqual(response.data["description"], "")

    def test_create_product_type_without_category_is_rejected(self):
        # AC-4: category is a required field on the Product Type form
        response = self.client.post(
            reverse("producttype-list"), {"name": "Bar LED Model A"}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("category", response.data)

    def test_create_product_type_with_archived_category_is_rejected(self):
        # AC-6/WRH-62: an archived category is hidden from the category
        # dropdown, but the API itself must also reject it directly - a
        # stale/replayed id shouldn't be able to attach a new Product Type
        # to a retired category.
        archived_category = CategoryFactory(archived=True)

        response = self.client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": archived_category.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("category", response.data)

    def test_product_type_response_includes_category_name(self):
        # Denormalized so the frontend can display the category name for a
        # Product Type without depending on the (now archived-filtered)
        # category list still containing it.
        response = self.client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": self.category.id},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["category_name"], self.category.name)

    def test_product_type_still_shows_category_name_after_category_archived(self):
        response = self.client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": self.category.id},
        )
        created_id = response.data["id"]
        self.category.archived = True
        self.category.save(update_fields=["archived"])

        list_response = self.client.get(reverse("producttype-list"))

        item = next(i for i in list_response.data if i["id"] == created_id)
        self.assertEqual(item["category_name"], self.category.name)

    def test_create_product_type_succeeds_via_real_csrf_flow(self):
        # force_authenticate (used by every other test here) bypasses CSRF
        # entirely, so it can't prove a real browser session - which must
        # send X-CSRFToken - can actually create a product type.
        client = APIClient(enforce_csrf_checks=True)
        login_response = client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        csrf_token = client.cookies["csrftoken"].value

        response = client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": self.category.id},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_product_type_is_rejected_without_csrf_token(self):
        # A real session without the X-CSRFToken header must still be
        # rejected - proves CSRF enforcement is actually active on this
        # endpoint, not just that it succeeds when the token is sent.
        client = APIClient(enforce_csrf_checks=True)
        login_response = client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        response = client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": self.category.id},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retrieve_and_update_routes_are_not_registered(self):
        # ProductTypeViewSet mixes in list/create/destroy (WRH-21), so the
        # detail route now exists (DELETE works) but retrieve/update aren't
        # needed by any story yet - GET/PUT/PATCH correctly 405 (method not
        # allowed on an existing route) rather than 404 (route doesn't
        # exist at all).
        product_type = ProductTypeFactory()
        detail_url = f"/api/product-types/{product_type.pk}/"

        self.assertEqual(
            self.client.get(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.put(detail_url, {"name": "New Name"}).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.patch(detail_url, {"name": "New Name"}).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_create_product_type_without_name_is_rejected(self):
        # AC-1/TC-01
        response = self.client.post(
            reverse("producttype-list"), {"category": self.category.id}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["name"], ["Name is required."])

    def test_create_product_type_with_empty_name_is_rejected(self):
        # AC-1/TC-01: empty string, not just an omitted field
        response = self.client.post(
            reverse("producttype-list"),
            {"name": "", "category": self.category.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["name"], ["Name is required."])

    def test_create_product_type_with_duplicate_name_is_rejected(self):
        # AC-2/TC-02
        ProductTypeFactory(name="Bar LED Model A")

        response = self.client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": self.category.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["name"], ["A product type with this name already exists."]
        )

    def test_create_product_type_duplicate_name_race_still_returns_400(self):
        # AC-2: simulates two requests racing past the serializer's
        # SELECT-based UniqueValidator (disabled here to stand in for that
        # race) so the DB's unique constraint is the only thing left to
        # catch the duplicate - proves perform_create() translates the
        # resulting IntegrityError into a 400, not an unhandled 500.
        ProductTypeFactory(name="Bar LED Model A")

        with patch("rest_framework.validators.UniqueValidator.__call__"):
            response = self.client.post(
                reverse("producttype-list"),
                {"name": "Bar LED Model A", "category": self.category.id},
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["name"], "A product type with this name already exists."
        )

    def test_delete_product_type_with_registered_items_is_blocked(self):
        # AC-3/TC-03
        product_type = ProductTypeFactory()
        SerializedItemFactory.create_batch(5, product_type=product_type)

        response = self.client.delete(f"/api/product-types/{product_type.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "Cannot delete — 5 items are registered under this product type. "
            "Reassign or remove them first.",
        )
        self.assertEqual(response.data["registered_item_count"], 5)
        self.assertTrue(ProductType.objects.filter(pk=product_type.pk).exists())

    def test_delete_blocked_message_is_singular_for_one_item(self):
        # AC-3: "1 items are" reads wrong - singular noun/verb only when
        # exactly one SerializedItem is registered.
        product_type = ProductTypeFactory()
        SerializedItemFactory(product_type=product_type)

        response = self.client.delete(f"/api/product-types/{product_type.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "Cannot delete — 1 item is registered under this product type. "
            "Reassign or remove them first.",
        )

    def test_delete_product_type_with_zero_items_succeeds(self):
        # AC-5/TC-05
        product_type = ProductTypeFactory()

        response = self.client.delete(f"/api/product-types/{product_type.pk}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProductType.objects.filter(pk=product_type.pk).exists())

    def test_delete_with_unrelated_search_query_param_still_succeeds(self):
        # A stray ?search= param (e.g. left over from the list view's search
        # box) must not affect get_object()'s pk lookup on the detail route -
        # SearchFilter is only meant to scope the list action.
        product_type = ProductTypeFactory(name="Bar LED Model A")

        response = self.client.delete(
            f"/api/product-types/{product_type.pk}/", {"search": "does-not-match"}
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProductType.objects.filter(pk=product_type.pk).exists())

    def test_archive_endpoint_is_removed(self):
        # WRH-73: the archive action/route no longer exists - the
        # `archived` field/filter themselves stay (admin-editable only).
        product_type = ProductTypeFactory(name="Bar LED Model A")

        response = self.client.post(f"/api/product-types/{product_type.pk}/archive/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        product_type.refresh_from_db()
        self.assertFalse(product_type.archived)

    def test_delete_race_with_concurrent_registration_is_blocked_not_500(self):
        # AC-3: if a SerializedItem gets registered between the count()
        # check and the actual delete (concurrent request), on_delete=PROTECT
        # raises ProtectedError inside perform_destroy() - this must still
        # surface as the same structured 400, not an unhandled 500.
        product_type = ProductTypeFactory()

        with patch(
            "inventory.views.ProductTypeViewSet.perform_destroy",
            side_effect=ProtectedError("protected", set()),
        ):
            response = self.client.delete(f"/api/product-types/{product_type.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("registered_item_count", response.data)
        self.assertTrue(ProductType.objects.filter(pk=product_type.pk).exists())

    def test_archived_product_type_hidden_from_default_list(self):
        # AC-4/AC-6/TC-06: same list endpoint backs the active-list view
        # and the SerializedItem registration form's product-type selector.
        archived = ProductTypeFactory(archived=True)
        active = ProductTypeFactory()

        response = self.client.get(reverse("producttype-list"))

        ids = [item["id"] for item in response.data]
        self.assertIn(active.id, ids)
        self.assertNotIn(archived.id, ids)

    def test_created_product_type_appears_in_list(self):
        # AC-1: new product type appears in the product type list
        response = self.client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A", "category": self.category.id},
        )
        created_id = response.data["id"]

        list_response = self.client.get(reverse("producttype-list"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(created_id, [item["id"] for item in list_response.data])

    def test_search_matches_by_name(self):
        # TC-03: search filters to product types whose name matches
        ProductTypeFactory(name="Bar LED Model A")
        ProductTypeFactory(name="Fog Machine")

        response = self.client.get(reverse("producttype-list"), {"search": "Bar LED"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["name"] for item in response.data], ["Bar LED Model A"])

    def test_search_matches_by_model_code(self):
        # AC-2: search also matches on model code
        ProductTypeFactory(name="Bar LED Model A", model_code="BAR-LED-A")
        ProductTypeFactory(name="Fog Machine", model_code="FOG-01")

        response = self.client.get(reverse("producttype-list"), {"search": "FOG-01"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["name"] for item in response.data], ["Fog Machine"])

    def test_search_with_no_matches_returns_empty_list(self):
        # TC-04: no matches -> empty list, not an error
        ProductTypeFactory(name="Bar LED Model A")

        response = self.client.get(
            reverse("producttype-list"), {"search": "nonexistent"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])


class ProductTypeStockSummaryTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.category = CategoryFactory()

    def _summary_for(self, response, product_type_id):
        return next(row for row in response.data if row["id"] == product_type_id)

    def test_shows_counts_per_status(self):
        # AC-1/TC-01: total registered, out, damaged, missing, available.
        product_type = ProductTypeFactory(category=self.category)
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_AVAILABLE
        )
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_OUT
        )
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_DAMAGED
        )
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_MISSING
        )

        response = self.client.get(reverse("producttype-stock-summary"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = self._summary_for(response, product_type.id)
        self.assertEqual(row["total_registered"], 4)
        self.assertEqual(row["out"], 1)
        self.assertEqual(row["damaged"], 1)
        self.assertEqual(row["missing"], 1)
        self.assertEqual(row["available"], 1)

    def test_available_count_is_computed_correctly(self):
        # AC-2/TC-02: 100 total, 30 out, 5 damaged, 2 missing, 1 written_off
        # -> Available = 62.
        product_type = ProductTypeFactory(category=self.category)
        for _ in range(62):
            SerializedItemFactory(
                product_type=product_type, status=SerializedItem.STATUS_AVAILABLE
            )
        for _ in range(30):
            SerializedItemFactory(
                product_type=product_type, status=SerializedItem.STATUS_OUT
            )
        for _ in range(5):
            SerializedItemFactory(
                product_type=product_type, status=SerializedItem.STATUS_DAMAGED
            )
        for _ in range(2):
            SerializedItemFactory(
                product_type=product_type, status=SerializedItem.STATUS_MISSING
            )
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_WRITTEN_OFF
        )

        response = self.client.get(reverse("producttype-stock-summary"))

        row = self._summary_for(response, product_type.id)
        self.assertEqual(row["total_registered"], 100)
        self.assertEqual(row["available"], 62)
        self.assertEqual(row["written_off"], 1)

    def test_reserved_items_are_not_counted_as_available(self):
        # A reserved item is claimed by an in-progress WO, not free stock -
        # it must not be double-counted as available.
        product_type = ProductTypeFactory(category=self.category)
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_RESERVED
        )

        response = self.client.get(reverse("producttype-stock-summary"))

        row = self._summary_for(response, product_type.id)
        self.assertEqual(row["total_registered"], 1)
        self.assertEqual(row["available"], 0)

    def test_in_maintenance_items_are_counted_but_not_available(self):
        # WRH-46: an item pulled onto a Maintenance Order still shows in
        # total_registered but has its own bucket - not silently dropped
        # from the breakdown, and not counted as available.
        product_type = ProductTypeFactory(category=self.category)
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_IN_MAINTENANCE
        )

        response = self.client.get(reverse("producttype-stock-summary"))

        row = self._summary_for(response, product_type.id)
        self.assertEqual(row["total_registered"], 1)
        self.assertEqual(row["in_maintenance"], 1)
        self.assertEqual(row["available"], 0)

    def test_product_type_with_zero_items_shows_all_zeros(self):
        # AC-5/TC-05
        product_type = ProductTypeFactory(category=self.category)

        response = self.client.get(reverse("producttype-stock-summary"))

        row = self._summary_for(response, product_type.id)
        self.assertEqual(row["total_registered"], 0)
        self.assertEqual(row["out"], 0)
        self.assertEqual(row["damaged"], 0)
        self.assertEqual(row["missing"], 0)
        self.assertEqual(row["available"], 0)
        self.assertEqual(row["in_maintenance"], 0)
        self.assertEqual(row["written_off"], 0)

    def test_written_off_items_are_counted_but_not_available(self):
        # WRH-74/AC-2/TC-02: a written-off item still shows in
        # total_registered but has its own bucket, and isn't counted as
        # available.
        product_type = ProductTypeFactory(category=self.category)
        SerializedItemFactory(
            product_type=product_type, status=SerializedItem.STATUS_WRITTEN_OFF
        )

        response = self.client.get(reverse("producttype-stock-summary"))

        row = self._summary_for(response, product_type.id)
        self.assertEqual(row["total_registered"], 1)
        self.assertEqual(row["written_off"], 1)
        self.assertEqual(row["available"], 0)

    def test_no_product_types_shows_empty_list(self):
        # AC-6/TC-06
        response = self.client.get(reverse("producttype-stock-summary"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_archived_product_types_are_excluded(self):
        # TC-07: matches the default list's archived-exclusion convention.
        archived = ProductTypeFactory(category=self.category, archived=True)
        active = ProductTypeFactory(category=self.category)

        response = self.client.get(reverse("producttype-stock-summary"))

        ids = [row["id"] for row in response.data]
        self.assertIn(active.id, ids)
        self.assertNotIn(archived.id, ids)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(reverse("producttype-stock-summary"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
