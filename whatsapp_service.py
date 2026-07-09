import logging
from typing import Any

import requests

from config import Config


logger = logging.getLogger(__name__)


class WhatsAppServiceError(Exception):
    pass


def send_whatsapp_message(telefono: str, mensaje: str) -> dict[str, Any]:
    if not Config.WHATSAPP_TOKEN or not Config.WHATSAPP_PHONE_NUMBER_ID:
        raise WhatsAppServiceError("WhatsApp no configurado: define WHATSAPP_TOKEN y WHATSAPP_PHONE_NUMBER_ID.")

    url = (
        f"https://graph.facebook.com/{Config.WHATSAPP_API_VERSION}/"
        f"{Config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "text",
        "text": {"body": mensaje},
    }
    headers = {
        "Authorization": f"Bearer {Config.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("whatsapp_send_error telefono=%s", _mask_phone(telefono))
        raise WhatsAppServiceError("No se pudo enviar el mensaje de WhatsApp.") from exc

    return response.json()


def _mask_phone(telefono: str) -> str:
    if len(telefono) <= 4:
        return "****"
    return f"***{telefono[-4:]}"
