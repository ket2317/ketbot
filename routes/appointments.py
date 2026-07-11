import logging
from datetime import date

from flask import Blueprint, jsonify, request

from calendar_service import CalendarServiceError
from clients import (
    ClientLookupError,
    MissingAssistantIdError,
    extract_assistant_id,
    get_client_for_payload,
    normalize_vapi_payload,
)
from date_resolver import DATE_FORMAT_RE, client_today, resolve_date_context, resolve_date_text
from database import session_scope
from prompt_service import generate_client_prompt
from services import (
    AppointmentClarificationError,
    AvailabilityError,
    cancel_appointment,
    check_client_availability,
    create_appointment,
    get_active_services,
    get_service_for_payload,
    normalize_phone,
    parse_start,
    appointment_end_for_client,
    require_fields,
    serialize_service,
    ServiceNotFoundError,
    update_appointment,
)


logger = logging.getLogger(__name__)
appointments_bp = Blueprint("appointments", __name__)
INVALID_DATE_FORMAT_MESSAGE = "La fecha debe venir en formato YYYY-MM-DD."
PAST_DATE_MESSAGE = "No se pueden agendar citas en fechas pasadas. Pide una fecha futura."
UNRESOLVED_DATE_MESSAGE = "No pude determinar la fecha. Pide al cliente una fecha exacta."


def cors_preflight_response():
    response = jsonify({"ok": True})
    response.status_code = 204
    return response


def error_response(message: str, status_code: int = 400, **extra):
    payload = {"success": False, "available": False, "message": message}
    payload.update(extra)
    return jsonify(payload), status_code


def resolve_date_error_response(message: str = UNRESOLVED_DATE_MESSAGE, status_code: int = 400):
    return jsonify({"success": False, "date": None, "message": message}), status_code


def assistant_id_error_response():
    return jsonify({"error": "assistant_id requerido"}), 400


def get_json_payload():
    if not request.is_json:
        return None, "La petición debe usar Content-Type: application/json."

    payload = request.get_json(silent=True)
    if payload is None:
        return None, "El JSON recibido no es válido."

    return normalize_vapi_payload(payload), None


def get_query_payload() -> dict:
    return normalize_vapi_payload(dict(request.args))


def validate_requested_date(
    payload: dict,
    endpoint: str,
    assistant_id: str,
    today: date | None = None,
) -> tuple[date | None, tuple | None]:
    raw_fecha = str(payload.get("fecha", "")).strip()
    if not DATE_FORMAT_RE.fullmatch(raw_fecha):
        logger.warning(
            "invalid_date_format endpoint=%s assistant_id=%s fecha=%s",
            endpoint,
            assistant_id,
            raw_fecha or "missing",
        )
        return None, error_response(INVALID_DATE_FORMAT_MESSAGE, 400)

    try:
        requested_date = date.fromisoformat(raw_fecha)
    except ValueError:
        logger.warning(
            "invalid_date_format endpoint=%s assistant_id=%s fecha=%s",
            endpoint,
            assistant_id,
            raw_fecha or "missing",
        )
        return None, error_response(INVALID_DATE_FORMAT_MESSAGE, 400)

    current_date = today or date.today()
    if requested_date < current_date:
        logger.warning(
            "past_date_rejected endpoint=%s assistant_id=%s fecha=%s today=%s",
            endpoint,
            assistant_id,
            requested_date.isoformat(),
            current_date.isoformat(),
        )
        return None, error_response(PAST_DATE_MESSAGE, 400)

    return requested_date, None


def date_resolution_response(resolution, status_code: int = 400):
    return jsonify(
        {
            "success": False,
            "date": resolution.date.isoformat() if resolution.date else None,
            "display_date": resolution.display_date,
            "needs_clarification": resolution.needs_clarification,
            "resolved_month": resolution.resolved_month,
            "resolved_year": resolution.resolved_year,
            "timezone": resolution.timezone,
            "interpreted_from": resolution.interpreted_from,
            "resolution_rule": resolution.resolution_rule,
            "message": resolution.message or UNRESOLVED_DATE_MESSAGE,
        }
    ), status_code


def first_text(payload: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def apply_date_text(
    payload: dict,
    cliente,
    endpoint: str,
    assistant_id: str,
    target_field: str,
    text_fields: tuple[str, ...],
    allow_month_lookup: bool = False,
) -> tuple | None:
    if payload.get(target_field):
        return None

    date_text = first_text(payload, text_fields)
    if not date_text:
        return None

    today = client_today(cliente.timezone)
    resolution = resolve_date_context(date_text, today, cliente.timezone)
    logger.info(
        "date_text_resolution endpoint=%s assistant_id=%s timezone=%s current_local_date=%s date_text=%s "
        "resolved_date=%s resolved_month=%s resolved_year=%s resolution_rule=%s needs_clarification=%s target_field=%s",
        endpoint,
        assistant_id,
        cliente.timezone,
        today.isoformat(),
        date_text,
        resolution.date.isoformat() if resolution.date else None,
        resolution.resolved_month,
        resolution.resolved_year,
        resolution.resolution_rule,
        resolution.needs_clarification,
        target_field,
    )
    if resolution.success and resolution.date:
        payload[target_field] = resolution.date.isoformat()
        return None

    if allow_month_lookup and resolution.needs_clarification and resolution.resolved_month and resolution.resolved_year:
        payload["lookup_month"] = resolution.resolved_month
        payload["lookup_year"] = resolution.resolved_year
        payload["lookup_date_text"] = date_text
        return None

    return date_resolution_response(resolution, 200 if resolution.needs_clarification else 400)


def wrong_create_tool_response(payload: dict) -> dict | None:
    text = request_context_text(payload)
    if not text:
        return None

    normalized = " ".join(text.lower().split())
    cancel_words = (
        "cancelar", "cancela", "cancel", "eliminar", "elimina", "borrar",
        "borra", "ya no voy", "no asistir", "no voy a asistir",
    )
    update_words = (
        "reagendar", "reagenda", "cambiar", "cambia", "mover", "mueve",
        "modificar", "modifica", "actualizar", "actualiza",
    )

    if any(word in normalized for word in cancel_words):
        return {
            "success": False,
            "wrong_tool": True,
            "expected_tool": "cancel_appointment",
            "message": "Esta solicitud parece ser para cancelar una cita existente. Usa la herramienta cancel_appointment.",
        }

    if any(word in normalized for word in update_words):
        return {
            "success": False,
            "wrong_tool": True,
            "expected_tool": "update_appointment",
            "message": "Esta solicitud parece ser para cambiar una cita existente. Usa la herramienta update_appointment.",
        }

    return None


def request_context_text(payload: dict) -> str:
    fields = ("intent", "action", "mensaje", "message", "user_message", "transcript", "context")
    parts = []
    for field in fields:
        value = payload.get(field)
        if value is not None:
            parts.append(stringify_context_value(value))
    return " ".join(part for part in parts if part)


def stringify_context_value(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(stringify_context_value(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(stringify_context_value(item) for item in value)
    return str(value)


@appointments_bp.get("/")
def healthcheck():
    return jsonify(
        {
            "ok": True,
            "service": "vapi-flask-calendar",
            "endpoints": [
                "/check-availability",
                "/create-appointment",
                "/update-appointment",
                "/cancel-appointment",
                "/resolve-date",
                "/get-services",
                "/client-prompt",
            ],
        }
    )


@appointments_bp.route("/check-availability", methods=["POST", "OPTIONS"])
def check_availability():
    if request.method == "OPTIONS":
        return cors_preflight_response()

    payload, error = get_json_payload()
    if error:
        logger.warning("validation_error endpoint=/check-availability error=%s", error)
        return error_response(error, 415 if "Content-Type" in error else 400)

    assistant_id = extract_assistant_id(payload)
    logger.info(
        "request endpoint=/check-availability assistant_id=%s fecha=%s hora=%s",
        assistant_id or "missing",
        payload.get("fecha"),
        payload.get("hora"),
    )
    if not assistant_id:
        logger.warning("validation_error endpoint=/check-availability assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()

    missing_error = require_fields(payload, ["hora"])
    if missing_error:
        logger.warning(
            "validation_error endpoint=/check-availability assistant_id=%s error=%s",
            assistant_id or "missing",
            missing_error,
        )
        return error_response(missing_error, 400)

    try:
        with session_scope() as session:
            cliente = get_client_for_payload(session, payload)
            date_text_error = apply_date_text(
                payload,
                cliente,
                "/check-availability",
                assistant_id,
                "fecha",
                ("fecha_text", "date_text", "dateText"),
            )
            if date_text_error:
                return date_text_error
            missing_error = require_fields(payload, ["fecha", "hora"])
            if missing_error:
                logger.warning(
                    "validation_error endpoint=/check-availability assistant_id=%s error=%s",
                    assistant_id,
                    missing_error,
                )
                return error_response(missing_error, 400)
            _, date_error = validate_requested_date(payload, "/check-availability", assistant_id, client_today(cliente.timezone))
            if date_error:
                return date_error
            logger.info(
                "client_identified endpoint=/check-availability assistant_id=%s cliente_id=%s cliente=%s",
                assistant_id,
                cliente.id,
                cliente.nombre,
            )
            servicio = get_service_for_payload(session, cliente, payload)
            start = parse_start(payload["fecha"], payload["hora"], cliente.timezone)
            end = appointment_end_for_client(start, cliente, servicio)
            available = check_client_availability(
                cliente,
                start,
                end,
                servicio=servicio,
                canal=payload.get("canal"),
            )
            client_name = cliente.nombre
            logger.info(
                "availability_result endpoint=/check-availability assistant_id=%s cliente_id=%s fecha=%s hora=%s available=%s",
                assistant_id,
                cliente.id,
                payload["fecha"],
                payload["hora"],
                available,
            )
    except MissingAssistantIdError:
        logger.warning("validation_error endpoint=/check-availability assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()
    except ClientLookupError as exc:
        logger.warning("client_lookup_error endpoint=/check-availability assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 404)
    except AvailabilityError as exc:
        logger.info(
            "availability_result endpoint=/check-availability assistant_id=%s fecha=%s hora=%s available=false reason=business_hours",
            assistant_id,
            payload.get("fecha"),
            payload.get("hora"),
        )
        return error_response(str(exc), exc.status_code)
    except ServiceNotFoundError as exc:
        logger.warning("validation_error endpoint=/check-availability assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), exc.status_code)
    except ValueError as exc:
        logger.warning("validation_error endpoint=/check-availability assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 400)
    except CalendarServiceError as exc:
        logger.exception("google_calendar_error endpoint=/check-availability assistant_id=%s", assistant_id)
        return error_response(f"No pude consultar Google Calendar: {exc}", 502)

    if available:
        return jsonify(
            {
                "available": True,
                "message": f"Sí hay disponibilidad el {payload['fecha']} a las {payload['hora']}.",
                "client": client_name,
            }
        )

    return jsonify(
        {
            "available": False,
            "message": f"No hay disponibilidad el {payload['fecha']} a las {payload['hora']}.",
            "client": client_name,
        }
    )


@appointments_bp.route("/create-appointment", methods=["POST", "OPTIONS"])
def create_appointment_route():
    if request.method == "OPTIONS":
        return cors_preflight_response()

    payload, error = get_json_payload()
    if error:
        logger.warning("validation_error endpoint=/create-appointment error=%s", error)
        return error_response(error, 415 if "Content-Type" in error else 400)

    assistant_id = extract_assistant_id(payload)
    logger.info(
        "request endpoint=/create-appointment assistant_id=%s fecha=%s hora=%s",
        assistant_id or "missing",
        payload.get("fecha"),
        payload.get("hora"),
    )
    if not assistant_id:
        logger.warning("validation_error endpoint=/create-appointment assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()

    wrong_tool_response = wrong_create_tool_response(payload)
    if wrong_tool_response:
        logger.warning(
            "wrong_tool_block endpoint=/create-appointment assistant_id=%s expected_tool=%s context=%s",
            assistant_id,
            wrong_tool_response["expected_tool"],
            request_context_text(payload),
        )
        return jsonify(wrong_tool_response), 200

    missing_error = require_fields(payload, ["nombre", "telefono", "hora"])
    if missing_error:
        logger.warning(
            "validation_error endpoint=/create-appointment assistant_id=%s error=%s",
            assistant_id or "missing",
            missing_error,
        )
        return error_response(missing_error, 400)

    try:
        with session_scope() as session:
            cliente = get_client_for_payload(session, payload)
            date_text_error = apply_date_text(
                payload,
                cliente,
                "/create-appointment",
                assistant_id,
                "fecha",
                ("fecha_text", "date_text", "dateText"),
            )
            if date_text_error:
                return date_text_error
            missing_error = require_fields(payload, ["nombre", "telefono", "fecha", "hora"])
            if missing_error:
                logger.warning(
                    "validation_error endpoint=/create-appointment assistant_id=%s error=%s",
                    assistant_id,
                    missing_error,
                )
                return error_response(missing_error, 400)
            _, date_error = validate_requested_date(payload, "/create-appointment", assistant_id, client_today(cliente.timezone))
            if date_error:
                return date_error
            logger.info(
                "client_identified endpoint=/create-appointment assistant_id=%s cliente_id=%s cliente=%s",
                assistant_id,
                cliente.id,
                cliente.nombre,
            )
            result = create_appointment(session, cliente, payload)
            event = result["event"]
            logger.info(
                "appointment_created endpoint=/create-appointment assistant_id=%s cliente_id=%s fecha=%s hora=%s google_event_created=%s",
                assistant_id,
                cliente.id,
                payload["fecha"],
                payload["hora"],
                bool(event.get("id")),
            )
    except MissingAssistantIdError:
        logger.warning("validation_error endpoint=/create-appointment assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()
    except ClientLookupError as exc:
        logger.warning("client_lookup_error endpoint=/create-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 404, calendar_link="")
    except AvailabilityError as exc:
        logger.info(
            "availability_result endpoint=/create-appointment assistant_id=%s fecha=%s hora=%s available=false",
            assistant_id,
            payload.get("fecha"),
            payload.get("hora"),
        )
        return error_response(str(exc), exc.status_code, calendar_link="")
    except ServiceNotFoundError as exc:
        logger.warning("validation_error endpoint=/create-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), exc.status_code, calendar_link="")
    except ValueError as exc:
        logger.warning("validation_error endpoint=/create-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 400, calendar_link="")
    except CalendarServiceError as exc:
        logger.exception("google_calendar_error endpoint=/create-appointment assistant_id=%s", assistant_id)
        return error_response(f"No pude crear la cita en Google Calendar: {exc}", 502, calendar_link="")

    return jsonify(
        {
            "success": True,
            "message": f"Cita agendada para {payload['nombre']} el {payload['fecha']} a las {payload['hora']}.",
            "calendar_link": event.get("htmlLink", ""),
        }
    )


@appointments_bp.route("/update-appointment", methods=["POST", "OPTIONS"])
def update_appointment_route():
    if request.method == "OPTIONS":
        return cors_preflight_response()

    payload, error = get_json_payload()
    if error:
        logger.warning("validation_error endpoint=/update-appointment error=%s", error)
        return error_response(error, 415 if "Content-Type" in error else 400)

    assistant_id = extract_assistant_id(payload)
    logger.info(
        "request endpoint=/update-appointment assistant_id=%s event_id=%s fecha_actual=%s hora_actual=%s fecha=%s hora=%s",
        assistant_id or "missing",
        payload.get("event_id") or payload.get("google_event_id"),
        payload.get("fecha_actual"),
        payload.get("hora_actual"),
        payload.get("fecha"),
        payload.get("hora"),
    )
    if not assistant_id:
        logger.warning("validation_error endpoint=/update-appointment assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()

    missing_error = require_fields(payload, ["telefono", "hora"])
    if missing_error:
        logger.warning("validation_error endpoint=/update-appointment assistant_id=%s error=%s", assistant_id, missing_error)
        return error_response(missing_error, 400)

    try:
        with session_scope() as session:
            cliente = get_client_for_payload(session, payload)
            new_date_error = apply_date_text(
                payload,
                cliente,
                "/update-appointment",
                assistant_id,
                "fecha",
                ("fecha_nueva_text", "nueva_fecha_text", "new_date_text"),
            )
            if new_date_error:
                return new_date_error
            lookup_date_error = apply_date_text(
                payload,
                cliente,
                "/update-appointment",
                assistant_id,
                "fecha_actual",
                ("fecha_actual_text", "date_text_actual", "original_date_text"),
                allow_month_lookup=True,
            )
            if lookup_date_error:
                return lookup_date_error
            missing_error = require_fields(payload, ["telefono", "fecha", "hora"])
            if missing_error:
                logger.warning("validation_error endpoint=/update-appointment assistant_id=%s error=%s", assistant_id, missing_error)
                return error_response(missing_error, 400)
            _, date_error = validate_requested_date(payload, "/update-appointment", assistant_id, client_today(cliente.timezone))
            if date_error:
                return date_error
            logger.info(
                "appointment_lookup endpoint=/update-appointment assistant_id=%s telefono_normalizado=%s event_id=%s "
                "fecha_actual=%s hora_actual=%s fecha=%s hora=%s lookup_month=%s lookup_year=%s",
                assistant_id,
                normalize_phone(payload.get("telefono")),
                payload.get("event_id") or payload.get("google_event_id"),
                payload.get("fecha_actual"),
                payload.get("hora_actual"),
                payload.get("fecha"),
                payload.get("hora"),
                payload.get("lookup_month"),
                payload.get("lookup_year"),
            )
            result = update_appointment(session, cliente, payload)
            cita = result["cita"]
            event = result.get("event") or {}
            logger.info(
                "appointment_updated endpoint=/update-appointment assistant_id=%s cliente_id=%s cita_id=%s event_id=%s fecha=%s hora=%s",
                assistant_id,
                cliente.id,
                cita.id,
                cita.google_event_id,
                payload["fecha"],
                payload["hora"],
            )
    except MissingAssistantIdError:
        logger.warning("validation_error endpoint=/update-appointment assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()
    except ClientLookupError as exc:
        logger.warning("client_lookup_error endpoint=/update-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 404, calendar_link="")
    except AppointmentClarificationError as exc:
        logger.info(
            "appointment_clarification endpoint=/update-appointment assistant_id=%s telefono_normalizado=%s event_id=%s "
            "fecha_actual=%s hora_actual=%s fecha=%s hora=%s count=%s",
            assistant_id,
            normalize_phone(payload.get("telefono")),
            payload.get("event_id") or payload.get("google_event_id"),
            payload.get("fecha_actual"),
            payload.get("hora_actual"),
            payload.get("fecha"),
            payload.get("hora"),
            len(exc.appointments),
        )
        return error_response(str(exc), exc.status_code, needs_clarification=True, appointments=exc.appointments)
    except AvailabilityError as exc:
        logger.info("appointment_update_rejected endpoint=/update-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), exc.status_code, calendar_link="")
    except ServiceNotFoundError as exc:
        logger.warning("validation_error endpoint=/update-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), exc.status_code, calendar_link="")
    except ValueError as exc:
        logger.warning("validation_error endpoint=/update-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 400, calendar_link="")
    except CalendarServiceError as exc:
        logger.exception("google_calendar_error endpoint=/update-appointment assistant_id=%s", assistant_id)
        return error_response(f"No pude actualizar la cita en Google Calendar: {exc}", 502, calendar_link="")

    return jsonify(
        {
            "success": True,
            "message": f"Listo, tu cita fue reagendada para {payload['fecha']} a las {payload['hora']}.",
            "calendar_link": event.get("htmlLink", ""),
        }
    )


@appointments_bp.route("/cancel-appointment", methods=["POST", "OPTIONS"])
def cancel_appointment_route():
    if request.method == "OPTIONS":
        return cors_preflight_response()

    payload, error = get_json_payload()
    if error:
        logger.warning("validation_error endpoint=/cancel-appointment error=%s", error)
        return error_response(error, 415 if "Content-Type" in error else 400)

    assistant_id = extract_assistant_id(payload)
    logger.info(
        "request endpoint=/cancel-appointment assistant_id=%s event_id=%s fecha=%s hora=%s",
        assistant_id or "missing",
        payload.get("event_id") or payload.get("google_event_id"),
        payload.get("fecha"),
        payload.get("hora"),
    )
    if not assistant_id:
        logger.warning("validation_error endpoint=/cancel-appointment assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()

    missing_error = require_fields(payload, ["telefono"])
    if missing_error:
        logger.warning("validation_error endpoint=/cancel-appointment assistant_id=%s error=%s", assistant_id, missing_error)
        return error_response(missing_error, 400)

    try:
        with session_scope() as session:
            cliente = get_client_for_payload(session, payload)
            lookup_date_error = apply_date_text(
                payload,
                cliente,
                "/cancel-appointment",
                assistant_id,
                "fecha",
                ("fecha_text", "date_text", "dateText", "fecha_actual_text"),
                allow_month_lookup=True,
            )
            if lookup_date_error:
                return lookup_date_error
            if payload.get("fecha"):
                _, date_error = validate_requested_date(payload, "/cancel-appointment", assistant_id, client_today(cliente.timezone))
                if date_error:
                    return date_error
            logger.info(
                "appointment_lookup endpoint=/cancel-appointment assistant_id=%s telefono_normalizado=%s event_id=%s "
                "fecha=%s hora=%s lookup_month=%s lookup_year=%s",
                assistant_id,
                normalize_phone(payload.get("telefono")),
                payload.get("event_id") or payload.get("google_event_id"),
                payload.get("fecha"),
                payload.get("hora"),
                payload.get("lookup_month"),
                payload.get("lookup_year"),
            )
            result = cancel_appointment(session, cliente, payload)
            cita = result["cita"]
            logger.info(
                "appointment_cancelled endpoint=/cancel-appointment assistant_id=%s cliente_id=%s cita_id=%s event_id=%s",
                assistant_id,
                cliente.id,
                cita.id,
                cita.google_event_id,
            )
    except MissingAssistantIdError:
        logger.warning("validation_error endpoint=/cancel-appointment assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()
    except ClientLookupError as exc:
        logger.warning("client_lookup_error endpoint=/cancel-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 404)
    except AppointmentClarificationError as exc:
        logger.info(
            "appointment_clarification endpoint=/cancel-appointment assistant_id=%s telefono_normalizado=%s event_id=%s "
            "fecha=%s hora=%s count=%s",
            assistant_id,
            normalize_phone(payload.get("telefono")),
            payload.get("event_id") or payload.get("google_event_id"),
            payload.get("fecha"),
            payload.get("hora"),
            len(exc.appointments),
        )
        return error_response(str(exc), exc.status_code, needs_clarification=True, appointments=exc.appointments)
    except AvailabilityError as exc:
        logger.info("appointment_cancel_rejected endpoint=/cancel-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), exc.status_code)
    except ValueError as exc:
        logger.warning("validation_error endpoint=/cancel-appointment assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 400)
    except CalendarServiceError as exc:
        logger.exception("google_calendar_error endpoint=/cancel-appointment assistant_id=%s", assistant_id)
        return error_response(f"No pude cancelar la cita en Google Calendar: {exc}", 502)

    return jsonify({"success": True, "message": "Listo, tu cita fue cancelada."})


@appointments_bp.route("/resolve-date", methods=["POST", "OPTIONS"])
def resolve_date_route():
    if request.method == "OPTIONS":
        return cors_preflight_response()

    payload, error = get_json_payload()
    if error:
        logger.warning("validation_error endpoint=/resolve-date error=%s", error)
        return error_response(error, 415 if "Content-Type" in error else 400)

    assistant_id = extract_assistant_id(payload)
    date_text = str(payload.get("date_text") or payload.get("dateText") or "").strip()
    logger.info(
        "resolve_date_request endpoint=/resolve-date assistant_id=%s date_text=%s",
        assistant_id or "missing",
        date_text or "missing",
    )
    if not assistant_id:
        logger.warning("validation_error endpoint=/resolve-date assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()

    missing_error = require_fields({"date_text": date_text}, ["date_text"])
    if missing_error:
        logger.warning("validation_error endpoint=/resolve-date assistant_id=%s error=%s", assistant_id, missing_error)
        return resolve_date_error_response()

    try:
        with session_scope() as session:
            cliente = get_client_for_payload(session, payload)
            today = client_today(cliente.timezone)
            resolution = resolve_date_context(date_text, today, cliente.timezone)

            logger.info(
                "resolve_date_result endpoint=/resolve-date assistant_id=%s cliente_id=%s timezone=%s current_local_date=%s "
                "date_text=%s resolved_date=%s resolved_month=%s resolved_year=%s resolution_rule=%s needs_clarification=%s",
                assistant_id,
                cliente.id,
                cliente.timezone,
                today.isoformat(),
                date_text,
                resolution.date.isoformat() if resolution.date else None,
                resolution.resolved_month,
                resolution.resolved_year,
                resolution.resolution_rule,
                resolution.needs_clarification,
            )
            if not resolution.success:
                return date_resolution_response(resolution, 200 if resolution.needs_clarification else 400)
    except MissingAssistantIdError:
        logger.warning("validation_error endpoint=/resolve-date assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()
    except ClientLookupError as exc:
        logger.warning("client_lookup_error endpoint=/resolve-date assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 404)

    return jsonify(
        {
            "success": True,
            "date": resolution.date.isoformat(),
            "display_date": resolution.display_date,
            "timezone": resolution.timezone,
            "interpreted_from": resolution.interpreted_from,
            "resolved_month": resolution.resolved_month,
            "resolved_year": resolution.resolved_year,
            "resolution_rule": resolution.resolution_rule,
            "message": resolution.message,
        }
    )


@appointments_bp.get("/get-services")
def get_services_route():
    payload = get_query_payload()
    assistant_id = extract_assistant_id(payload)
    logger.info("request endpoint=/get-services assistant_id=%s", assistant_id or "missing")

    try:
        with session_scope() as session:
            cliente = get_client_for_payload(session, payload)
            servicios = get_active_services(session, cliente, payload.get("canal"))
            logger.info(
                "services_listed endpoint=/get-services assistant_id=%s cliente_id=%s count=%s",
                assistant_id,
                cliente.id,
                len(servicios),
            )
            return jsonify([serialize_service(servicio) for servicio in servicios])
    except MissingAssistantIdError:
        logger.warning("validation_error endpoint=/get-services assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()
    except ClientLookupError as exc:
        logger.warning("client_lookup_error endpoint=/get-services assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 404)


@appointments_bp.get("/client-prompt")
def client_prompt_route():
    payload = get_query_payload()
    assistant_id = extract_assistant_id(payload)
    logger.info("request endpoint=/client-prompt assistant_id=%s", assistant_id or "missing")

    try:
        with session_scope() as session:
            cliente = get_client_for_payload(session, payload)
            servicios = get_active_services(session, cliente)
            prompt = generate_client_prompt(cliente, servicios)
            logger.info(
                "prompt_generated endpoint=/client-prompt assistant_id=%s cliente_id=%s services_count=%s",
                assistant_id,
                cliente.id,
                len(servicios),
            )
            return jsonify({"assistant_id": cliente.assistant_id, "cliente": cliente.nombre, "prompt": prompt})
    except MissingAssistantIdError:
        logger.warning("validation_error endpoint=/client-prompt assistant_id=missing error=assistant_id requerido")
        return assistant_id_error_response()
    except ClientLookupError as exc:
        logger.warning("client_lookup_error endpoint=/client-prompt assistant_id=%s error=%s", assistant_id, exc)
        return error_response(str(exc), 404)
