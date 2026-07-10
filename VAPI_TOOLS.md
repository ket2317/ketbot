# Vapi Tools - KET Prioridad 1

Base URL para Vapi:

```text
https://TU-DOMINIO-PUBLICO
```

Vapi no puede llamar `localhost` desde la nube. En local usa una URL publica como ngrok y reemplaza `https://TU-DOMINIO-PUBLICO` en todas las tools.

Regla critica: si la intencion del cliente es reagendar o cancelar, Vapi nunca debe llamar `create_appointment`.

Para RPM Automotive usa `assistant_id="rpm-automotive"`. El backend usa la zona horaria del cliente configurado; RPM trabaja en `America/Mexico_City`.

## Tool: resolve_date

Nombre:

```text
resolve_date
```

Descripcion:

```text
Convierte fechas naturales del cliente a YYYY-MM-DD o devuelve el mes/año cuando falta el día exacto. Debe usarse antes de check_availability, create_appointment, update_appointment o cancel_appointment cuando el cliente diga fechas como hoy, mañana, próximo viernes, este mes, mes que viene o finales de julio.
```

URL:

```text
https://TU-DOMINIO-PUBLICO/resolve-date
```

Metodo:

```text
POST
```

Headers:

```json
{
  "Content-Type": "application/json"
}
```

Body:

```json
{
  "assistant_id": "{{assistant_id}}",
  "date_text": "{{date_text}}",
  "canal": "vapi"
}
```

JSON Schema:

```json
{
  "type": "object",
  "required": ["assistant_id", "date_text", "canal"],
  "properties": {
    "assistant_id": { "type": "string", "description": "ID del asistente/negocio. Para RPM Automotive usa rpm-automotive." },
    "date_text": { "type": "string", "description": "Texto exacto de fecha dicho por el cliente, por ejemplo mañana, próximo viernes, este mes, 20 de agosto." },
    "canal": { "type": "string", "enum": ["vapi"] }
  }
}
```

Prueba:

```bash
curl -X POST "https://TU-DOMINIO-PUBLICO/resolve-date" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"rpm-automotive","date_text":"próximo viernes","canal":"vapi"}'
```

Si falta el día:

```json
{
  "success": false,
  "needs_clarification": true,
  "resolved_month": 7,
  "resolved_year": 2026,
  "message": "¿Qué día de julio prefieres?"
}
```

## Tool: check_availability

Nombre:

```text
check_availability
```

Descripcion:

```text
Revisa si un horario esta disponible en el calendario del negocio identificado por assistant_id. Debe usarse antes de crear una cita y antes de reagendar una cita.
```

URL:

```text
https://TU-DOMINIO-PUBLICO/check-availability
```

Metodo:

```text
POST
```

Headers:

```json
{
  "Content-Type": "application/json"
}
```

Body:

```json
{
  "assistant_id": "{{assistant_id}}",
  "telefono": "{{telefono}}",
  "fecha": "{{fecha}}",
  "fecha_text": "{{fecha_text}}",
  "hora": "{{hora}}",
  "canal": "vapi"
}
```

JSON Schema:

```json
{
  "type": "object",
  "required": ["assistant_id", "telefono", "hora", "canal"],
  "properties": {
    "assistant_id": { "type": "string", "description": "ID del asistente/negocio." },
    "telefono": { "type": "string", "description": "Telefono del cliente." },
    "fecha": { "type": "string", "description": "Fecha YYYY-MM-DD resuelta por resolve_date.", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" },
    "fecha_text": { "type": "string", "description": "Texto original de fecha si Vapi no pudo convertirla. Opcional; preferible usar resolve_date primero." },
    "hora": { "type": "string", "description": "Hora HH:MM de 24 horas.", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$" },
    "canal": { "type": "string", "enum": ["vapi"] }
  }
}
```

Prueba:

```bash
curl -X POST "https://TU-DOMINIO-PUBLICO/check-availability" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"asst_1","telefono":"+52 55 5000 0000","fecha":"2026-07-21","hora":"17:00","canal":"vapi"}'
```

## Tool: create_appointment

Nombre:

```text
create_appointment
```

Descripcion:

```text
Crea una cita nueva. Solo debe usarse si la intencion del cliente es agendar una cita nueva, despues de check_availability y despues de confirmacion clara.
```

URL:

```text
https://TU-DOMINIO-PUBLICO/create-appointment
```

Metodo:

```text
POST
```

Headers:

```json
{
  "Content-Type": "application/json"
}
```

Body:

```json
{
  "assistant_id": "{{assistant_id}}",
  "telefono": "{{telefono}}",
  "nombre": "{{nombre}}",
  "fecha": "{{fecha}}",
  "fecha_text": "{{fecha_text}}",
  "hora": "{{hora}}",
  "motivo": "{{motivo}}",
  "user_message": "{{user_message}}",
  "canal": "vapi"
}
```

JSON Schema:

```json
{
  "type": "object",
  "required": ["assistant_id", "telefono", "nombre", "hora", "motivo", "canal"],
  "properties": {
    "assistant_id": { "type": "string", "description": "ID del asistente/negocio." },
    "telefono": { "type": "string", "description": "Telefono del cliente." },
    "nombre": { "type": "string", "description": "Nombre del cliente." },
    "fecha": { "type": "string", "description": "Fecha YYYY-MM-DD resuelta por resolve_date.", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" },
    "fecha_text": { "type": "string", "description": "Texto original de fecha si Vapi no pudo convertirla. Opcional; preferible usar resolve_date primero." },
    "hora": { "type": "string", "description": "Hora HH:MM de 24 horas.", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$" },
    "motivo": { "type": "string", "description": "Motivo de la cita." },
    "user_message": { "type": "string", "description": "Mensaje original del usuario. Opcional, pero recomendado para que el backend bloquee la tool incorrecta si la intencion real era reagendar o cancelar." },
    "canal": { "type": "string", "enum": ["vapi"] }
  }
}
```

Prueba:

```bash
curl -X POST "https://TU-DOMINIO-PUBLICO/create-appointment" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"asst_1","telefono":"+52 55 5000 0000","nombre":"Juan Perez","fecha":"2026-07-21","hora":"17:00","motivo":"revision","user_message":"quiero agendar una cita nueva","canal":"vapi"}'
```

## Tool: update_appointment

Nombre:

```text
update_appointment
```

Descripcion:

```text
Reagenda una cita existente en Google Calendar. Nunca crea una cita nueva. Debe usarse solo si la intencion del cliente es cambiar/mover/reagendar una cita, despues de check_availability del nuevo horario y despues de confirmacion clara.
```

URL:

```text
https://TU-DOMINIO-PUBLICO/update-appointment
```

Metodo:

```text
POST
```

Headers:

```json
{
  "Content-Type": "application/json"
}
```

Body:

```json
{
  "assistant_id": "{{assistant_id}}",
  "telefono": "{{telefono}}",
  "event_id": "{{event_id}}",
  "nombre": "{{nombre}}",
  "fecha_actual": "{{fecha_actual}}",
  "hora_actual": "{{hora_actual}}",
  "fecha_actual_text": "{{fecha_actual_text}}",
  "lookup_month": "{{lookup_month}}",
  "lookup_year": "{{lookup_year}}",
  "fecha": "{{fecha}}",
  "fecha_nueva_text": "{{fecha_nueva_text}}",
  "hora": "{{hora}}",
  "motivo": "{{motivo}}",
  "user_message": "{{user_message}}",
  "canal": "vapi"
}
```

JSON Schema:

```json
{
  "type": "object",
  "required": ["assistant_id", "telefono", "fecha", "hora", "canal"],
  "properties": {
    "assistant_id": { "type": "string", "description": "ID del asistente/negocio." },
    "telefono": { "type": "string", "description": "Telefono del cliente. El backend lo normaliza, puede venir con +52, espacios o guiones." },
    "event_id": { "type": "string", "description": "ID del evento original. Opcional pero preferido si Vapi lo conoce." },
    "nombre": { "type": "string", "description": "Nombre del cliente para ayudar a ubicar la cita original. Opcional." },
    "fecha_actual": { "type": "string", "description": "Fecha de la cita original en formato YYYY-MM-DD. Opcional si no hay event_id.", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" },
    "hora_actual": { "type": "string", "description": "Hora de la cita original en formato HH:MM. Opcional si no hay event_id.", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$" },
    "fecha_actual_text": { "type": "string", "description": "Texto natural para ubicar la cita original, por ejemplo este mes, la del viernes, finales de julio. Opcional." },
    "lookup_month": { "type": "integer", "description": "Mes devuelto por resolve_date cuando falta día exacto. Opcional." },
    "lookup_year": { "type": "integer", "description": "Año devuelto por resolve_date cuando falta día exacto. Opcional." },
    "fecha": { "type": "string", "description": "Nueva fecha en formato YYYY-MM-DD.", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" },
    "fecha_nueva_text": { "type": "string", "description": "Texto natural de la nueva fecha si Vapi no pudo convertirla. Opcional; preferible usar resolve_date primero." },
    "hora": { "type": "string", "description": "Nueva hora en formato HH:MM.", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$" },
    "motivo": { "type": "string", "description": "Motivo actualizado. Opcional." },
    "user_message": { "type": "string", "description": "Mensaje original del usuario. Opcional, recomendado para conservar contexto de llamada." },
    "canal": { "type": "string", "enum": ["vapi"] }
  }
}
```

Prueba con event_id:

```bash
curl -X POST "https://TU-DOMINIO-PUBLICO/update-appointment" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"asst_1","telefono":"+52 55 5000 0000","event_id":"GOOGLE_EVENT_ID","fecha":"2026-07-22","hora":"18:00","motivo":"seguimiento","user_message":"quiero cambiar mi cita","canal":"vapi"}'
```

Prueba sin event_id, ubicando la cita original por fecha/hora:

```bash
curl -X POST "https://TU-DOMINIO-PUBLICO/update-appointment" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"asst_1","telefono":"55 5000 0000","nombre":"Juan Perez","fecha_actual":"2026-07-20","hora_actual":"13:00","fecha":"2026-07-21","hora":"14:00","user_message":"cambia la del sabado a la 1 para el domingo a las 2","canal":"vapi"}'
```

Si hay varias citas futuras:

```json
{
  "success": false,
  "needs_clarification": true,
  "message": "Encontre varias citas futuras. Necesito saber cual quieres modificar o cancelar.",
  "appointments": [
    { "event_id": "evt_1", "fecha": "2026-07-20", "hora": "13:00", "nombre": "Juan Perez", "motivo": "revision" }
  ]
}
```

## Tool: cancel_appointment

Nombre:

```text
cancel_appointment
```

Descripcion:

```text
Cancela una cita existente en Google Calendar. Nunca crea una cita nueva. Debe usarse solo si la intencion del cliente es cancelar/eliminar una cita y despues de confirmacion clara.
```

URL:

```text
https://TU-DOMINIO-PUBLICO/cancel-appointment
```

Metodo:

```text
POST
```

Headers:

```json
{
  "Content-Type": "application/json"
}
```

Body:

```json
{
  "assistant_id": "{{assistant_id}}",
  "telefono": "{{telefono}}",
  "event_id": "{{event_id}}",
  "nombre": "{{nombre}}",
  "fecha": "{{fecha}}",
  "fecha_text": "{{fecha_text}}",
  "lookup_month": "{{lookup_month}}",
  "lookup_year": "{{lookup_year}}",
  "hora": "{{hora}}",
  "user_message": "{{user_message}}",
  "canal": "vapi"
}
```

JSON Schema:

```json
{
  "type": "object",
  "required": ["assistant_id", "telefono", "canal"],
  "properties": {
    "assistant_id": { "type": "string", "description": "ID del asistente/negocio." },
    "telefono": { "type": "string", "description": "Telefono del cliente. El backend lo normaliza, puede venir con +52, espacios o guiones." },
    "event_id": { "type": "string", "description": "ID del evento a cancelar. Opcional pero preferido si Vapi lo conoce." },
    "nombre": { "type": "string", "description": "Nombre del cliente para ayudar a ubicar la cita. Opcional." },
    "fecha": { "type": "string", "description": "Fecha de la cita a cancelar en formato YYYY-MM-DD. Opcional si no hay event_id.", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" },
    "fecha_text": { "type": "string", "description": "Texto natural para ubicar la cita, por ejemplo este mes, la del viernes, finales de julio. Opcional." },
    "lookup_month": { "type": "integer", "description": "Mes devuelto por resolve_date cuando falta día exacto. Opcional." },
    "lookup_year": { "type": "integer", "description": "Año devuelto por resolve_date cuando falta día exacto. Opcional." },
    "hora": { "type": "string", "description": "Hora de la cita a cancelar en formato HH:MM. Opcional si no hay event_id. Si solo hay fecha, el backend busca en ese día y pide aclaración si hay varias.", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$" },
    "user_message": { "type": "string", "description": "Mensaje original del usuario. Opcional, recomendado para conservar contexto de llamada." },
    "canal": { "type": "string", "enum": ["vapi"] }
  }
}
```

Prueba con event_id:

```bash
curl -X POST "https://TU-DOMINIO-PUBLICO/cancel-appointment" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"asst_1","telefono":"+52 55 5000 0000","event_id":"GOOGLE_EVENT_ID","user_message":"quiero cancelar mi cita","canal":"vapi"}'
```

Prueba sin event_id, ubicando por fecha/hora:

```bash
curl -X POST "https://TU-DOMINIO-PUBLICO/cancel-appointment" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"asst_1","telefono":"55 5000 0000","nombre":"Juan Perez","fecha":"2026-07-20","hora":"13:00","user_message":"cancela la del sabado a la 1","canal":"vapi"}'
```

Respuesta exitosa:

```json
{
  "success": true,
  "message": "Listo, tu cita fue cancelada."
}
```

## Endpoints conversacionales opcionales

Si quieres que el backend administre el estado conversacional, Vapi puede mandar cada turno a:

```text
POST https://TU-DOMINIO-PUBLICO/message
POST https://TU-DOMINIO-PUBLICO/process-message
```

Body minimo:

```json
{
  "assistant_id": "{{assistant_id}}",
  "telefono": "{{telefono}}",
  "canal": "vapi",
  "message": "{{transcript}}"
}
```
