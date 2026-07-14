from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from inventory.models import Category
from inventory.tests.factories import CategoryFactory, ProductTypeFactory


class CategoryTests(APITestCase):
    def setUp(self):
        self.password = "correct-horse-battery-staple"
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password=self.password
        )
        self.client.force_authenticate(user=self.user)

    def _login_with_csrf_enforced_client(self):
        # force_authenticate (used by every other test here) bypasses CSRF
        # entirely, so it can't prove a real browser session - which must
        # send X-CSRFToken - can actually create a category.
        client = APIClient(enforce_csrf_checks=True)
        login_response = client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        return client, client.cookies["csrftoken"].value

    def test_create_category_with_all_fields(self):
        # TC-01: create with name and description
        response = self.client.post(
            reverse("category-list"),
            {"name": "Lighting", "description": "Moving lights and fixtures"},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Lighting")
        self.assertEqual(response.data["description"], "Moving lights and fixtures")

    def test_create_category_with_name_only(self):
        # TC-02/AC-3: description is optional
        response = self.client.post(reverse("category-list"), {"name": "Lighting"})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["description"], "")

    def test_create_category_succeeds_via_real_csrf_flow(self):
        client, csrf_token = self._login_with_csrf_enforced_client()

        response = client.post(
            reverse("category-list"),
            {"name": "Lighting"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_category_is_rejected_without_csrf_token(self):
        # A real session without the X-CSRFToken header must still be
        # rejected - proves CSRF enforcement is actually active on this
        # endpoint, not just that it succeeds when the token is sent.
        client, _csrf_token = self._login_with_csrf_enforced_client()

        response = client.post(reverse("category-list"), {"name": "Lighting"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retrieve_and_update_routes_are_not_registered(self):
        # CategoryViewSet mixes in list/create/destroy plus the archive
        # action (WRH-62), so the detail route now exists (DELETE works)
        # but retrieve/update aren't needed by any story yet - GET/PUT/PATCH
        # correctly 405 (method not allowed on an existing route) rather
        # than 404 (route doesn't exist at all).
        category = CategoryFactory()
        detail_url = f"/api/categories/{category.pk}/"

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

    def test_create_category_with_duplicate_name_is_rejected(self):
        # AC-2/TC-02
        CategoryFactory(name="Lighting")

        response = self.client.post(reverse("category-list"), {"name": "Lighting"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["name"], ["A category with this name already exists."]
        )

    def test_create_category_without_name_is_rejected(self):
        # AC-1/TC-01
        response = self.client.post(reverse("category-list"), {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["name"], ["Name is required."])

    def test_create_category_with_empty_name_is_rejected(self):
        # AC-1/TC-01: empty string, not just an omitted field
        response = self.client.post(reverse("category-list"), {"name": ""})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["name"], ["Name is required."])

    def test_delete_category_with_assigned_product_types_is_blocked(self):
        # AC-3/TC-03
        category = CategoryFactory()
        ProductTypeFactory.create_batch(3, category=category)

        response = self.client.delete(f"/api/categories/{category.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "Cannot delete — 3 product types are assigned to this category. "
            "Archive it instead.",
        )
        self.assertEqual(response.data["assigned_product_type_count"], 3)
        self.assertTrue(Category.objects.filter(pk=category.pk).exists())

    def test_delete_blocked_message_is_singular_for_one_product_type(self):
        # AC-3: "1 product types are" reads wrong - singular noun/verb only
        # when exactly one Product Type is assigned.
        category = CategoryFactory()
        ProductTypeFactory(category=category)

        response = self.client.delete(f"/api/categories/{category.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "Cannot delete — 1 product type is assigned to this category. "
            "Archive it instead.",
        )

    def test_delete_category_with_zero_product_types_succeeds(self):
        # AC-5/TC-05
        category = CategoryFactory()

        response = self.client.delete(f"/api/categories/{category.pk}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())

    def test_delete_with_unrelated_search_query_param_still_succeeds(self):
        # A stray ?search= param (e.g. left over from the list view's search
        # box) must not affect get_object()'s pk lookup on the detail route -
        # SearchFilter is only meant to scope the list action.
        category = CategoryFactory(name="Lighting")

        response = self.client.delete(
            f"/api/categories/{category.pk}/", {"search": "does-not-match"}
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())

    def test_archive_with_unrelated_search_query_param_still_succeeds(self):
        category = CategoryFactory(name="Lighting")

        response = self.client.post(
            f"/api/categories/{category.pk}/archive/", {"search": "does-not-match"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_race_with_concurrent_assignment_is_blocked_not_500(self):
        # AC-3: if a Product Type gets assigned between the count() check and
        # the actual delete (concurrent request), on_delete=PROTECT raises
        # ProtectedError inside perform_destroy() - this must still surface
        # as the same structured 400, not an unhandled 500.
        category = CategoryFactory()

        with patch(
            "inventory.views.CategoryViewSet.perform_destroy",
            side_effect=ProtectedError("protected", set()),
        ):
            response = self.client.delete(f"/api/categories/{category.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("assigned_product_type_count", response.data)
        self.assertTrue(Category.objects.filter(pk=category.pk).exists())

    def test_archive_category_keeps_product_types_intact(self):
        # AC-4/TC-04
        category = CategoryFactory()
        product_type = ProductTypeFactory(category=category)

        response = self.client.post(f"/api/categories/{category.pk}/archive/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["archived"])
        category.refresh_from_db()
        self.assertTrue(category.archived)
        product_type.refresh_from_db()
        self.assertEqual(product_type.category_id, category.id)

    def test_archived_category_hidden_from_default_list(self):
        # AC-4/AC-6/TC-06: same list endpoint backs the active-list view
        # and the Product Type form's category selector.
        archived = CategoryFactory(archived=True)
        active = CategoryFactory()

        response = self.client.get(reverse("category-list"))

        ids = [item["id"] for item in response.data]
        self.assertIn(active.id, ids)
        self.assertNotIn(archived.id, ids)

    def test_created_category_appears_in_list(self):
        # AC-1: new category appears in the category list
        category = CategoryFactory(name="Lighting")

        list_response = self.client.get(reverse("category-list"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(category.id, [item["id"] for item in list_response.data])

    def test_search_matches_by_name(self):
        # TC-03: search filters to categories whose name matches
        CategoryFactory(name="Lighting")
        CategoryFactory(name="Staging")

        response = self.client.get(reverse("category-list"), {"search": "Light"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["name"] for item in response.data], ["Lighting"])

    def test_search_with_no_matches_returns_empty_list(self):
        # TC-04: no matches -> empty list, not an error
        CategoryFactory(name="Lighting")

        response = self.client.get(reverse("category-list"), {"search": "nonexistent"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])
