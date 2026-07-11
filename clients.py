import json
from datetime import time
from typing import Any

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from models import Cliente, WhatsAppAccount
from config import Config


class ClientLookupError(Exception):
    pass


class MissingAssistantIdError(ClientLookupError):
    pass


class WhatsAppAccountLookupError(ClientLookupError):
    pass


def normalize_vapi_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    for key in ("arguments", "args", "parameters", "input"):
        nested = _decode_possible_json_object(payload.get(key))
        if isinstance(nested, dict):
            return {**payload, **nested}

    message = payload.get("message")
    if isinstance(message, dict):
        tool_calls = message.get("toolCalls") or message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
                function_data = tool_calls[0].get("function", {})
                arguments = _decode_possible_json_object(function_data.get("arguments"))
                if isinstance(arguments, dict):
                    return {**payload, **arguments}

    return payload


def extract_assistant_id(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("assistant_id"),
        payload.get("assistantId"),
        payload.get("assistant") if isinstance(payload.get("assistant"), str) else None,
        _nested_id(payload.get("assistant")),
    ]

    message = payload.get("message")
    if isinstance(message, dict):
        candidates.extend(
            [
                message.get("assistantId"),
                _nested_id(message.get("assistant")),
            ]
        )

    call = payload.get("call")
    if isinstance(call, dict):
        candidates.extend([call.get("assistantId"), _nested_id(call.get("assistant"))])

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    return None


def get_client_for_payload(session: Session, payload: dict[str, Any]) -> Cliente:
    assistant_id = extract_assistant_id(payload)
    if assistant_id:
        return get_active_client_by_assistant_id(session, assistant_id)

    raise MissingAssistantIdError("assistant_id requerido")


def get_active_client_by_assistant_id(session: Session, assistant_id: str) -> Cliente:
    client = session.scalar(select(Cliente).where(Cliente.assistant_id == assistant_id, Cliente.activo.is_(True)))
    if client:
        return client
    raise ClientLookupError(f"No existe un cliente activo para assistant_id={assistant_id}.")


def get_active_whatsapp_account_by_phone_number_id(session: Session, phone_number_id: str) -> WhatsAppAccount:
    normalized_phone_number_id = str(phone_number_id or "").strip()
    if not normalized_phone_number_id:
        raise WhatsAppAccountLookupError("phone_number_id requerido")

    account = session.scalar(
        select(WhatsAppAccount)
        .join(WhatsAppAccount.cliente)
        .where(
            WhatsAppAccount.phone_number_id == normalized_phone_number_id,
            WhatsAppAccount.activo.is_(True),
            Cliente.activo.is_(True),
        )
    )
    if account:
        return account
    raise WhatsAppAccountLookupError("No existe una cuenta activa para ese phone_number_id.")


def get_active_whatsapp_account_by_verify_token(session: Session, verify_token: str) -> WhatsAppAccount:
    normalized_verify_token = str(verify_token or "").strip()
    if not normalized_verify_token:
        raise WhatsAppAccountLookupError("verify_token requerido")

    account = session.scalar(
        select(WhatsAppAccount)
        .join(WhatsAppAccount.cliente)
        .where(
            WhatsAppAccount.verify_token == normalized_verify_token,
            WhatsAppAccount.activo.is_(True),
            Cliente.activo.is_(True),
        )
    )
    if account:
        return account
    raise WhatsAppAccountLookupError("No existe una cuenta activa para ese verify_token.")


def seed_initial_clients(session: Session) -> None:
    _upsert_client(
        session,
        assistant_id=Config.RPM_ASSISTANT_ID,
        nombre="RPM Automotive",
        calendar_id=Config.RPM_CALENDAR_ID,
        credentials_file=Config.RPM_CREDENTIALS_FILE,
        credentials_env_var=Config.RPM_CREDENTIALS_ENV_VAR,
        horario_inicio=time(8, 0),
        horario_fin=time(18, 0),
        timezone=Config.RPM_TIMEZONE,
        telefono="",
        direccion="",
        prompt="Asistente de RPM Automotive para agendar citas de servicio automotriz.",
        activo=True,
    )
    _ensure_default_business_hours(session)
    _upsert_client(
        session,
        assistant_id=Config.UNAS_ASSISTANT_ID,
        nombre="Uñas La Comer",
        calendar_id=Config.UNAS_CALENDAR_ID,
        credentials_file=Config.UNAS_CREDENTIALS_FILE,
        credentials_env_var=Config.UNAS_CREDENTIALS_ENV_VAR,
        horario_inicio=time(8, 0),
        horario_fin=time(22, 0),
        timezone=Config.UNAS_TIMEZONE,
        telefono="",
        direccion="",
        prompt="Asistente de Uñas La Comer para agendar citas.",
        activo=True,
    )
    _ensure_default_business_hours(session)
    _seed_legacy_whatsapp_accounts(session)


def _upsert_client(session: Session, **data: Any) -> None:
    client = session.scalar(select(Cliente).where(Cliente.assistant_id == data["assistant_id"]))
    if client is None:
        session.add(Cliente(**data))
        return

    if not client.credentials_env_var and data.get("credentials_env_var"):
        client.credentials_env_var = data["credentials_env_var"]


def _ensure_default_business_hours(session: Session) -> None:
    clients = session.scalars(select(Cliente)).all()
    for client in clients:
        for weekday in range(7):
            session.execute(
                text(
                    "INSERT INTO client_business_hours "
                    "(cliente_id, weekday, is_open, start_time, end_time, breaks_json) "
                    "VALUES (:cliente_id, :weekday, :is_open, :start_time, :end_time, :breaks_json) "
                    "ON CONFLICT (cliente_id, weekday) DO NOTHING"
                ),
                {
                    "cliente_id": client.id,
                    "weekday": weekday,
                    "is_open": weekday < 6,
                    "start_time": client.horario_inicio.strftime("%H:%M:%S"),
                    "end_time": client.horario_fin.strftime("%H:%M:%S"),
                    "breaks_json": "[]",
                },
            )


def _seed_legacy_whatsapp_accounts(session: Session) -> None:
    legacy_pairs = dict(Config.WHATSAPP_PHONE_ASSISTANT_MAP)
    if Config.WHATSAPP_PHONE_NUMBER_ID and Config.WHATSAPP_DEFAULT_ASSISTANT_ID:
        legacy_pairs.setdefault(Config.WHATSAPP_PHONE_NUMBER_ID, Config.WHATSAPP_DEFAULT_ASSISTANT_ID)

    if not legacy_pairs:
        return

    for phone_number_id, assistant_id in legacy_pairs.items():
        phone_number_id = str(phone_number_id or "").strip()
        assistant_id = str(assistant_id or "").strip()
        if not phone_number_id or not assistant_id:
            continue

        client = session.scalar(select(Cliente).where(Cliente.assistant_id == assistant_id))
        if client is None:
            continue

        existing = session.scalar(select(WhatsAppAccount).where(WhatsAppAccount.phone_number_id == phone_number_id))
        if existing is not None:
            continue

        session.add(
            WhatsAppAccount(
                cliente_id=client.id,
                phone_number_id=phone_number_id,
                verify_token=Config.WHATSAPP_VERIFY_TOKEN if len(legacy_pairs) == 1 else None,
                access_token_env_var="WHATSAPP_TOKEN" if Config.WHATSAPP_TOKEN else "",
                activo=True,
            )
        )


def _nested_id(value: Any) -> str | None:
    if isinstance(value, dict):
        nested = value.get("id")
        return nested if isinstance(nested, str) else None
    return None


def _decode_possible_json_object(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded if isinstance(decoded, dict) else value
