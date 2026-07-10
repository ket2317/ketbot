import logging
from typing import Any

from flask import Blueprint, Response, jsonify, request

from config import Config
from message_processor import procesar_mensaje_cliente
from whatsapp_service import WhatsAppServiceError, send_whatsapp_message


logger = logging.getLogger(__name__)
whatsapp_bp = Blueprint("whatsapp", __name__)


@whatsapp_bp.get("/whatsapp")
def verify_whatsapp_webhook():
    mode = request.args.get("hub.mode")
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge", "")

    if not Config.WHATSAPP_VERIFY_TOKEN:
        logger.error("whatsapp_verify_error reason=missing_verify_token")
        return Response("WhatsApp verify token no configurado.", status=500, mimetype="text/plain")

    if mode == "subscribe" and verify_token == Config.WHATSAPP_VERIFY_TOKEN:
        logger.info("whatsapp_webhook_verified")
        return Response(challenge, status=200, mimetype="text/plain")

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
    for message in messages:
        telefono = message["telefono"]
        texto = message["mensaje"]
        phone_number_id = message.get("phone_number_id", "")
        assistant_id = _assistant_id_for_phone_number(phone_number_id)
        logger.info(
            "whatsapp_message_received phone_number_id=%s assistant_id=%s telefono=%s message_length=%s",
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
        send_whatsapp_message(telefono, respuesta)
        processed += 1

    return jsonify({"success": True, "processed": processed})


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
                        }
                    )
    return extracted


def _assistant_id_for_phone_number(phone_number_id: str) -> str:
    assistant_id = Config.WHATSAPP_PHONE_ASSISTANT_MAP.get(phone_number_id)
    if assistant_id:
        return assistant_id

    logger.info(
        "whatsapp_phone_number_unmapped phone_number_id=%s fallback_assistant_id=%s",
        phone_number_id or "missing",
        Config.WHATSAPP_DEFAULT_ASSISTANT_ID,
    )
    return Config.WHATSAPP_DEFAULT_ASSISTANT_ID


def _mask_phone(telefono: str) -> str:
    if len(telefono) <= 4:
        return "****"
    return f"***{telefono[-4:]}"


@whatsapp_bp.errorhandler(WhatsAppServiceError)
def handle_whatsapp_service_error(exc: WhatsAppServiceError):
    logger.warning("whatsapp_service_error error=%s", exc)
    return jsonify({"success": False, "error": str(exc)}), 502
