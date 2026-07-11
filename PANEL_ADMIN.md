# Panel administrativo KET

El panel vive dentro del mismo backend Flask desplegado en Render:

```text
https://ketbot.onrender.com/admin/
```

Por ahora es solo para el administrador de KET. No existe portal público para clientes.

## Rutas principales

- `GET /admin/`: lista clientes.
- `GET,POST /admin/clients/new`: crea un negocio.
- `GET,POST /admin/clients/<client_id>/edit`: edita información del negocio.
- `GET,POST /admin/clients/<client_id>/hours`: configura horarios por día.
- `GET /admin/clients/<client_id>/services`: lista servicios del negocio.
- `GET,POST /admin/clients/<client_id>/services/new`: crea servicio.
- `GET,POST /admin/services/<service_id>/edit`: edita servicio y disponibilidad.
- `GET /admin/clients/<client_id>/activity`: dashboard de actividad.
- `GET /admin/clients/<client_id>/activity/summary`: métricas agregadas del periodo.
- `GET /admin/clients/<client_id>/activity/list`: lista paginada de actividad.
- `GET /admin/clients/<client_id>/activity/<activity_id>`: detalle seguro de una actividad.
- `GET /admin/clients/<client_id>/activity/export.csv`: exportación CSV filtrada.
- `GET /admin/clients/<client_id>/activity/report.pdf`: reporte PDF del periodo.
- `POST /admin/clients/<client_id>/toggle`: activa o desactiva negocio.
- `POST /admin/services/<service_id>/toggle`: activa o desactiva servicio.
- `POST /vapi/webhook`: webhook opcional para registrar eventos de llamada enviados por Vapi.

Todas las rutas `/admin/` requieren Basic Auth con `ADMIN_USERNAME`, `ADMIN_PASSWORD` y `SECRET_KEY`.

## Tablas usadas

- `clientes`: perfil del negocio, assistant_id, calendario, zona horaria, teléfono, correo, dirección, descripción, información general, instrucciones del asistente, duración predeterminada y estado activo.
- `client_business_hours`: horario por `cliente_id` y día de semana.
- `servicios`: catálogo por cliente con precio, duración, canales, estado y notas internas.
- `service_availability`: disponibilidad por servicio y día.
- `citas`: citas existentes por cliente.
- `activity_interactions`: llamadas, conversaciones y acciones del asistente por cliente.
- `activity_events`: línea de tiempo idempotente de cada interacción.

## Configurar un cliente

1. Entra a `/admin/`.
2. Crea o abre un cliente.
3. Completa:
   - nombre comercial;
   - `assistant_id`;
   - calendario y credenciales;
   - teléfono, correo y dirección;
   - zona horaria;
   - descripción, mensaje de bienvenida, información general e instrucciones.
4. Activa el cliente si debe recibir citas.

El asistente identifica el negocio por `assistant_id`; nunca mezcla datos entre clientes.

## Configurar horarios

1. Entra al cliente.
2. Abre `Horarios`.
3. Para cada día marca abierto o cerrado.
4. Define apertura y cierre.
5. Opcionalmente agrega descansos en JSON:

```json
[{"start":"13:00","end":"14:00"}]
```

Puedes poner varios descansos:

```json
[
  {"start":"13:00","end":"14:00"},
  {"start":"16:00","end":"16:15"}
]
```

## Crear servicios

Cada servicio pertenece a un solo `client_id`.

Configura:

- nombre;
- descripción;
- precio;
- duración en minutos;
- activo;
- requiere cita;
- disponible por llamada;
- disponible por WhatsApp;
- notas internas.

Si un servicio está inactivo, el asistente no debe ofrecerlo ni agendarlo.

## Disponibilidad específica por servicio

En la edición del servicio, sección `Disponibilidad`:

- marca si el servicio está disponible por día;
- elige si usa el horario del negocio;
- o define hora inicial y final propia para ese día.

Ejemplo: `Alineación` puede estar activa lunes a viernes de `09:00` a `17:00`, aunque el negocio abra de `08:00` a `18:00`.

## Conexión con disponibilidad

`check_availability` valida en orden:

1. cliente activo por `assistant_id`;
2. horario del negocio del día solicitado;
3. descanso del negocio;
4. servicio del mismo cliente;
5. servicio activo;
6. canal permitido;
7. disponibilidad específica del servicio;
8. duración completa dentro del horario;
9. conflicto en Google Calendar.

Si no se envía servicio, usa la duración predeterminada del negocio.

## Conexión con crear y reagendar

`create_appointment` y `update_appointment` usan la misma validación y duración:

- si hay servicio, usan `servicio.duracion_minutos`;
- si no hay servicio, usan `cliente.duracion_cita_minutos`;
- validan horario de negocio y disponibilidad del servicio antes de crear o mover el evento en Google Calendar.

## Actividad y reportes

La sección `Actividad` muestra:

- métricas del periodo;
- servicios solicitados;
- demanda por día y hora;
- resultados de conversación;
- historial paginado;
- detalle por actividad;
- descarga CSV;
- reporte PDF.

La información siempre se filtra por `client_id`. El teléfono se muestra parcialmente oculto.

Las tools de citas registran actividad cuando ocurre:

- `availability_checked`;
- `appointment_created`;
- `appointment_cancelled`;
- `appointment_rescheduled`.

WhatsApp registra una actividad por mensaje recibido cuando puede identificar el negocio.

Vapi puede enviar eventos a:

```text
POST /vapi/webhook
```

El webhook acepta campos como:

- `assistant_id`;
- `type` o `event`;
- `call.id` o `external_id`;
- `telefono` o `customer_phone`;
- `duration_seconds`;
- `summary`;
- `transcript`;
- `error_code`;
- `error_message`.

La idempotencia usa `client_id + channel + external_id` para la interacción y `activity_id + event_type + external_event_id` para eventos.

Datos que dependen de que Vapi/WhatsApp los entregue:

- duración exacta de llamada;
- transcripción;
- resumen generado por proveedor;
- estado final de llamada;
- costo;
- ID externo confiable.

Si esos datos no llegan, el panel muestra estados vacíos o valores neutros sin inventarlos.

## Variables de entorno

Requeridas para proteger el panel:

```text
ADMIN_USERNAME
ADMIN_PASSWORD
SECRET_KEY
```

Las variables existentes de Google Calendar se mantienen igual.

## Deploy

No hay aplicación separada. Render sigue ejecutando:

```text
gunicorn app:app
```

Al iniciar, `init_db()` crea tablas nuevas y agrega columnas faltantes de forma idempotente.

## Acceso individual futuro para clientes

La arquitectura queda preparada para agregar una tabla futura `client_users` con:

- `client_id`;
- nombre;
- email;
- password hash o proveedor OAuth;
- rol/permisos;
- estado activo.

Ese portal deberá filtrar siempre por el `client_id` del usuario autenticado y nunca aceptar un `client_id` arbitrario enviado por el navegador.
