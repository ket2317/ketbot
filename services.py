import re
import calendar
import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from calendar_service import CalendarService, CalendarServiceError
from config import Config
from models import Cita, ClientBusinessHour, Cliente, ServiceAvailability, Servicio


class AppointmentError(Exception):
    status_code = 400


class AvailabilityError(AppointmentError):
    status_code = 409


class ServiceNotFoundError(AppointmentError):
    status_code = 400


class AppointmentClarificationError(AppointmentError):
    status_code = 200

    def __init__(self, message: str, appointments: list[dict[str, Any]]):
        super().__init__(message)
        self.appointments = appointments


def require_fields(payload: dict[str, Any], fields: list[str]) -> str | None:
    missing = [field for field in fields if not str(payload.get(field, "")).strip()]
    if missing:
        return f"Faltan campos requeridos: {', '.join(missing)}."
    return None


def parse_start(fecha: str, hora: str, timezone: str) -> datetime:
    try:
        start = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValueError("Formato inválido. Usa fecha YYYY-MM-DD y hora HH:MM.") from exc

    try:
        return start.replace(tzinfo=ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Timezone inválido configurado para el cliente.") from exc


def get_service_for_payload(session: Session, cliente: Cliente, payload: dict[str, Any]) -> Servicio | None:
    servicio_id = payload.get("servicio_id") or payload.get("service_id")
    if servicio_id:
        try:
            parsed_service_id = int(servicio_id)
        except (TypeError, ValueError) as exc:
            raise ServiceNotFoundError("servicio_id debe ser numérico.") from exc

        servicio = session.scalar(
            select(Servicio)
            .where(
                Servicio.id == parsed_service_id,
                Servicio.cliente_id == cliente.id,
                Servicio.activo.is_(True),
            )
        )
        if servicio is None:
            raise ServiceNotFoundError("El servicio solicitado no existe o está inactivo.")
        return servicio

    service_name = payload.get("servicio") or payload.get("motivo")
    if isinstance(service_name, str) and service_name.strip():
        return session.scalar(
            select(Servicio).where(
                Servicio.cliente_id == cliente.id,
                Servicio.nombre == service_name.strip(),
                Servicio.activo.is_(True),
            )
        )

    return None


def get_active_services(session: Session, cliente: Cliente, canal: str | None = None) -> list[Servicio]:
    query = select(Servicio).where(Servicio.cliente_id == cliente.id, Servicio.activo.is_(True))
    normalized_channel = (canal or "").strip().lower()
    if normalized_channel == "vapi":
        normalized_channel = "llamada"
    if normalized_channel in ("llamada", "call", "voice"):
        query = query.where(Servicio.disponible_por_llamada.is_(True))
    elif normalized_channel in ("whatsapp", "wa"):
        query = query.where(Servicio.disponible_por_whatsapp.is_(True))
    return list(session.scalars(query.order_by(Servicio.nombre)))


def serialize_service(servicio: Servicio) -> dict[str, Any]:
    price = None
    if servicio.precio is not None:
        price = int(servicio.precio) if servicio.precio == servicio.precio.to_integral_value() else float(servicio.precio)

    return {
        "id": servicio.id,
        "nombre": servicio.nombre,
        "descripcion": servicio.descripcion,
        "precio": price,
        "duracion_minutos": servicio.duracion_minutos,
        "activo": servicio.activo,
        "requiere_cita": servicio.requiere_cita,
        "disponible_por_llamada": servicio.disponible_por_llamada,
        "disponible_por_whatsapp": servicio.disponible_por_whatsapp,
    }


def appointment_end(start: datetime, servicio: Servicio | None) -> datetime:
    minutes = servicio.duracion_minutos if servicio else Config.DEFAULT_APPOINTMENT_MINUTES
    if servicio is None and getattr(start, "tzinfo", None):
        minutes = minutes
    if minutes <= 0:
        raise ValueError("La duración de la cita debe ser mayor a cero.")
    return start + timedelta(minutes=minutes)


def appointment_end_for_client(start: datetime, cliente: Cliente, servicio: Servicio | None) -> datetime:
    minutes = servicio.duracion_minutos if servicio else (cliente.duracion_cita_minutos or Config.DEFAULT_APPOINTMENT_MINUTES)
    if minutes <= 0:
        raise ValueError("La duración de la cita debe ser mayor a cero.")
    return start + timedelta(minutes=minutes)


def ensure_inside_business_hours(cliente: Cliente, start: datetime, end: datetime) -> None:
    business_hours = business_hours_for_datetime(cliente, start)
    if business_hours is None or not business_hours.is_open:
        raise AvailabilityError("El negocio está cerrado ese día.")
    if start.date() != end.date() or start.time() < business_hours.start_time or end.time() > business_hours.end_time:
        raise AvailabilityError(
            f"El horario debe estar entre {business_hours.start_time.strftime('%H:%M')} "
            f"y {business_hours.end_time.strftime('%H:%M')}."
        )
    if _overlaps_break(start.time(), end.time(), business_hours.breaks_json):
        raise AvailabilityError("Ese horario cruza un descanso del negocio.")


def business_hours_for_datetime(cliente: Cliente, start: datetime) -> ClientBusinessHour | None:
    for row in getattr(cliente, "horarios", []) or []:
        if row.weekday == start.weekday():
            return row
    return ClientBusinessHour(
        cliente_id=cliente.id,
        weekday=start.weekday(),
        is_open=True,
        start_time=cliente.horario_inicio,
        end_time=cliente.horario_fin,
        breaks_json="[]",
    )


def check_client_availability(
    cliente: Cliente,
    start: datetime,
    end: datetime,
    exclude_event_id: str | None = None,
    servicio: Servicio | None = None,
    canal: str | None = None,
) -> bool:
    if not cliente.activo:
        raise AvailabilityError("El negocio está inactivo y no puede recibir nuevas citas.")
    ensure_inside_business_hours(cliente, start, end)
    ensure_service_available(cliente, servicio, start, end, canal)
    calendar = CalendarService(
        timezone=ZoneInfo(cliente.timezone),
        calendar_id=cliente.calendar_id,
        credentials_file=cliente.credentials_file,
        credentials_env_var=cliente.credentials_env_var,
    )
    return calendar.is_available(start, end, exclude_event_id=exclude_event_id)


def ensure_service_available(
    cliente: Cliente,
    servicio: Servicio | None,
    start: datetime,
    end: datetime,
    canal: str | None,
) -> None:
    if servicio is None:
        return
    if servicio.cliente_id != cliente.id:
        raise ServiceNotFoundError("El servicio solicitado no pertenece a este negocio.")
    if not servicio.activo:
        raise ServiceNotFoundError("Ese servicio no está activo.")
    normalized_channel = (canal or "").strip().lower()
    if normalized_channel == "vapi":
        normalized_channel = "llamada"
    if normalized_channel in ("llamada", "call", "voice") and not servicio.disponible_por_llamada:
        raise AvailabilityError("Ese servicio no está disponible por llamada.")
    if normalized_channel in ("whatsapp", "wa") and not servicio.disponible_por_whatsapp:
        raise AvailabilityError("Ese servicio no está disponible por WhatsApp.")

    availability = service_availability_for_datetime(servicio, start)
    if availability is None:
        return
    if not availability.is_available:
        days = next_available_service_days(servicio, start.weekday())
        suffix = f" Los próximos días disponibles son {', '.join(days)}." if days else ""
        raise AvailabilityError(f"Ese servicio no está disponible ese día.{suffix}")
    if availability.use_business_hours:
        return
    if not availability.start_time or not availability.end_time:
        raise AvailabilityError("Ese servicio no tiene horario configurado para ese día.")
    if start.time() < availability.start_time or end.time() > availability.end_time:
        raise AvailabilityError(
            f"Ese servicio solo está disponible de {availability.start_time.strftime('%H:%M')} "
            f"a {availability.end_time.strftime('%H:%M')} ese día."
        )


def service_availability_for_datetime(servicio: Servicio, start: datetime) -> ServiceAvailability | None:
    for row in getattr(servicio, "disponibilidad", []) or []:
        if row.weekday == start.weekday():
            return row
    return None


def next_available_service_days(servicio: Servicio, current_weekday: int) -> list[str]:
    day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    rows = {row.weekday: row for row in getattr(servicio, "disponibilidad", []) or []}
    days = []
    for offset in range(1, 8):
        weekday = (current_weekday + offset) % 7
        row = rows.get(weekday)
        if row is None or row.is_available:
            days.append(day_names[weekday])
        if len(days) == 3:
            break
    return days


def _overlaps_break(start_time, end_time, breaks_json: str | None) -> bool:
    if not breaks_json:
        return False
    try:
        breaks = json.loads(breaks_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(breaks, list):
        return False
    for item in breaks:
        if not isinstance(item, dict):
            continue
        try:
            break_start = datetime.strptime(str(item.get("start")), "%H:%M").time()
            break_end = datetime.strptime(str(item.get("end")), "%H:%M").time()
        except (TypeError, ValueError):
            continue
        if start_time < break_end and end_time > break_start:
            return True
    return False


def create_appointment(session: Session, cliente: Cliente, payload: dict[str, Any]) -> dict[str, Any]:
    servicio = get_service_for_payload(session, cliente, payload)
    start = parse_start(payload["fecha"], payload["hora"], cliente.timezone)
    end = appointment_end_for_client(start, cliente, servicio)

    motivo = payload.get("motivo") or payload.get("servicio") or (servicio.nombre if servicio else "Cita")
    cita = Cita(
        cliente_id=cliente.id,
        nombre_cliente=payload["nombre"],
        telefono=payload["telefono"],
        servicio_id=servicio.id if servicio else None,
        fecha=start.date(),
        hora=start.time().replace(tzinfo=None),
        estado="pendiente",
    )
    session.add(cita)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise AvailabilityError(f"Ese horario ya no está disponible: {payload['fecha']} {payload['hora']}.") from exc

    if not check_client_availability(cliente, start, end, servicio=servicio, canal=payload.get("canal")):
        cita.estado = "rechazada"
        raise AvailabilityError(f"Ese horario ya no está disponible: {payload['fecha']} {payload['hora']}.")

    calendar = CalendarService(
        timezone=ZoneInfo(cliente.timezone),
        calendar_id=cliente.calendar_id,
        credentials_file=cliente.credentials_file,
        credentials_env_var=cliente.credentials_env_var,
    )
    event = calendar.create_calendar_event(
        nombre=payload["nombre"],
        telefono=payload["telefono"],
        motivo=motivo,
        start=start,
        end=end,
    )
    cita.google_event_id = event.get("id")
    cita.estado = "agendada"

    return {"event": event, "cita": cita, "servicio": servicio}


def update_appointment(session: Session, cliente: Cliente, payload: dict[str, Any]) -> dict[str, Any]:
    cita = _find_appointment(session, cliente, payload, use_payload_datetime=False)
    servicio = get_service_for_payload(session, cliente, payload)
    start = parse_start(payload["fecha"], payload["hora"], cliente.timezone)
    end = appointment_end_for_client(start, cliente, servicio or cita.servicio)

    if not check_client_availability(
        cliente,
        start,
        end,
        exclude_event_id=cita.google_event_id,
        servicio=servicio or cita.servicio,
        canal=payload.get("canal"),
    ):
        raise AvailabilityError(f"Ese horario ya no está disponible: {payload['fecha']} {payload['hora']}.")

    motivo = payload.get("motivo") or payload.get("servicio") or (servicio.nombre if servicio else "Cita")
    event = {}
    if cita.google_event_id:
        calendar = CalendarService(
            timezone=ZoneInfo(cliente.timezone),
            calendar_id=cliente.calendar_id,
            credentials_file=cliente.credentials_file,
            credentials_env_var=cliente.credentials_env_var,
        )
        event = calendar.update_calendar_event(
            event_id=cita.google_event_id,
            nombre=cita.nombre_cliente,
            telefono=cita.telefono,
            motivo=motivo,
            start=start,
            end=end,
        )

    if servicio:
        cita.servicio_id = servicio.id
    cita.fecha = start.date()
    cita.hora = start.time().replace(tzinfo=None)
    cita.estado = "agendada"
    return {"event": event, "cita": cita, "servicio": servicio or cita.servicio}


def cancel_appointment(session: Session, cliente: Cliente, payload: dict[str, Any]) -> dict[str, Any]:
    cita = _find_appointment(session, cliente, payload, use_payload_datetime=True)
    if cita.google_event_id:
        calendar = CalendarService(
            timezone=ZoneInfo(cliente.timezone),
            calendar_id=cliente.calendar_id,
            credentials_file=cliente.credentials_file,
            credentials_env_var=cliente.credentials_env_var,
        )
        calendar.delete_calendar_event(cita.google_event_id)

    cita.estado = "cancelada"
    return {"cita": cita, "servicio": cita.servicio}


def serialize_appointment(cita: Cita) -> dict[str, Any]:
    return {
        "cita_id": cita.id,
        "event_id": cita.google_event_id,
        "fecha": cita.fecha.isoformat(),
        "hora": cita.hora.strftime("%H:%M"),
        "nombre": cita.nombre_cliente,
        "telefono": cita.telefono,
        "motivo": cita.servicio.nombre if cita.servicio else "Cita",
    }


def normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) > 10 and digits.startswith("521"):
        digits = digits[3:]
    elif len(digits) > 10 and digits.startswith("52"):
        digits = digits[2:]
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def _phones_match(left: str | None, right: str | None) -> bool:
    left_normalized = normalize_phone(left)
    right_normalized = normalize_phone(right)
    return bool(left_normalized and right_normalized and left_normalized == right_normalized)


def phones_match(left: str | None, right: str | None) -> bool:
    return _phones_match(left, right)


def _find_appointment(session: Session, cliente: Cliente, payload: dict[str, Any], use_payload_datetime: bool) -> Cita:
    cita_id = payload.get("cita_id") or payload.get("appointment_id")
    if cita_id:
        try:
            parsed_cita_id = int(cita_id)
        except (TypeError, ValueError) as exc:
            raise AvailabilityError("cita_id debe ser numérico.") from exc
        cita = session.get(Cita, parsed_cita_id)
        if cita and cita.cliente_id == cliente.id:
            return cita

    google_event_id = payload.get("event_id") or payload.get("google_event_id")
    if google_event_id:
        cita = session.scalar(
            select(Cita).where(
                Cita.cliente_id == cliente.id,
                Cita.google_event_id == str(google_event_id),
                Cita.estado != "cancelada",
            )
        )
        if cita:
            return cita

    telefono = str(payload.get("telefono", "")).strip()
    telefono_normalizado = normalize_phone(telefono)
    if not telefono_normalizado:
        raise AvailabilityError("Necesito teléfono, cita_id o google_event_id para encontrar la cita.")

    lookup_fecha = payload.get("fecha_actual") or (payload.get("fecha") if use_payload_datetime else None)
    lookup_hora = payload.get("hora_actual") or (payload.get("hora") if use_payload_datetime else None)
    lookup_start = None
    lookup_date = None
    lookup_time = None
    if lookup_fecha and lookup_hora:
        lookup_start = parse_start(str(lookup_fecha), str(lookup_hora), cliente.timezone)
        lookup_date = lookup_start.date()
        lookup_time = lookup_start.time().replace(tzinfo=None)
    elif lookup_fecha:
        try:
            lookup_date = date.fromisoformat(str(lookup_fecha))
        except ValueError as exc:
            raise ValueError("Formato inválido. Usa fecha YYYY-MM-DD.") from exc

    lookup_month = payload.get("lookup_month")
    lookup_year = payload.get("lookup_year")
    if lookup_month and lookup_year:
        try:
            lookup_month = int(lookup_month)
            lookup_year = int(lookup_year)
            month_last_day = calendar.monthrange(lookup_year, lookup_month)[1]
        except (TypeError, ValueError) as exc:
            raise ValueError("lookup_month y lookup_year deben ser valores válidos.") from exc
    else:
        lookup_month = None
        lookup_year = None
        month_last_day = None

    current_date = _client_today(cliente.timezone)

    query = (
        select(Cita)
        .where(Cita.cliente_id == cliente.id, Cita.estado.in_(("agendada", "pendiente")))
        .order_by(Cita.fecha.asc(), Cita.hora.asc())
    )
    if lookup_date:
        query = query.where(Cita.fecha == lookup_date)
        if lookup_time:
            query = query.where(Cita.hora == lookup_time)
    elif lookup_month and lookup_year:
        query = query.where(
            Cita.fecha >= date(lookup_year, lookup_month, 1),
            Cita.fecha <= date(lookup_year, lookup_month, month_last_day),
        )
    else:
        query = query.where(Cita.fecha >= current_date)

    candidates = [
        cita
        for cita in session.scalars(query).unique().all()
        if _phones_match(telefono, cita.telefono)
    ]
    candidates = _filter_candidates_by_date(candidates, lookup_date, lookup_time, lookup_month, lookup_year)

    nombre = str(payload.get("nombre", "")).strip().lower()
    if nombre:
        candidates = [
            cita for cita in candidates
            if nombre in cita.nombre_cliente.lower() or cita.nombre_cliente.lower() in nombre
        ]

    if not candidates and (lookup_date or lookup_month):
        fallback_query = (
            select(Cita)
            .where(
                Cita.cliente_id == cliente.id,
                Cita.estado.in_(("agendada", "pendiente")),
                Cita.fecha >= current_date,
            )
            .order_by(Cita.fecha.asc(), Cita.hora.asc())
        )
        fallback = [
            cita
            for cita in session.scalars(fallback_query).unique().all()
            if _phones_match(telefono, cita.telefono)
        ]
        if fallback:
            if lookup_month and lookup_year:
                message = "No encontré una cita en ese mes. Estas son las citas futuras que encontré."
            else:
                message = "No encontré una cita con esa fecha u hora. Estas son las citas futuras que encontré."
            raise AppointmentClarificationError(
                message,
                [serialize_appointment(cita) for cita in fallback],
            )

    if not candidates:
        raise AvailabilityError("No encontré una cita activa para actualizar o cancelar.")

    if len(candidates) > 1:
        raise AppointmentClarificationError(
            "Encontré varias citas futuras. Necesito saber cuál quieres modificar o cancelar.",
            [serialize_appointment(cita) for cita in candidates],
        )

    return candidates[0]


def _client_today(timezone: str) -> date:
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return date.today()


def _filter_candidates_by_date(
    candidates: list[Cita],
    lookup_date: date | None,
    lookup_time,
    lookup_month: int | None,
    lookup_year: int | None,
) -> list[Cita]:
    filtered = candidates
    if lookup_date:
        filtered = [cita for cita in filtered if cita.fecha == lookup_date]
        if lookup_time:
            filtered = [cita for cita in filtered if cita.hora == lookup_time]
    elif lookup_month and lookup_year:
        filtered = [
            cita for cita in filtered
            if cita.fecha.month == lookup_month and cita.fecha.year == lookup_year
        ]
    return filtered
