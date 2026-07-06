from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class LoginTests(APITestCase):
    def setUp(self):
        self.password = "correct-horse-battery-staple"
        self.user = User.objects.create_user(
            username="jane",
            email="jane@example.com",
            password=self.password,
            first_name="Jane",
        )

    def test_login_with_valid_credentials_succeeds(self):
        # TC-01: successful login with valid credentials
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], self.user.email)

    def test_login_response_includes_user_identity(self):
        # TC-02: identity available for the frontend to display in the nav bar
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )

        self.assertEqual(response.data["first_name"], "Jane")
        self.assertEqual(response.data["username"], "jane")

    def test_login_with_wrong_password_is_rejected(self):
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_session_cookie_is_httponly(self):
        # TC-07: session token must not be readable from JS
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )

        self.assertTrue(response.cookies["sessionid"]["httponly"])

    def test_session_persists_across_requests(self):
        # TC-03: session persists across subsequent requests (e.g. page refresh)
        self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], self.user.email)

    def test_logout_clears_session(self):
        # TC-04: logout clears the session
        self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )

        logout_response = self.client.post(reverse("logout"))
        self.assertEqual(logout_response.status_code, status.HTTP_204_NO_CONTENT)

        me_response = self.client.get(reverse("me"))
        # Session-only auth has no WWW-Authenticate challenge, so DRF denies
        # unauthenticated requests with 403 rather than 401 - see DRF's
        # exceptions.NotAuthenticated / authenticate_header docs.
        self.assertEqual(me_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_protected_route_requires_session(self):
        # TC-06: no active session -> rejected, frontend redirects to login
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_me_identifies_the_logged_in_user(self):
        # AC-4 groundwork: request.user resolves to the authenticated identity,
        # which is what future action-attribution (e.g. Transaction Log) will
        # key off of. The Transaction Log epic itself is out of scope here.
        self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )

        response = self.client.get(reverse("me"))

        self.assertEqual(response.data["id"], self.user.id)
