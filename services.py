from datetime import date, datetime, timedelta
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


def check_client_availability(cliente: Cliente, start: datetime, end: datetime, exclude_event_id: str | None = None) -> bool:
    ensure_inside_business_hours(cliente, start, end)
    calendar = CalendarService(
        timezone=ZoneInfo(cliente.timezone),
        calendar_id=cliente.calendar_id,
        credentials_file=cliente.credentials_file,
        credentials_env_var=cliente.credentials_env_var,
    )
    return calendar.is_available(start, end, exclude_event_id=exclude_event_id)


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


def update_appointment(session: Session, cliente: Cliente, payload: dict[str, Any]) -> dict[str, Any]:
    cita = _find_appointment(session, cliente, payload)
    servicio = get_service_for_payload(session, cliente, payload)
    start = parse_start(payload["fecha"], payload["hora"], cliente.timezone)
    end = appointment_end(start, servicio or cita.servicio)

    if not check_client_availability(cliente, start, end, exclude_event_id=cita.google_event_id):
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
    return {"event": event, "cita": cita}


def cancel_appointment(session: Session, cliente: Cliente, payload: dict[str, Any]) -> dict[str, Any]:
    cita = _find_appointment(session, cliente, payload)
    if cita.google_event_id:
        calendar = CalendarService(
            timezone=ZoneInfo(cliente.timezone),
            calendar_id=cliente.calendar_id,
            credentials_file=cliente.credentials_file,
            credentials_env_var=cliente.credentials_env_var,
        )
        calendar.delete_calendar_event(cita.google_event_id)

    cita.estado = "cancelada"
    return {"cita": cita}


def _find_appointment(session: Session, cliente: Cliente, payload: dict[str, Any]) -> Cita:
    cita_id = payload.get("cita_id") or payload.get("appointment_id")
    if cita_id:
        try:
            parsed_cita_id = int(cita_id)
        except (TypeError, ValueError) as exc:
            raise AvailabilityError("cita_id debe ser numérico.") from exc
        cita = session.get(Cita, parsed_cita_id)
        if cita and cita.cliente_id == cliente.id:
            return cita

    google_event_id = payload.get("google_event_id")
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
    if not telefono:
        raise AvailabilityError("Necesito teléfono, cita_id o google_event_id para encontrar la cita.")

    query = select(Cita).where(Cita.cliente_id == cliente.id, Cita.estado.in_(("agendada", "pendiente")))
    if telefono:
        query = query.where(Cita.telefono == telefono)

    if payload.get("fecha") and payload.get("hora"):
        start = parse_start(payload["fecha"], payload["hora"], cliente.timezone)
        query = query.where(Cita.fecha == start.date(), Cita.hora == start.time().replace(tzinfo=None))
    else:
        query = query.where(Cita.fecha >= date.today()).order_by(Cita.fecha.asc(), Cita.hora.asc())

    cita = session.scalar(query)
    if not cita:
        raise AvailabilityError("No encontré una cita activa para actualizar o cancelar.")
    return cita
