import unittest
import os
import tempfile
from contextlib import contextmanager
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

test_db = tempfile.NamedTemporaryFile(prefix="ketbot-test-", suffix=".db", delete=True)
os.environ["DATABASE_URL"] = f"sqlite:///{test_db.name}"

from app import app
from date_resolver import resolve_date_context
from services import AppointmentClarificationError, _find_appointment, phones_match


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
        get_client.return_value = SimpleNamespace(id=1, nombre="KET", timezone="America/Mexico_City")
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
        get_client.return_value = SimpleNamespace(id=1, nombre="KET", timezone="America/Mexico_City")
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
        get_client.return_value = SimpleNamespace(id=1, nombre="KET", timezone="America/Mexico_City")
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


class DateResolverTest(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 7, 10)
        self.timezone = "America/Mexico_City"

    def assertResolved(self, text, expected):
        resolution = resolve_date_context(text, self.today, self.timezone)
        self.assertTrue(resolution.success, resolution.message)
        self.assertEqual(resolution.date, expected)
        self.assertEqual(resolution.timezone, self.timezone)
        self.assertEqual(resolution.interpreted_from, text)

    def test_relative_dates(self):
        self.assertResolved("hoy", date(2026, 7, 10))
        self.assertResolved("mañana", date(2026, 7, 11))
        self.assertResolved("pasado mañana", date(2026, 7, 12))

    def test_weekdays_current_and_next_week(self):
        self.assertResolved("viernes", date(2026, 7, 10))
        self.assertResolved("este viernes", date(2026, 7, 10))
        self.assertResolved("próximo viernes", date(2026, 7, 17))

    def test_month_without_year_uses_future_year_when_needed(self):
        self.assertResolved("20 de agosto", date(2026, 8, 20))
        self.assertResolved("julio 20", date(2026, 7, 20))
        self.assertResolved("20/07", date(2026, 7, 20))
        self.assertResolved("20-06", date(2027, 6, 20))

    def test_day_only_uses_current_or_next_month(self):
        self.assertResolved("el 15", date(2026, 7, 15))
        self.assertResolved("el día 5", date(2026, 8, 5))

    def test_month_only_and_ranges_need_clarification(self):
        current_month = resolve_date_context("este mes", self.today, self.timezone)
        self.assertFalse(current_month.success)
        self.assertTrue(current_month.needs_clarification)
        self.assertEqual(current_month.resolved_month, 7)
        self.assertEqual(current_month.resolved_year, 2026)

        next_month = resolve_date_context("mes que viene", self.today, self.timezone)
        self.assertFalse(next_month.success)
        self.assertTrue(next_month.needs_clarification)
        self.assertEqual(next_month.resolved_month, 8)
        self.assertEqual(next_month.resolved_year, 2026)

        range_result = resolve_date_context("finales de este mes", self.today, self.timezone)
        self.assertFalse(range_result.success)
        self.assertTrue(range_result.needs_clarification)
        self.assertEqual(range_result.resolved_month, 7)
        self.assertEqual(range_result.resolved_year, 2026)

    def test_day_this_month_does_not_create_past_date(self):
        resolution = resolve_date_context("el 5 de este mes", self.today, self.timezone)
        self.assertFalse(resolution.success)
        self.assertTrue(resolution.needs_clarification)
        self.assertEqual(resolution.resolved_month, 7)
        self.assertEqual(resolution.resolved_year, 2026)


class FakeScalars:
    def __init__(self, items):
        self.items = items

    def unique(self):
        return self

    def all(self):
        return self.items


class FakeSession:
    def __init__(self, appointments):
        self.appointments = appointments

    def get(self, model, item_id):
        for appointment in self.appointments:
            if appointment.id == item_id:
                return appointment
        return None

    def scalar(self, query):
        return self.appointments[0] if self.appointments else None

    def scalars(self, query):
        return FakeScalars(self.appointments)


def appointment(appointment_id, fecha, hora, telefono="55 5000 0000", event_id=None, nombre="Juan Perez"):
    return SimpleNamespace(
        id=appointment_id,
        cliente_id=1,
        google_event_id=event_id or f"evt_{appointment_id}",
        fecha=fecha,
        hora=hora,
        telefono=telefono,
        nombre_cliente=nombre,
        servicio=None,
        estado="agendada",
    )


class AppointmentLookupTest(unittest.TestCase):
    def setUp(self):
        self.cliente = SimpleNamespace(id=1, timezone="America/Mexico_City")

    def test_lookup_single_future_appointment_by_phone(self):
        cita = appointment(1, date(2026, 7, 15), time(13, 0))
        result = _find_appointment(FakeSession([cita]), self.cliente, {"telefono": "+52 55 5000 0000"}, False)
        self.assertEqual(result.id, 1)

    def test_update_lookup_by_event_id(self):
        cita = appointment(1, date(2026, 7, 15), time(13, 0), event_id="evt_original")
        result = _find_appointment(FakeSession([cita]), self.cliente, {"event_id": "evt_original"}, False)
        self.assertEqual(result.id, 1)

    def test_cancel_lookup_by_event_id(self):
        cita = appointment(1, date(2026, 7, 15), time(13, 0), event_id="evt_original")
        result = _find_appointment(FakeSession([cita]), self.cliente, {"event_id": "evt_original"}, True)
        self.assertEqual(result.id, 1)

    def test_lookup_by_current_date_and_time(self):
        cita = appointment(1, date(2026, 7, 15), time(13, 0))
        payload = {"telefono": "55 5000 0000", "fecha_actual": "2026-07-15", "hora_actual": "13:00"}
        result = _find_appointment(FakeSession([cita]), self.cliente, payload, False)
        self.assertEqual(result.id, 1)

    def test_cancel_lookup_by_date_and_time(self):
        cita = appointment(1, date(2026, 7, 15), time(13, 0))
        payload = {"telefono": "55 5000 0000", "fecha": "2026-07-15", "hora": "13:00"}
        result = _find_appointment(FakeSession([cita]), self.cliente, payload, True)
        self.assertEqual(result.id, 1)

    def test_lookup_by_month_filter(self):
        cita = appointment(1, date(2026, 7, 15), time(13, 0))
        payload = {"telefono": "55 5000 0000", "lookup_month": 7, "lookup_year": 2026}
        result = _find_appointment(FakeSession([cita]), self.cliente, payload, False)
        self.assertEqual(result.id, 1)

    def test_multiple_future_appointments_need_clarification(self):
        citas = [
            appointment(1, date(2026, 7, 15), time(13, 0)),
            appointment(2, date(2026, 7, 20), time(14, 0)),
        ]
        with self.assertRaises(AppointmentClarificationError) as context:
            _find_appointment(FakeSession(citas), self.cliente, {"telefono": "55 5000 0000"}, False)
        self.assertEqual(len(context.exception.appointments), 2)

    def test_country_code_phone_matches_local_phone(self):
        cita = appointment(1, date(2026, 7, 15), time(13, 0), telefono="55-5000-0000")
        result = _find_appointment(FakeSession([cita]), self.cliente, {"telefono": "+52 55 5000 0000"}, False)
        self.assertEqual(result.id, 1)


if __name__ == "__main__":
    unittest.main()
