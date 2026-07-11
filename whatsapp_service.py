import logging
import os
from typing import Any

import requests

from config import Config


logger = logging.getLogger(__name__)


class WhatsAppServiceError(Exception):
    pass


class WhatsAppClient:
    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token_env_var: str | None = None,
        access_token: str | None = None,
        api_version: str | None = None,
    ) -> None:
        self.phone_number_id = str(phone_number_id or "").strip()
        self.access_token_env_var = str(access_token_env_var or "").strip()
        self.access_token = str(access_token or "").strip()
        self.api_version = api_version or Config.WHATSAPP_API_VERSION

    @classmethod
    def from_account(cls, account: Any) -> "WhatsAppClient":
        return cls(
            phone_number_id=account.phone_number_id,
            access_token_env_var=account.access_token_env_var,
            access_token=account.access_token,
        )

    def send_text(self, telefono: str, mensaje: str) -> dict[str, Any]:
        token = self._resolve_access_token()
        if not self.phone_number_id or not token:
            raise WhatsAppServiceError("WhatsApp no configurado para este cliente.")

        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "text",
            "text": {"body": mensaje},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception(
                "whatsapp_send_error phone_number_id=%s telefono=%s",
                self.phone_number_id or "missing",
                _mask_phone(telefono),
            )
            raise WhatsAppServiceError("No se pudo enviar el mensaje de WhatsApp.") from exc

        return response.json()

    def _resolve_access_token(self) -> str:
        if self.access_token_env_var:
            return os.getenv(self.access_token_env_var, "").strip()
        return self.access_token


def send_whatsapp_message(telefono: str, mensaje: str) -> dict[str, Any]:
    return WhatsAppClient(
        phone_number_id=Config.WHATSAPP_PHONE_NUMBER_ID,
        access_token=Config.WHATSAPP_TOKEN,
    ).send_text(telefono, mensaje)


def _mask_phone(telefono: str) -> str:
    if len(telefono) <= 4:
        return "****"
    return f"***{telefono[-4:]}"
