import logging

from assistant_engine import generar_respuesta_asistente


logger = logging.getLogger(__name__)


def procesar_mensaje_cliente(telefono: str, mensaje: str, canal: str, assistant_id: str | None = None) -> str:
    logger.info(
        "client_message_received canal=%s assistant_id=%s telefono=%s message_length=%s",
        canal,
        assistant_id or "default",
        _mask_phone(telefono),
        len(mensaje),
    )
    respuesta = generar_respuesta_asistente(telefono=telefono, mensaje=mensaje, canal=canal, assistant_id=assistant_id)
    logger.info(
        "client_message_processed canal=%s assistant_id=%s telefono=%s response_length=%s",
        canal,
        assistant_id or "default",
        _mask_phone(telefono),
        len(respuesta),
    )
    return respuesta


def _mask_phone(telefono: str) -> str:
    if len(telefono) <= 4:
        return "****"
    return f"***{telefono[-4:]}"
