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
- `POST /admin/clients/<client_id>/toggle`: activa o desactiva negocio.
- `POST /admin/services/<service_id>/toggle`: activa o desactiva servicio.

Todas las rutas `/admin/` requieren Basic Auth con `ADMIN_USERNAME`, `ADMIN_PASSWORD` y `SECRET_KEY`.

## Tablas usadas

- `clientes`: perfil del negocio, assistant_id, calendario, zona horaria, teléfono, correo, dirección, descripción, información general, instrucciones del asistente, duración predeterminada y estado activo.
- `client_business_hours`: horario por `cliente_id` y día de semana.
- `servicios`: catálogo por cliente con precio, duración, canales, estado y notas internas.
- `service_availability`: disponibilidad por servicio y día.
- `citas`: citas existentes por cliente.

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
