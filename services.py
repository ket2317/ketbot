from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from calendar_service import CalendarService, CalendarServiceError
from config import Config
from models import Cita, Cliente, Servicio


class AppointmentError(Exception):
    status_code = 400


class AvailabilityError(AppointmentError):
    status_code = 409


class ServiceNotFoundError(AppointmentError):
    status_code = 400


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


def get_active_services(session: Session, cliente: Cliente) -> list[Servicio]:
    return list(
        session.scalars(
            select(Servicio)
            .where(Servicio.cliente_id == cliente.id, Servicio.activo.is_(True))
            .order_by(Servicio.nombre)
        )
    )


def serialize_service(servicio: Servicio) -> dict[str, Any]:
    price = None
    if servicio.precio is not None:
        price = int(servicio.precio) if servicio.precio == servicio.precio.to_integral_value() else float(servicio.precio)

    return {
        "id": servicio.id,
        "nombre": servicio.nombre,
        "precio": price,
        "duracion_minutos": servicio.duracion_minutos,
    }


def appointment_end(start: datetime, servicio: Servicio | None) -> datetime:
    minutes = servicio.duracion_minutos if servicio else Config.DEFAULT_APPOINTMENT_MINUTES
    if minutes <= 0:
        raise ValueError("La duración de la cita debe ser mayor a cero.")
    return start + timedelta(minutes=minutes)


def ensure_inside_business_hours(cliente: Cliente, start: datetime, end: datetime) -> None:
    if start.date() != end.date() or start.time() < cliente.horario_inicio or end.time() > cliente.horario_fin:
        raise AvailabilityError(
            f"El horario debe estar entre {cliente.horario_inicio.strftime('%H:%M')} "
            f"y {cliente.horario_fin.strftime('%H:%M')}."
        )


def check_client_availability(cliente: Cliente, start: datetime, end: datetime) -> bool:
    ensure_inside_business_hours(cliente, start, end)
    calendar = CalendarService(
        timezone=ZoneInfo(cliente.timezone),
        calendar_id=cliente.calendar_id,
        credentials_file=cliente.credentials_file,
        credentials_env_var=cliente.credentials_env_var,
    )
    return calendar.is_available(start, end)


def create_appointment(session: Session, cliente: Cliente, payload: dict[str, Any]) -> dict[str, Any]:
    servicio = get_service_for_payload(session, cliente, payload)
    start = parse_start(payload["fecha"], payload["hora"], cliente.timezone)
    end = appointment_end(start, servicio)

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

    if not check_client_availability(cliente, start, end):
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

    return {"event": event, "cita": cita}
