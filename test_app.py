import unittest
import os
import tempfile
import base64
import uuid
from contextlib import contextmanager
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

test_db = tempfile.NamedTemporaryFile(prefix="ketbot-test-", suffix=".db", delete=True)
os.environ["DATABASE_URL"] = f"sqlite:///{test_db.name}"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret"
os.environ["SECRET_KEY"] = "test-secret"

from app import app
from config import Config
from database import session_scope as session_scope_for_test
from date_resolver import resolve_date_context
from models import ClientBusinessHour, Cliente, ServiceAvailability, Servicio
from prompt_service import generate_client_prompt
from services import (
    AppointmentClarificationError,
    AvailabilityError,
    ServiceNotFoundError,
    _find_appointment,
    appointment_end_for_client,
    check_client_availability,
    get_active_services,
    phones_match,
)


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


class AdminPanelAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.cliente = Cliente(
            id=1,
            nombre="RPM",
            assistant_id="rpm-test",
            calendar_id="primary",
            credentials_file="credentials/test.json",
            horario_inicio=time(8, 0),
            horario_fin=time(18, 0),
            timezone="America/Mexico_City",
            duracion_cita_minutos=60,
            activo=True,
        )
        self.cliente.horarios = [
            ClientBusinessHour(
                cliente_id=1,
                weekday=0,
                is_open=True,
                start_time=time(8, 0),
                end_time=time(18, 0),
                breaks_json="[]",
            )
        ]
        self.servicio = Servicio(
            id=10,
            cliente_id=1,
            nombre="Alineación",
            precio=500,
            duracion_minutos=60,
            activo=True,
            requiere_cita=True,
            disponible_por_llamada=True,
            disponible_por_whatsapp=False,
        )
        self.servicio.disponibilidad = [
            ServiceAvailability(
                service_id=10,
                weekday=0,
                is_available=True,
                use_business_hours=False,
                start_time=time(9, 0),
                end_time=time(17, 0),
            ),
            ServiceAvailability(
                service_id=10,
                weekday=1,
                is_available=False,
                use_business_hours=True,
            ),
        ]

    def test_service_available_only_specific_days(self):
        start = SimpleNamespace(weekday=lambda: 1, time=lambda: time(10, 0), date=lambda: date(2026, 7, 14))
        end = SimpleNamespace(time=lambda: time(11, 0), date=lambda: date(2026, 7, 14))

        with self.assertRaises(AvailabilityError):
            check_client_availability(self.cliente, start, end, servicio=self.servicio, canal="vapi")

    @patch("services.CalendarService")
    def test_service_available_inside_specific_hours(self, calendar_service):
        calendar_service.return_value.is_available.return_value = True
        start = __import__("datetime").datetime(2026, 7, 13, 10, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/Mexico_City"))
        end = appointment_end_for_client(start, self.cliente, self.servicio)

        self.assertTrue(check_client_availability(self.cliente, start, end, servicio=self.servicio, canal="vapi"))

    def test_service_outside_specific_hours_rejected(self):
        start = __import__("datetime").datetime(2026, 7, 13, 17, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/Mexico_City"))
        end = appointment_end_for_client(start, self.cliente, self.servicio)

        with self.assertRaises(AvailabilityError):
            check_client_availability(self.cliente, start, end, servicio=self.servicio, canal="vapi")

    def test_duration_must_fit_inside_service_hours(self):
        self.servicio.duracion_minutos = 90
        start = __import__("datetime").datetime(2026, 7, 13, 16, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/Mexico_City"))
        end = appointment_end_for_client(start, self.cliente, self.servicio)

        with self.assertRaises(AvailabilityError):
            check_client_availability(self.cliente, start, end, servicio=self.servicio, canal="vapi")

    def test_service_call_only_not_available_on_whatsapp(self):
        start = __import__("datetime").datetime(2026, 7, 13, 10, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/Mexico_City"))
        end = appointment_end_for_client(start, self.cliente, self.servicio)

        with self.assertRaises(AvailabilityError):
            check_client_availability(self.cliente, start, end, servicio=self.servicio, canal="whatsapp")

    def test_business_hours_change_affects_availability(self):
        self.cliente.horarios[0].end_time = time(12, 0)
        start = __import__("datetime").datetime(2026, 7, 13, 12, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/Mexico_City"))
        end = appointment_end_for_client(start, self.cliente, self.servicio)

        with self.assertRaises(AvailabilityError):
            check_client_availability(self.cliente, start, end, servicio=self.servicio, canal="vapi")

    def test_inactive_client_cannot_receive_new_appointments(self):
        self.cliente.activo = False
        start = __import__("datetime").datetime(2026, 7, 13, 10, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/Mexico_City"))
        end = appointment_end_for_client(start, self.cliente, self.servicio)

        with self.assertRaises(AvailabilityError):
            check_client_availability(self.cliente, start, end, servicio=self.servicio, canal="vapi")

    def test_price_change_affects_assistant_prompt(self):
        self.servicio.precio = 750
        prompt = generate_client_prompt(self.cliente, [self.servicio])

        self.assertIn("$750", prompt)


class ServiceCatalogTest(unittest.TestCase):
    def test_create_service_for_client_and_do_not_leak_to_other_client(self):
        suffix = uuid.uuid4().hex
        with session_scope_for_test() as session:
            client_one = Cliente(
                nombre="Cliente Uno",
                assistant_id=f"cliente-uno-{suffix}",
                calendar_id="primary",
                credentials_file="credentials/test.json",
                horario_inicio=time(8, 0),
                horario_fin=time(18, 0),
                timezone="America/Mexico_City",
                duracion_cita_minutos=60,
                activo=True,
            )
            client_two = Cliente(
                nombre="Cliente Dos",
                assistant_id=f"cliente-dos-{suffix}",
                calendar_id="primary",
                credentials_file="credentials/test.json",
                horario_inicio=time(8, 0),
                horario_fin=time(18, 0),
                timezone="America/Mexico_City",
                duracion_cita_minutos=60,
                activo=True,
            )
            session.add_all([client_one, client_two])
            session.flush()
            session.add(
                Servicio(
                    cliente_id=client_one.id,
                    nombre="Cambio de aceite",
                    duracion_minutos=45,
                    activo=True,
                    disponible_por_llamada=True,
                    disponible_por_whatsapp=True,
                    requiere_cita=True,
                )
            )
            session.flush()

            self.assertEqual([service.nombre for service in get_active_services(session, client_one)], ["Cambio de aceite"])
            self.assertEqual(get_active_services(session, client_two), [])

    def test_inactive_service_is_not_offered(self):
        suffix = uuid.uuid4().hex
        with session_scope_for_test() as session:
            client = Cliente(
                nombre="Cliente Servicios",
                assistant_id=f"cliente-servicios-{suffix}",
                calendar_id="primary",
                credentials_file="credentials/test.json",
                horario_inicio=time(8, 0),
                horario_fin=time(18, 0),
                timezone="America/Mexico_City",
                duracion_cita_minutos=60,
                activo=True,
            )
            session.add(client)
            session.flush()
            session.add(
                Servicio(
                    cliente_id=client.id,
                    nombre="Servicio apagado",
                    duracion_minutos=45,
                    activo=False,
                    disponible_por_llamada=True,
                    disponible_por_whatsapp=True,
                    requiere_cita=True,
                )
            )
            session.flush()

            self.assertEqual(get_active_services(session, client), [])


class AdminAuthTest(unittest.TestCase):
    def setUp(self):
        Config.ADMIN_USERNAME = "admin"
        Config.ADMIN_PASSWORD = "secret"
        Config.SECRET_KEY = "test-secret"

    def test_admin_routes_require_authentication(self):
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/admin/")

        self.assertEqual(response.status_code, 401)

    def test_admin_routes_allow_basic_auth(self):
        app.config["TESTING"] = True
        client = app.test_client()
        token = base64.b64encode(b"admin:secret").decode("ascii")

        response = client.get("/admin/", headers={"Authorization": f"Basic {token}"})

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
