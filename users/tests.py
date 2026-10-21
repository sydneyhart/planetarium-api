from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

User = get_user_model()
PASSWORD = "Strong-passphrase-123!"


class AccountTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("visitor@example.com", PASSWORD)

    def test_registration_hashes_password_and_ignores_privilege_flags(self):
        response = self.client.post(
            "/api/user/register/",
            {
                "email": "NEW@example.com",
                "password": PASSWORD,
                "is_staff": True,
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email="new@example.com")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertFalse(user.is_staff)
        self.assertNotIn("password", response.data)

    def test_registration_rejects_weak_password_and_duplicate_email(self):
        for payload in (
            {"email": "weak@example.com", "password": "123"},
            {"email": "VISITOR@example.com", "password": PASSWORD},
        ):
            self.assertEqual(self.client.post("/api/user/register/", payload).status_code, 400)

    def test_email_jwt_login_and_profile(self):
        response = self.client.post(
            "/api/user/token/", {"email": "VISITOR@example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["access"])
        self.assertEqual(self.client.get("/api/user/me/").data["email"], self.user.email)

    def test_refresh_rotation_and_logout_blacklist(self):
        tokens = self.client.post(
            "/api/user/token/", {"email": self.user.email, "password": PASSWORD}
        ).data
        rotated = self.client.post("/api/user/token/refresh/", {"refresh": tokens["refresh"]})
        self.assertEqual(rotated.status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/user/token/refresh/", {"refresh": tokens["refresh"]}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post("/api/user/logout/", {"refresh": rotated.data["refresh"]}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/user/token/refresh/", {"refresh": rotated.data["refresh"]}
            ).status_code,
            401,
        )

    def test_invalid_login_and_inactive_user(self):
        self.assertEqual(
            self.client.post(
                "/api/user/token/", {"email": self.user.email, "password": "bad"}
            ).status_code,
            401,
        )
        self.user.is_active = False
        self.user.save()
        self.assertEqual(
            self.client.post(
                "/api/user/token/", {"email": self.user.email, "password": PASSWORD}
            ).status_code,
            401,
        )

    def test_profile_owner_can_update_without_escalation(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch("/api/user/me/", {"first_name": "Sky", "is_staff": True})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Sky")
        self.assertFalse(self.user.is_staff)

    def test_profile_password_change(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch("/api/user/me/", {"password": "New-long-passphrase-456!"})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-long-passphrase-456!"))

    def test_anonymous_profile_rejected_and_admin_email_user_form_works(self):
        self.assertEqual(self.client.get("/api/user/me/").status_code, 401)
        staff = User.objects.create_superuser("admin@example.com", PASSWORD)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin/users/user/add/").status_code, 200)
