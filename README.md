# Vapi Flask Calendar Backend Multi-Cliente

Backend Flask multi-cliente para Vapi + Google Calendar + SQLAlchemy. Cada llamada identifica al cliente por `assistant_id`, carga configuracion desde base de datos y usa el calendario correcto.

## Ejecutar localmente

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Por defecto usa SQLite local en `app.db`. Si `DATABASE_URL` existe, usa esa base. El proyecto tambien acepta URLs `postgres://` de Render y las normaliza a `postgresql://`.

## Panel admin

Abre:

```text
http://127.0.0.1:5000/admin
```

Desde el panel puedes:

- ver clientes;
- crear y editar clientes;
- activar o desactivar clientes;
- ver servicios por cliente;
- crear y editar servicios;
- activar o desactivar servicios.

Para proteger el panel en produccion, configura:

```env
ADMIN_USERNAME=
ADMIN_PASSWORD=
SECRET_KEY=
```

No escribas contrasenas reales en el README ni en el codigo. Define esos valores en `.env` local o en las variables de entorno del proveedor. Si `ADMIN_USERNAME`, `ADMIN_PASSWORD` o `SECRET_KEY` no existen, `/admin` queda desactivado y responde un error claro. Los endpoints de Vapi no usan esta autenticacion.

Para entrar al admin en local con credenciales configuradas:

```bash
curl -u "$ADMIN_USERNAME:$ADMIN_PASSWORD" http://127.0.0.1:5000/admin
```

## Crear un cliente desde el admin

1. Entra a `/admin`.
2. Haz clic en `Crear cliente`.
3. Completa:
   - nombre del negocio;
   - `assistant_id` de Vapi;
   - `calendar_id` de Google Calendar;
   - `credentials_file`, por ejemplo `credentials/cliente.json`;
   - horario, timezone, telefono, direccion y notas de prompt.
4. Guarda el cliente como activo.

No se edita codigo para agregar clientes.

## Agregar servicios

1. En `/admin`, abre `Servicios` en el cliente.
2. Haz clic en `Crear servicio`.
3. Completa nombre, precio y duracion en minutos.
4. Guarda el servicio como activo.

Solo los servicios activos aparecen en `/get-services` y en el prompt dinamico.

## Conectar Google Calendar

1. Crea o elige un calendario en Google Calendar.
2. Copia el `calendar_id`.
3. Crea credenciales OAuth o service account.
4. Si usas service account, comparte el calendario con el email de la service account.
5. Guarda el JSON en `credentials/`.
6. En el cliente del admin, coloca el `calendar_id` y el `credentials_file`.
7. Para Render, coloca tambien `credentials_env_var`, por ejemplo `GOOGLE_CREDENTIALS_RPM_AUTOMOTIVE_JSON`.

Cada cliente tiene su propio `calendar_id` y su propio archivo de credenciales.
En produccion se recomienda usar service accounts. El flujo OAuth interactivo local solo debe activarse temporalmente con `GOOGLE_OAUTH_LOCAL_FLOW=true`.

El sistema carga credenciales en este orden:

1. Si el cliente tiene `credentials_env_var` y esa variable existe, carga el JSON completo desde la variable de entorno.
2. Si no existe esa variable, usa `credentials_file` local, por ejemplo `credentials/rpm_automotive.json`.

No pongas JSON real de Google en README, codigo ni `.env.example`.

## Configurar Vapi

En cada asistente de Vapi:

1. Copia el `assistant_id`.
2. Guardalo en el cliente correspondiente del admin.
3. Configura las herramientas HTTP:
   - `POST https://TU-DOMINIO/check-availability`
   - `POST https://TU-DOMINIO/create-appointment`
   - `GET https://TU-DOMINIO/get-services?assistant_id={{assistant_id}}`
   - `GET https://TU-DOMINIO/client-prompt?assistant_id={{assistant_id}}`
4. Asegurate de enviar `assistant_id` o `assistantId`.

Si falta `assistant_id`, el backend responde:

```json
{
  "error": "assistant_id requerido"
}
```

## Endpoints

### `POST /check-availability`

Request:

```json
{
  "assistant_id": "rpm-automotive",
  "fecha": "2026-07-08",
  "hora": "15:00",
  "servicio_id": 1
}
```

Response disponible:

```json
{
  "available": true,
  "message": "Si hay disponibilidad el 2026-07-08 a las 15:00.",
  "client": "RPM Automotive"
}
```

### `POST /create-appointment`

Request:

```json
{
  "assistant_id": "rpm-automotive",
  "nombre": "John",
  "telefono": "123456789",
  "fecha": "2026-07-08",
  "hora": "15:00",
  "servicio_id": 1,
  "motivo": "Afinacion"
}
```

Response:

```json
{
  "success": true,
  "message": "Cita agendada para John el 2026-07-08 a las 15:00.",
  "calendar_link": "https://www.google.com/calendar/event?..."
}
```

### `GET /get-services`

Request:

```text
/get-services?assistant_id=rpm-automotive
```

Response:

```json
[
  {
    "id": 1,
    "nombre": "Gelish",
    "precio": 350,
    "duracion_minutos": 60
  }
]
```

Si no hay servicios activos, devuelve `[]`.

### `GET /client-prompt`

Request:

```text
/client-prompt?assistant_id=rpm-automotive
```

Response:

```json
{
  "assistant_id": "rpm-automotive",
  "cliente": "RPM Automotive",
  "prompt": "Eres el asistente telefonico de..."
}
```

El prompt se genera con `prompt_template.txt`, datos del cliente y servicios activos.

## Desplegar en Render

1. Sube el proyecto a un repo.
2. En Render, crea una base PostgreSQL.
3. Copia el `DATABASE_URL` de PostgreSQL.
4. Crea un Web Service apuntando al repo.
5. Build Command:

```bash
pip install -r requirements.txt
```

6. Start Command:

```bash
gunicorn app:app
```

La instancia Flask se llama `app` en `app.py`, por eso `gunicorn app:app` es el comando correcto.

7. Configura variables de entorno en Render.
8. Para credenciales de Google, crea una variable por cliente con el JSON completo de la service account como valor. Usa nombres como:

```env
GOOGLE_CREDENTIALS_RPM_AUTOMOTIVE_JSON=
GOOGLE_CREDENTIALS_UNAS_LA_COMER_JSON=
```

9. En `/admin`, edita cada cliente y asigna `credentials_env_var` al nombre de su variable. Ejemplo: `GOOGLE_CREDENTIALS_RPM_AUTOMOTIVE_JSON`.

Render puede entregar `DATABASE_URL` como `postgres://...`; el backend lo normaliza automaticamente a `postgresql://...` para SQLAlchemy.

## Variables de entorno

```env
DATABASE_URL=
FLASK_HOST=0.0.0.0
PORT=5000
APPOINTMENT_MINUTES=60
CORS_ORIGINS=*
LOG_LEVEL=INFO
SECRET_KEY=
ADMIN_USERNAME=
ADMIN_PASSWORD=
GOOGLE_OAUTH_LOCAL_FLOW=false

RPM_ASSISTANT_ID=rpm-automotive
RPM_CALENDAR_ID=primary
RPM_CREDENTIALS_FILE=credentials/rpm_automotive.json
RPM_CREDENTIALS_ENV_VAR=GOOGLE_CREDENTIALS_RPM_AUTOMOTIVE_JSON
GOOGLE_CREDENTIALS_RPM_AUTOMOTIVE_JSON=
RPM_TIMEZONE=America/Mexico_City

UNAS_ASSISTANT_ID=unas-la-comer
UNAS_CALENDAR_ID=primary
UNAS_CREDENTIALS_FILE=credentials/unas_la_comer.json
UNAS_CREDENTIALS_ENV_VAR=GOOGLE_CREDENTIALS_UNAS_LA_COMER_JSON
GOOGLE_CREDENTIALS_UNAS_LA_COMER_JSON=
UNAS_TIMEZONE=America/Mexico_City
```

Las variables `RPM_*` y `UNAS_*` solo se usan para sembrar clientes iniciales si no existen. Los clientes reales se administran desde la base de datos.

## Probar en Render

Despues del deploy:

```bash
curl https://TU-SERVICIO.onrender.com/
curl "https://TU-SERVICIO.onrender.com/get-services?assistant_id=rpm-automotive"
curl "https://TU-SERVICIO.onrender.com/client-prompt?assistant_id=rpm-automotive"
```

Para validar que `/admin` esta protegido:

```bash
curl -i https://TU-SERVICIO.onrender.com/admin/
curl -u "$ADMIN_USERNAME:$ADMIN_PASSWORD" https://TU-SERVICIO.onrender.com/admin/
```

Para Vapi, usa las URLs publicas de Render:

```text
https://TU-SERVICIO.onrender.com/check-availability
https://TU-SERVICIO.onrender.com/create-appointment
```

## Logging

El backend registra:

- endpoint usado;
- `assistant_id` recibido;
- cliente identificado;
- fecha y hora solicitadas;
- disponibilidad;
- citas creadas;
- errores de validacion;
- errores de Google Calendar.

No registra credenciales ni API keys.
