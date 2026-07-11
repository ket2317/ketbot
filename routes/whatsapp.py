import logging
from typing import Any

from flask import Blueprint, Response, jsonify, request

from activity_service import record_activity
from clients import (
    ClientLookupError,
    WhatsAppAccountLookupError,
    get_active_whatsapp_account_by_phone_number_id,
    get_active_whatsapp_account_by_verify_token,
)
from database import session_scope
from message_processor import procesar_mensaje_cliente
from whatsapp_service import WhatsAppClient, WhatsAppServiceError


logger = logging.getLogger(__name__)
whatsapp_bp = Blueprint("whatsapp", __name__)


@whatsapp_bp.get("/whatsapp")
def verify_whatsapp_webhook():
    mode = request.args.get("hub.mode")
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge", "")

    if mode == "subscribe" and verify_token:
        try:
            with session_scope() as session:
                account = get_active_whatsapp_account_by_verify_token(session, verify_token)
                logger.info(
                    "whatsapp_webhook_verified client_id=%s phone_number_id=%s",
                    account.cliente_id,
                    account.phone_number_id,
                )
                return Response(challenge, status=200, mimetype="text/plain")
        except WhatsAppAccountLookupError:
            logger.warning("whatsapp_verify_error reason=unknown_verify_token mode=%s", mode or "missing")
            return Response("Forbidden", status=403, mimetype="text/plain")

    logger.warning("whatsapp_verify_error reason=invalid_token mode=%s", mode or "missing")
    return Response("Forbidden", status=403, mimetype="text/plain")


@whatsapp_bp.post("/whatsapp")
def receive_whatsapp_message():
    payload = request.get_json(silent=True) or {}
    messages = _extract_messages(payload)

    if not messages:
        logger.info("whatsapp_webhook_received messages=0")
        return jsonify({"success": True, "processed": 0})

    processed = 0
    ignored = 0
    for message in messages:
        telefono = message["telefono"]
        texto = message["mensaje"]
        phone_number_id = message.get("phone_number_id", "")

        try:
            with session_scope() as session:
                account = get_active_whatsapp_account_by_phone_number_id(session, phone_number_id)
                assistant_id = account.cliente.assistant_id
                client_id = account.cliente_id
                whatsapp_client = WhatsAppClient.from_account(account)
        except WhatsAppAccountLookupError:
            logger.warning(
                "whatsapp_message_ignored reason=unknown_phone_number_id phone_number_id=%s telefono=%s",
                phone_number_id or "missing",
                _mask_phone(telefono),
            )
            ignored += 1
            continue

        logger.info(
            "whatsapp_message_received client_id=%s phone_number_id=%s assistant_id=%s telefono=%s message_length=%s",
            client_id,
            phone_number_id or "missing",
            assistant_id,
            _mask_phone(telefono),
            len(texto),
        )
        respuesta = procesar_mensaje_cliente(
            telefono=telefono,
            mensaje=texto,
            canal="whatsapp",
            assistant_id=assistant_id,
        )
        try:
            with session_scope() as session:
                account = get_active_whatsapp_account_by_phone_number_id(session, phone_number_id)
                cliente = account.cliente
                record_activity(
                    session,
                    cliente,
                    channel="whatsapp",
                    outcome=_outcome_from_response(respuesta),
                    event_type="information_provided",
                    external_id=message.get("external_id"),
                    customer_phone=telefono,
                    status="completed",
                    summary=respuesta[:500],
                    transcript=texto[:4000],
                    metadata={"phone_number_id": phone_number_id},
                )
        except ClientLookupError:
            logger.warning("whatsapp_activity_client_not_found phone_number_id=%s assistant_id=%s", phone_number_id, assistant_id)
        whatsapp_client.send_text(telefono, respuesta)
        processed += 1

    return jsonify({"success": True, "processed": processed, "ignored": ignored})


def _extract_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    extracted = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = value.get("metadata", {})
            phone_number_id = str(metadata.get("phone_number_id", "")).strip() if isinstance(metadata, dict) else ""
            for message in value.get("messages", []):
                telefono = str(message.get("from", "")).strip()
                text = message.get("text", {})
                mensaje = str(text.get("body", "")).strip() if isinstance(text, dict) else ""
                if telefono and mensaje:
                    extracted.append(
                        {
                            "telefono": telefono,
                            "mensaje": mensaje,
                            "phone_number_id": phone_number_id,
                            "external_id": str(message.get("id", "")).strip(),
                        }
                    )
    return extracted


def _mask_phone(telefono: str) -> str:
    if len(telefono) <= 4:
        return "****"
    return f"***{telefono[-4:]}"


def _outcome_from_response(response: str) -> str:
    normalized = response.lower()
    if "agend" in normalized:
        return "appointment_created"
    if "cancel" in normalized:
        return "appointment_cancelled"
    if "moví" in normalized or "movi" in normalized or "reagend" in normalized:
        return "appointment_rescheduled"
    if "disponibilidad" in normalized or "disponible" in normalized:
        return "availability_checked"
    return "information_provided"


@whatsapp_bp.errorhandler(WhatsAppServiceError)
def handle_whatsapp_service_error(exc: WhatsAppServiceError):
    logger.warning("whatsapp_service_error error=%s", exc)
    return jsonify({"success": False, "error": str(exc)}), 502
