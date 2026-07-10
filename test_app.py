import unittest
import os
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

test_db = tempfile.NamedTemporaryFile(prefix="ketbot-test-", suffix=".db", delete=True)
os.environ["DATABASE_URL"] = f"sqlite:///{test_db.name}"

from app import app
from services import phones_match


@contextmanager
def fake_session_scope():
    yield object()


class AppointmentRoutesTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_url_map_registers_update_and_cancel_routes(self):
        rules = {rule.rule: rule for rule in app.url_map.iter_rules()}

        self.assertIn("/update-appointment", rules)
        self.assertIn("POST", rules["/update-appointment"].methods)
        self.assertIn("/cancel-appointment", rules)
        self.assertIn("POST", rules["/cancel-appointment"].methods)

    @patch("routes.appointments.create_appointment")
    def test_create_appointment_rejects_reschedule_user_message(self, create_appointment):
        response = self.client.post(
            "/create-appointment",
            json={
                "assistant_id": "asst_1",
                "telefono": "+52 55 5000 0000",
                "nombre": "Juan",
                "fecha": "2026-07-22",
                "hora": "18:00",
                "user_message": "quiero cambiar mi cita",
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["success"])
        self.assertTrue(payload["wrong_tool"])
        self.assertEqual(payload["expected_tool"], "update_appointment")
        create_appointment.assert_not_called()

    @patch("routes.appointments.create_appointment")
    def test_create_appointment_rejects_cancel_user_message(self, create_appointment):
        response = self.client.post(
            "/create-appointment",
            json={
                "assistant_id": "asst_1",
                "telefono": "+52 55 5000 0000",
                "nombre": "Juan",
                "fecha": "2026-07-22",
                "hora": "18:00",
                "user_message": "quiero cancelar mi cita",
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["success"])
        self.assertTrue(payload["wrong_tool"])
        self.assertEqual(payload["expected_tool"], "cancel_appointment")
        create_appointment.assert_not_called()

    @patch("routes.appointments.session_scope", fake_session_scope)
    @patch("routes.appointments.get_client_for_payload")
    @patch("routes.appointments.create_appointment")
    def test_create_appointment_normal_request_still_creates(self, create_appointment, get_client):
        get_client.return_value = SimpleNamespace(id=1, nombre="KET")
        create_appointment.return_value = {
            "event": {"id": "evt_1", "htmlLink": "https://calendar.test/event"},
            "cita": SimpleNamespace(id=10),
        }

        response = self.client.post(
            "/create-appointment",
            json={
                "assistant_id": "asst_1",
                "telefono": "+52 55 5000 0000",
                "nombre": "Juan",
                "fecha": "2026-07-22",
                "hora": "18:00",
                "user_message": "quiero agendar una cita nueva",
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        create_appointment.assert_called_once()

    @patch("routes.appointments.session_scope", fake_session_scope)
    @patch("routes.appointments.get_client_for_payload")
    @patch("routes.appointments.update_appointment")
    def test_update_appointment_route_uses_update_service(self, update_appointment, get_client):
        get_client.return_value = SimpleNamespace(id=1, nombre="KET")
        update_appointment.return_value = {
            "event": {"htmlLink": "https://calendar.test/updated"},
            "cita": SimpleNamespace(id=10, google_event_id="evt_1"),
        }

        response = self.client.post(
            "/update-appointment",
            json={
                "assistant_id": "asst_1",
                "telefono": "55 5000 0000",
                "event_id": "evt_1",
                "fecha": "2026-07-23",
                "hora": "12:00",
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        update_appointment.assert_called_once()

    @patch("routes.appointments.session_scope", fake_session_scope)
    @patch("routes.appointments.get_client_for_payload")
    @patch("routes.appointments.cancel_appointment")
    def test_cancel_appointment_route_uses_cancel_service(self, cancel_appointment, get_client):
        get_client.return_value = SimpleNamespace(id=1, nombre="KET")
        cancel_appointment.return_value = {
            "cita": SimpleNamespace(id=10, google_event_id="evt_1"),
        }

        response = self.client.post(
            "/cancel-appointment",
            json={
                "assistant_id": "asst_1",
                "telefono": "55 5000 0000",
                "event_id": "evt_1",
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        cancel_appointment.assert_called_once()

    def test_phone_with_country_code_matches_phone_without_country_code(self):
        self.assertTrue(phones_match("+52 1 55-5000-0000", "55 5000 0000"))


if __name__ == "__main__":
    unittest.main()
