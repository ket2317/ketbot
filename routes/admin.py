import json
import logging
import secrets
from datetime import time
from decimal import Decimal, InvalidOperation
from functools import wraps
from hmac import compare_digest
from typing import Callable, TypeVar

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from activity_service import (
    activity_query,
    dashboard_data,
    export_csv,
    generate_pdf_report,
    load_activities,
    report_filename,
    resolve_period,
    serialize_activity,
    serialize_activity_detail,
)
from config import Config
from database import session_scope
from models import ActivityInteraction, ClientBusinessHour, Cliente, ServiceAvailability, Servicio, WhatsAppAccount


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
F = TypeVar("F", bound=Callable)
logger = logging.getLogger(__name__)
WEEKDAYS = [
    (0, "Lunes"),
    (1, "Martes"),
    (2, "Miércoles"),
    (3, "Jueves"),
    (4, "Viernes"),
    (5, "Sábado"),
    (6, "Domingo"),
]


def admin_auth_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not Config.ADMIN_USERNAME or not Config.ADMIN_PASSWORD or not Config.SECRET_KEY:
            return Response(
                "Admin no configurado: define ADMIN_USERNAME, ADMIN_PASSWORD y SECRET_KEY.",
                503,
                mimetype="text/plain",
            )

        auth = request.authorization
        valid = (
            auth is not None
            and compare_digest(auth.username or "", Config.ADMIN_USERNAME)
            and compare_digest(auth.password or "", Config.ADMIN_PASSWORD)
        )
        if valid:
            if request.method == "POST" and not _valid_csrf_token():
                return Response("CSRF token inválido.", 400, mimetype="text/plain")
            return view(*args, **kwargs)

        return Response(
            "Autenticacion requerida",
            401,
            {"WWW-Authenticate": 'Basic realm="Admin"'},
        )

    return wrapped  # type: ignore[return-value]


@admin_bp.context_processor
def inject_csrf_token():
    return {"csrf_token": _csrf_token}


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _valid_csrf_token() -> bool:
    token = session.get("_csrf_token", "")
    submitted = request.form.get("csrf_token", "")
    return bool(token and submitted and compare_digest(token, submitted))


@admin_bp.get("/")
@admin_auth_required
def clients_index():
    with session_scope() as session:
        clientes = session.scalars(select(Cliente).order_by(Cliente.nombre)).all()
        return render_template("admin/clients.html", clientes=clientes)


@admin_bp.route("/clients/new", methods=["GET", "POST"])
@admin_auth_required
def client_new():
    if request.method == "POST":
        try:
            with session_scope() as session:
                cliente = _client_from_form(Cliente())
                session.add(cliente)
                session.flush()
                _whatsapp_account_from_form(session, cliente)
                _ensure_business_hours(session, cliente)
                logger.info("admin_change entity=cliente action=created client_id=%s admin=%s", cliente.id, _admin_name())
            flash("Cliente creado.", "success")
            return redirect(url_for("admin.clients_index"))
        except (ValueError, IntegrityError) as exc:
            flash(f"No se pudo crear el cliente: {_form_error(exc)}", "danger")

    return render_template("admin/client_form.html", cliente=None, title="Crear cliente", whatsapp_account=None)


@admin_bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@admin_auth_required
def client_edit(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            flash("Cliente no encontrado.", "danger")
            return redirect(url_for("admin.clients_index"))

        if request.method == "POST":
            try:
                _client_from_form(cliente)
                _whatsapp_account_from_form(session, cliente)
                session.flush()
                logger.info("admin_change entity=cliente action=updated client_id=%s admin=%s", client_id, _admin_name())
                flash("Cliente actualizado.", "success")
                return redirect(url_for("admin.clients_index"))
            except (ValueError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    session.rollback()
                flash(f"No se pudo actualizar el cliente: {_form_error(exc)}", "danger")

        return render_template(
            "admin/client_form.html",
            cliente=cliente,
            title="Editar cliente",
            whatsapp_account=_primary_whatsapp_account(cliente),
        )


@admin_bp.post("/clients/<int:client_id>/toggle")
@admin_auth_required
def client_toggle(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            flash("Cliente no encontrado.", "danger")
        else:
            cliente.activo = not cliente.activo
            logger.info(
                "admin_change entity=cliente action=toggle client_id=%s active=%s admin=%s",
                client_id,
                cliente.activo,
                _admin_name(),
            )
            flash("Estado del cliente actualizado.", "success")
    return redirect(url_for("admin.clients_index"))


@admin_bp.route("/clients/<int:client_id>/hours", methods=["GET", "POST"])
@admin_auth_required
def client_hours(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            flash("Cliente no encontrado.", "danger")
            return redirect(url_for("admin.clients_index"))

        _ensure_business_hours(session, cliente)
        if request.method == "POST":
            try:
                _business_hours_from_form(cliente)
                session.flush()
                logger.info("admin_change entity=business_hours action=updated client_id=%s admin=%s", client_id, _admin_name())
                flash("Horarios actualizados.", "success")
                return redirect(url_for("admin.client_hours", client_id=client_id))
            except ValueError as exc:
                flash(f"No se pudieron actualizar los horarios: {exc}", "danger")

        hours = sorted(cliente.horarios, key=lambda row: row.weekday)
        return render_template("admin/client_hours.html", cliente=cliente, hours=hours, weekdays=WEEKDAYS)


@admin_bp.get("/clients/<int:client_id>/services")
@admin_auth_required
def services_index(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            flash("Cliente no encontrado.", "danger")
            return redirect(url_for("admin.clients_index"))
        servicios = session.scalars(
            select(Servicio).where(Servicio.cliente_id == client_id).order_by(Servicio.nombre)
        ).all()
        return render_template("admin/services.html", cliente=cliente, servicios=servicios)


@admin_bp.get("/clients/<int:client_id>/activity")
@admin_auth_required
def client_activity(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            flash("Cliente no encontrado.", "danger")
            return redirect(url_for("admin.clients_index"))
        period = resolve_period(request.args, cliente.timezone)
        data = dashboard_data(session, cliente, period, request.args)
        activities, total, page, per_page = load_activities(session, cliente, period, request.args)
        servicios = session.scalars(
            select(Servicio).where(Servicio.cliente_id == client_id).order_by(Servicio.nombre)
        ).all()
        return render_template(
            "admin/activity.html",
            cliente=cliente,
            period=period,
            data=data,
            activities=[serialize_activity(item, cliente.timezone) for item in activities],
            total=total,
            page=page,
            per_page=per_page,
            servicios=servicios,
        )


@admin_bp.get("/clients/<int:client_id>/activity/summary")
@admin_auth_required
def client_activity_summary(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            return jsonify({"success": False, "message": "Cliente no encontrado."}), 404
        period = resolve_period(request.args, cliente.timezone)
        return jsonify({"success": True, "data": dashboard_data(session, cliente, period, request.args)})


@admin_bp.get("/clients/<int:client_id>/activity/list")
@admin_auth_required
def client_activity_list(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            return jsonify({"success": False, "message": "Cliente no encontrado."}), 404
        period = resolve_period(request.args, cliente.timezone)
        activities, total, page, per_page = load_activities(session, cliente, period, request.args)
        return jsonify(
            {
                "success": True,
                "items": [serialize_activity(item, cliente.timezone) for item in activities],
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )


@admin_bp.get("/clients/<int:client_id>/activity/<int:activity_id>")
@admin_auth_required
def client_activity_detail(client_id: int, activity_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            return jsonify({"success": False, "message": "Cliente no encontrado."}), 404
        activity = session.scalar(
            select(ActivityInteraction)
            .where(ActivityInteraction.id == activity_id, ActivityInteraction.cliente_id == cliente.id)
            .options(selectinload(ActivityInteraction.events))
        )
        if activity is None or activity.cliente_id != cliente.id:
            return jsonify({"success": False, "message": "Actividad no encontrada."}), 404
        return jsonify({"success": True, "activity": serialize_activity_detail(activity, cliente.timezone)})


@admin_bp.get("/clients/<int:client_id>/activity/export.csv")
@admin_auth_required
def client_activity_export_csv(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            return Response("Cliente no encontrado.", 404, mimetype="text/plain")
        period = resolve_period(request.args, cliente.timezone)
        activities = session.scalars(activity_query(cliente, period, request.args)).all()
        csv_text = export_csv(list(activities), cliente.timezone)
        logger.info("admin_report_download type=csv client_id=%s admin=%s", client_id, _admin_name())
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{report_filename(cliente, period, "csv", "actividad")}"'},
        )


@admin_bp.get("/clients/<int:client_id>/activity/report.pdf")
@admin_auth_required
def client_activity_report_pdf(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            return Response("Cliente no encontrado.", 404, mimetype="text/plain")
        period = resolve_period(request.args, cliente.timezone)
        data = dashboard_data(session, cliente, period, request.args)
        pdf_bytes = generate_pdf_report(cliente, period, data)
        logger.info("admin_report_download type=pdf client_id=%s admin=%s", client_id, _admin_name())
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{report_filename(cliente, period, "pdf", "reporte")}"'},
        )


@admin_bp.route("/clients/<int:client_id>/services/new", methods=["GET", "POST"])
@admin_auth_required
def service_new(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            flash("Cliente no encontrado.", "danger")
            return redirect(url_for("admin.clients_index"))

        if request.method == "POST":
            try:
                servicio = _service_from_form(Servicio(cliente_id=client_id))
                session.add(servicio)
                session.flush()
                _service_availability_from_form(session, servicio)
                logger.info(
                    "admin_change entity=servicio action=created client_id=%s service_id=%s admin=%s",
                    client_id,
                    servicio.id,
                    _admin_name(),
                )
                flash("Servicio creado.", "success")
                return redirect(url_for("admin.services_index", client_id=client_id))
            except (ValueError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    session.rollback()
                flash(f"No se pudo crear el servicio: {_form_error(exc)}", "danger")

        return render_template(
            "admin/service_form.html",
            cliente=cliente,
            servicio=None,
            title="Crear servicio",
            availability=[],
            availability_by_day={},
            weekdays=WEEKDAYS,
        )


@admin_bp.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
@admin_auth_required
def service_edit(service_id: int):
    with session_scope() as session:
        servicio = session.get(Servicio, service_id)
        if servicio is None:
            flash("Servicio no encontrado.", "danger")
            return redirect(url_for("admin.clients_index"))
        cliente = servicio.cliente

        if request.method == "POST":
            try:
                _service_from_form(servicio)
                _service_availability_from_form(session, servicio)
                session.flush()
                logger.info(
                    "admin_change entity=servicio action=updated client_id=%s service_id=%s admin=%s",
                    servicio.cliente_id,
                    servicio.id,
                    _admin_name(),
                )
                flash("Servicio actualizado.", "success")
                return redirect(url_for("admin.services_index", client_id=servicio.cliente_id))
            except (ValueError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    session.rollback()
                flash(f"No se pudo actualizar el servicio: {_form_error(exc)}", "danger")

        _ensure_service_availability(session, servicio)
        return render_template(
            "admin/service_form.html",
            cliente=cliente,
            servicio=servicio,
            title="Editar servicio",
            availability=sorted(servicio.disponibilidad, key=lambda row: row.weekday),
            availability_by_day={row.weekday: row for row in servicio.disponibilidad},
            weekdays=WEEKDAYS,
        )


@admin_bp.post("/services/<int:service_id>/toggle")
@admin_auth_required
def service_toggle(service_id: int):
    client_id = None
    with session_scope() as session:
        servicio = session.get(Servicio, service_id)
        if servicio is None:
            flash("Servicio no encontrado.", "danger")
        else:
            servicio.activo = not servicio.activo
            client_id = servicio.cliente_id
            logger.info(
                "admin_change entity=servicio action=toggle client_id=%s service_id=%s active=%s admin=%s",
                client_id,
                servicio.id,
                servicio.activo,
                _admin_name(),
            )
            flash("Estado del servicio actualizado.", "success")

    if client_id is None:
        return redirect(url_for("admin.clients_index"))
    return redirect(url_for("admin.services_index", client_id=client_id))


def _client_from_form(cliente: Cliente) -> Cliente:
    cliente.nombre = _required("nombre")
    cliente.assistant_id = _required("assistant_id")
    cliente.calendar_id = _required("calendar_id")
    cliente.credentials_file = _required("credentials_file")
    cliente.credentials_env_var = request.form.get("credentials_env_var", "").strip()
    cliente.horario_inicio = _parse_time(_required("horario_inicio"))
    cliente.horario_fin = _parse_time(_required("horario_fin"))
    if cliente.horario_fin <= cliente.horario_inicio:
        raise ValueError("El horario fin debe ser mayor al horario inicio.")
    cliente.timezone = _required("timezone")
    cliente.telefono = request.form.get("telefono", "").strip()
    cliente.email = request.form.get("email", "").strip()
    cliente.direccion = request.form.get("direccion", "").strip()
    cliente.descripcion = request.form.get("descripcion", "").strip()
    cliente.mensaje_bienvenida = request.form.get("mensaje_bienvenida", "").strip()
    cliente.informacion_general = request.form.get("informacion_general", "").strip()
    cliente.instrucciones_asistente = request.form.get("instrucciones_asistente", "").strip()
    cliente.prompt = request.form.get("prompt", "").strip()
    cliente.duracion_cita_minutos = int(request.form.get("duracion_cita_minutos", "60") or 60)
    if cliente.duracion_cita_minutos <= 0:
        raise ValueError("La duración predeterminada debe ser mayor a cero.")
    cliente.activo = request.form.get("activo") == "on"
    return cliente


def _primary_whatsapp_account(cliente: Cliente) -> WhatsAppAccount | None:
    accounts = sorted(cliente.whatsapp_accounts, key=lambda row: row.id or 0)
    return accounts[0] if accounts else None


def _whatsapp_account_from_form(session, cliente: Cliente) -> None:
    phone_number_id = request.form.get("whatsapp_phone_number_id", "").strip()
    verify_token = request.form.get("whatsapp_verify_token", "").strip()
    access_token_env_var = request.form.get("whatsapp_access_token_env_var", "").strip()
    activo = request.form.get("whatsapp_activo") == "on"
    existing = _primary_whatsapp_account(cliente)

    if not any((phone_number_id, verify_token, access_token_env_var, activo)):
        if existing is not None:
            session.delete(existing)
        return

    if not phone_number_id:
        raise ValueError("phone_number_id de WhatsApp es requerido si WhatsApp está configurado.")

    account = existing or WhatsAppAccount(cliente_id=cliente.id)
    account.phone_number_id = phone_number_id
    account.verify_token = verify_token or None
    account.access_token_env_var = access_token_env_var
    account.activo = activo
    if existing is None:
        cliente.whatsapp_accounts.append(account)
        session.add(account)
    logger.info("admin_change entity=whatsapp_account action=upsert client_id=%s admin=%s", cliente.id, _admin_name())


def _service_from_form(servicio: Servicio) -> Servicio:
    servicio.nombre = _required("nombre")
    servicio.descripcion = request.form.get("descripcion", "").strip()
    servicio.precio = _parse_decimal(request.form.get("precio", "").strip())
    servicio.duracion_minutos = int(_required("duracion_minutos"))
    if servicio.duracion_minutos <= 0:
        raise ValueError("La duracion debe ser mayor a cero.")
    servicio.requiere_cita = request.form.get("requiere_cita") == "on"
    servicio.disponible_por_llamada = request.form.get("disponible_por_llamada") == "on"
    servicio.disponible_por_whatsapp = request.form.get("disponible_por_whatsapp") == "on"
    servicio.notas_internas = request.form.get("notas_internas", "").strip()
    servicio.activo = request.form.get("activo") == "on"
    return servicio


def _ensure_business_hours(session, cliente: Cliente) -> None:
    existing = {row.weekday: row for row in cliente.horarios}
    for weekday, _label in WEEKDAYS:
        if weekday in existing:
            continue
        row = ClientBusinessHour(
            cliente_id=cliente.id,
            weekday=weekday,
            is_open=weekday < 6,
            start_time=cliente.horario_inicio,
            end_time=cliente.horario_fin,
            breaks_json="[]",
        )
        cliente.horarios.append(row)
        session.add(row)
    session.flush()


def _business_hours_from_form(cliente: Cliente) -> None:
    rows = {row.weekday: row for row in cliente.horarios}
    for weekday, _label in WEEKDAYS:
        row = rows[weekday]
        row.is_open = request.form.get(f"open_{weekday}") == "on"
        row.start_time = _parse_time(_required(f"start_{weekday}"))
        row.end_time = _parse_time(_required(f"end_{weekday}"))
        if row.end_time <= row.start_time:
            raise ValueError("La hora de cierre debe ser mayor a la hora de apertura.")
        row.breaks_json = _normalize_breaks(request.form.get(f"breaks_{weekday}", "").strip())


def _ensure_service_availability(session, servicio: Servicio) -> None:
    existing = {row.weekday: row for row in servicio.disponibilidad}
    for weekday, _label in WEEKDAYS:
        if weekday in existing:
            continue
        row = ServiceAvailability(
            service_id=servicio.id,
            weekday=weekday,
            is_available=True,
            use_business_hours=True,
        )
        servicio.disponibilidad.append(row)
        session.add(row)
    session.flush()


def _service_availability_from_form(session, servicio: Servicio) -> None:
    if servicio.id is None:
        session.flush()
    _ensure_service_availability(session, servicio)
    rows = {row.weekday: row for row in servicio.disponibilidad}
    for weekday, _label in WEEKDAYS:
        row = rows[weekday]
        row.is_available = request.form.get(f"available_{weekday}") == "on"
        row.use_business_hours = request.form.get(f"use_business_hours_{weekday}") == "on"
        start_value = request.form.get(f"service_start_{weekday}", "").strip()
        end_value = request.form.get(f"service_end_{weekday}", "").strip()
        if row.use_business_hours:
            row.start_time = None
            row.end_time = None
            continue
        row.start_time = _parse_time(start_value)
        row.end_time = _parse_time(end_value)
        if row.end_time <= row.start_time:
            raise ValueError("La hora final del servicio debe ser mayor a la hora inicial.")


def _normalize_breaks(value: str) -> str:
    if not value:
        return "[]"
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Los descansos deben ser JSON válido, ejemplo: [{\"start\":\"13:00\",\"end\":\"14:00\"}].") from exc
    if not isinstance(parsed, list):
        raise ValueError("Los descansos deben ser una lista JSON.")
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Cada descanso debe tener start y end.")
        start = _parse_time(str(item.get("start", "")))
        end = _parse_time(str(item.get("end", "")))
        if end <= start:
            raise ValueError("Cada descanso debe terminar después de iniciar.")
    return json.dumps(parsed, ensure_ascii=False)


def _required(field: str) -> str:
    value = request.form.get(field, "").strip()
    if not value:
        raise ValueError(f"{field} es requerido.")
    return value


def _parse_time(value: str) -> time:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except ValueError as exc:
        raise ValueError("El horario debe usar formato HH:MM.") from exc


def _parse_decimal(value: str) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("El precio debe ser numerico.") from exc


def _form_error(exc: Exception) -> str:
    if isinstance(exc, IntegrityError):
        return "revisa que los valores únicos no estén duplicados."
    return str(exc)


def _admin_name() -> str:
    auth = request.authorization
    return auth.username if auth else "unknown"
