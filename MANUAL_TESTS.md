# Pruebas manuales KET prioridad 1

Usa el backend en local:

```bash
.venv/bin/python app.py
```

Todas las pruebas deben incluir `assistant_id`, `telefono` y `canal`.

## 1. Crear cita normal

Enviar a `/message`:

```json
{
  "assistant_id": "asst_1",
  "telefono": "+5215550000000",
  "canal": "whatsapp",
  "message": "Quiero una cita mañana a las 4",
  "nombre": "Juan",
  "motivo": "revisión"
}
```

Resultado esperado: el bot revisa disponibilidad y pide confirmación sin crear la cita.

Después enviar:

```json
{
  "assistant_id": "asst_1",
  "telefono": "+5215550000000",
  "canal": "whatsapp",
  "message": "sí"
}
```

Resultado esperado: se llama a Google Calendar y se crea la cita.

## 2. Corrección antes de confirmar

Enviar una solicitud completa, esperar confirmación y después enviar:

```json
{
  "assistant_id": "asst_1",
  "telefono": "+5215550000000",
  "canal": "whatsapp",
  "message": "mejor a las 5"
}
```

Resultado esperado: el bot revisa disponibilidad de la nueva hora y vuelve a pedir confirmación.

## 3. Reagendar

Enviar:

```json
{
  "assistant_id": "asst_1",
  "telefono": "+5215550000000",
  "canal": "vapi",
  "message": "quiero cambiar mi cita para 2026-07-21 a las 5"
}
```

Resultado esperado: el bot encuentra la cita futura, revisa disponibilidad y pide confirmar el cambio.

Después enviar `confirmo`.

## 4. Cancelar

Enviar:

```json
{
  "assistant_id": "asst_1",
  "telefono": "+5215550000000",
  "canal": "whatsapp",
  "message": "cancela mi cita"
}
```

Resultado esperado: el bot encuentra la cita futura y pide confirmación.

Después enviar `ok`.

## 5. Error de Google Calendar

Configurar temporalmente un `GOOGLE_CALENDAR_ID` inválido o credenciales inválidas y repetir una consulta de disponibilidad.

Resultado esperado: el usuario recibe un mensaje amable sin detalles técnicos; el log del backend incluye `assistant_id`, `canal`, `telefono`, `accion` y el error real.
