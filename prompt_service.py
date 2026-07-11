from pathlib import Path

from config import BASE_DIR
from models import Cliente, Servicio


PROMPT_TEMPLATE_FILE = BASE_DIR / "prompt_template.txt"


def generate_client_prompt(cliente: Cliente, servicios: list[Servicio]) -> str:
    template = PROMPT_TEMPLATE_FILE.read_text(encoding="utf-8")
    services_text = _format_services(servicios)
    return template.format(
        nombre=cliente.nombre,
        horario_inicio=cliente.horario_inicio.strftime("%H:%M"),
        horario_fin=cliente.horario_fin.strftime("%H:%M"),
        telefono=cliente.telefono or "No especificado",
        direccion=cliente.direccion or "No especificada",
        email=cliente.email or "No especificado",
        descripcion=cliente.descripcion or "No especificada",
        mensaje_bienvenida=cliente.mensaje_bienvenida or "",
        informacion_general=cliente.informacion_general or "",
        timezone=cliente.timezone,
        servicios=services_text,
        prompt_extra=cliente.instrucciones_asistente or cliente.prompt or "",
    ).strip()


def _format_services(servicios: list[Servicio]) -> str:
    if not servicios:
        return "- Aun no hay servicios configurados. Si el usuario pregunta, indica que el negocio confirmara el servicio."

    lines = []
    for servicio in servicios:
        price = f"${servicio.precio:g}" if servicio.precio is not None else "precio por confirmar"
        description = f". {servicio.descripcion}" if servicio.descripcion else ""
        channels = []
        if servicio.disponible_por_llamada:
            channels.append("llamada")
        if servicio.disponible_por_whatsapp:
            channels.append("WhatsApp")
        channel_text = f", disponible por {' y '.join(channels)}" if channels else ", no disponible por canales automatizados"
        lines.append(
            f"- {servicio.nombre}: {price}, duracion {servicio.duracion_minutos} minutos{channel_text}{description}"
        )
    return "\n".join(lines)
