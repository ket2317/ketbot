import logging


logger = logging.getLogger(__name__)


def procesar_mensaje_cliente(telefono: str, mensaje: str, canal: str) -> str:
    logger.info(
        "client_message_received canal=%s telefono=%s message_length=%s",
        canal,
        _mask_phone(telefono),
        len(mensaje),
    )
    return "Recibimos tu mensaje. En breve te ayudaremos."


def _mask_phone(telefono: str) -> str:
    if len(telefono) <= 4:
        return "****"
    return f"***{telefono[-4:]}"
