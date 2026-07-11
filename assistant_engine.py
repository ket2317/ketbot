import logging
import re
from dataclasses import dataclass
from datetime import date
from datetime import time
from typing import Any

from calendar_service import CalendarServiceError
from clients import ClientLookupError, get_active_client_by_assistant_id
from date_resolver import client_today, normalize_date_text, resolve_date_text
from database import session_scope
from models import Cliente
from services import (
    AvailabilityError,
    ServiceNotFoundError,
    appointment_end_for_client,
    cancel_appointment,
    check_client_availability,
    create_appointment,
    get_active_services,
    get_service_for_payload,
    parse_start,
    update_appointment,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssistantContext:
    telefono: str
    mensaje: str
    canal: str
    assistant_id: str


@dataclass(frozen=True)
class TimeResolution:
    value: str
    ambiguous: bool = False


def generar_respuesta_asistente(telefono: str, mensaje: str, canal: str, assistant_id: str | None = None) -> str:
    try:
        resolved_assistant_id = assistant_id or _assistant_id_for_channel(canal)
    except ValueError as exc:
        logger.warning("assistant_id_missing canal=%s telefono=%s", canal, _mask_phone(telefono))
        return str(exc)

    context = AssistantContext(
        telefono=telefono,
        mensaje=mensaje.strip(),
        canal=canal,
        assistant_id=resolved_assistant_id,
    )
    logger.info(
        "assistant_message_start canal=%s assistant_id=%s telefono=%s message_length=%s",
        canal,
        resolved_assistant_id,
        _mask_phone(telefono),
        len(context.mensaje),
    )

    try:
        with session_scope() as session:
            cliente = get_active_client_by_assistant_id(session, resolved_assistant_id)
            intent = _detect_intent(context.mensaje)
            logger.info(
                "assistant_intent_detected canal=%s assistant_id=%s cliente_id=%s intent=%s",
                canal,
                resolved_assistant_id,
                cliente.id,
                intent,
            )

            if intent == "services":
                servicios = get_active_services(session, cliente, canal)
                return _services_response(servicios)

            if intent == "cancel":
                payload = _appointment_payload(context, cliente)
                cancel_appointment(session, cliente, payload)
                return "Listo, cancelé tu cita."

            if intent == "update":
                payload = _appointment_payload(context, cliente)
                missing = _missing_date_time(payload)
                if missing:
                    return missing
                update_appointment(session, cliente, payload)
                return f"Listo, moví tu cita al {payload['fecha']} a las {payload['hora']}."

            if intent == "create":
                payload = _appointment_payload(context, cliente)
                missing = _missing_date_time(payload)
                if missing:
                    return missing
                payload["nombre"] = "Cliente WhatsApp" if canal == "whatsapp" else "Cliente"
                create_appointment(session, cliente, payload)
                return f"Listo, agendé tu cita para el {payload['fecha']} a las {payload['hora']}."

            if intent == "availability":
                payload = _appointment_payload(context, cliente)
                missing = _missing_date_time(payload)
                if missing:
                    return missing
                servicio = get_service_for_payload(session, cliente, payload)
                start = parse_start(payload["fecha"], payload["hora"], cliente.timezone)
                end = appointment_end_for_client(start, cliente, servicio)
                available = check_client_availability(cliente, start, end, servicio=servicio, canal=canal)
                if available:
                    return f"Sí hay disponibilidad el {payload['fecha']} a las {payload['hora']}."
                return f"No hay disponibilidad el {payload['fecha']} a las {payload['hora']}."

    except ClientLookupError:
        logger.warning("assistant_client_not_found canal=%s assistant_id=%s", canal, resolved_assistant_id)
        return "No pude identificar el negocio para atender este mensaje."
    except (AvailabilityError, ServiceNotFoundError, ValueError) as exc:
        logger.info("assistant_business_error canal=%s assistant_id=%s error=%s", canal, resolved_assistant_id, exc)
        return str(exc)
    except CalendarServiceError:
        logger.exception("assistant_calendar_error canal=%s assistant_id=%s", canal, resolved_assistant_id)
        return "No pude conectar con el calendario en este momento. Intenta de nuevo más tarde."

    return "Puedo ayudarte a consultar disponibilidad, agendar, mover o cancelar una cita. Indícame fecha y hora."


def _assistant_id_for_channel(canal: str) -> str:
    raise ValueError("assistant_id requerido")


def _detect_intent(mensaje: str) -> str:
    normalized = _normalize(mensaje)
    if any(word in normalized for word in ("servicio", "servicios", "precio", "precios", "catalogo")):
        return "services"
    if any(word in normalized for word in ("cancelar", "cancela", "cancelame", "anular")):
        return "cancel"
    if any(word in normalized for word in ("mover", "cambiar", "reprogramar", "modificar")):
        return "update"
    if any(word in normalized for word in ("agendar", "agenda", "reservar", "reserva", "programar", "quiero cita")):
        return "create"
    if any(word in normalized for word in ("disponible", "disponibilidad", "puedes", "pueden", "hay lugar")):
        return "availability"
    if _extract_date_text(mensaje) and _extract_time(mensaje):
        return "availability"
    return "unknown"


def _appointment_payload(context: AssistantContext, cliente: Cliente) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "assistant_id": context.assistant_id,
        "telefono": context.telefono,
        "motivo": context.mensaje,
    }
    fecha = _resolve_message_date(context.mensaje, cliente.timezone)
    hora = _resolve_message_time(context, cliente)
    if fecha:
        payload["fecha"] = fecha
    if hora:
        payload["hora"] = hora
    return payload


def _resolve_message_date(mensaje: str, timezone: str) -> str | None:
    today = client_today(timezone)
    date_text = _extract_date_text(mensaje)
    if not date_text:
        return None
    resolved = resolve_date_text(date_text, today)
    return resolved.isoformat() if resolved else None


def _extract_date_text(mensaje: str) -> str | None:
    normalized = _normalize(mensaje)
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[/-]\d{1,2}\b",
        r"\b\d{1,2}\s+de\s+[a-z]+\b",
        r"\bpasado\s+manana\b",
        r"\bproxima\s+semana\b",
        r"\b(?:este|esta|proximo|proxima)\s+(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b",
        r"\b(?:hoy|manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(0)
    return None


def _resolve_message_time(context: AssistantContext, cliente: Cliente) -> str | None:
    resolved_time = _extract_time(context.mensaje)
    if not resolved_time:
        return None

    if not resolved_time.ambiguous:
        return resolved_time.value

    corrected_time = _correct_ambiguous_time_for_business_hours(
        resolved_time.value,
        cliente.horario_inicio,
        cliente.horario_fin,
    )
    if corrected_time != resolved_time.value:
        logger.info(
            "ambiguous_time_corrected canal=%s assistant_id=%s cliente_id=%s original=%s corrected=%s horario_inicio=%s horario_fin=%s",
            context.canal,
            context.assistant_id,
            cliente.id,
            resolved_time.value,
            corrected_time,
            cliente.horario_inicio.strftime("%H:%M"),
            cliente.horario_fin.strftime("%H:%M"),
        )
    return corrected_time


def _extract_time(mensaje: str) -> TimeResolution | None:
    normalized = _normalize(mensaje)
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", normalized)
    if match:
        return TimeResolution(f"{int(match.group(1)):02d}:{match.group(2)}")

    match = re.search(r"\b(1[0-2]|0?[1-9])\s*(am|pm)\b", normalized)
    if match:
        hour = int(match.group(1))
        if match.group(2) == "pm" and hour != 12:
            hour += 12
        if match.group(2) == "am" and hour == 12:
            hour = 0
        return TimeResolution(f"{hour:02d}:00")

    match = re.search(r"\b(?:a las|alas)\s+([01]?\d|2[0-3])\b", normalized)
    if match:
        hour = int(match.group(1))
        return TimeResolution(f"{hour:02d}:00", ambiguous=1 <= hour <= 11)

    return None


def _correct_ambiguous_time_for_business_hours(value: str, horario_inicio: time, horario_fin: time) -> str:
    parsed_time = time.fromisoformat(value)
    if _time_inside_business_hours(parsed_time, horario_inicio, horario_fin):
        return value

    pm_hour = parsed_time.hour + 12
    if pm_hour > 23:
        return value

    pm_time = parsed_time.replace(hour=pm_hour)
    if _time_inside_business_hours(pm_time, horario_inicio, horario_fin):
        return pm_time.strftime("%H:%M")

    return value


def _time_inside_business_hours(value: time, horario_inicio: time, horario_fin: time) -> bool:
    return horario_inicio <= value < horario_fin


def _missing_date_time(payload: dict[str, Any]) -> str | None:
    if not payload.get("fecha") and not payload.get("hora"):
        return "Indícame la fecha y hora para continuar."
    if not payload.get("fecha"):
        return "Indícame la fecha para continuar."
    if not payload.get("hora"):
        return "Indícame la hora para continuar."
    return None


def _services_response(servicios) -> str:
    if not servicios:
        return "Todavía no hay servicios activos configurados."
    lines = []
    for servicio in servicios[:8]:
        price = f" - ${servicio.precio}" if servicio.precio is not None else ""
        lines.append(f"{servicio.nombre}{price} ({servicio.duracion_minutos} min)")
    return "Servicios disponibles:\n" + "\n".join(lines)


def _normalize(value: str) -> str:
    return normalize_date_text(value)


def _mask_phone(telefono: str) -> str:
    if len(telefono) <= 4:
        return "****"
    return f"***{telefono[-4:]}"
