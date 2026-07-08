import secrets
from datetime import time
from decimal import Decimal, InvalidOperation
from functools import wraps
from hmac import compare_digest
from typing import Callable, TypeVar

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import Config
from database import session_scope
from models import Cliente, Servicio


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
F = TypeVar("F", bound=Callable)


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
                session.add(_client_from_form(Cliente()))
                session.flush()
            flash("Cliente creado.", "success")
            return redirect(url_for("admin.clients_index"))
        except (ValueError, IntegrityError) as exc:
            flash(f"No se pudo crear el cliente: {_form_error(exc)}", "danger")

    return render_template("admin/client_form.html", cliente=None, title="Crear cliente")


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
                session.flush()
                flash("Cliente actualizado.", "success")
                return redirect(url_for("admin.clients_index"))
            except (ValueError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    session.rollback()
                flash(f"No se pudo actualizar el cliente: {_form_error(exc)}", "danger")

        return render_template("admin/client_form.html", cliente=cliente, title="Editar cliente")


@admin_bp.post("/clients/<int:client_id>/toggle")
@admin_auth_required
def client_toggle(client_id: int):
    with session_scope() as session:
        cliente = session.get(Cliente, client_id)
        if cliente is None:
            flash("Cliente no encontrado.", "danger")
        else:
            cliente.activo = not cliente.activo
            flash("Estado del cliente actualizado.", "success")
    return redirect(url_for("admin.clients_index"))


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
                flash("Servicio creado.", "success")
                return redirect(url_for("admin.services_index", client_id=client_id))
            except (ValueError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    session.rollback()
                flash(f"No se pudo crear el servicio: {_form_error(exc)}", "danger")

        return render_template("admin/service_form.html", cliente=cliente, servicio=None, title="Crear servicio")


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
                session.flush()
                flash("Servicio actualizado.", "success")
                return redirect(url_for("admin.services_index", client_id=servicio.cliente_id))
            except (ValueError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    session.rollback()
                flash(f"No se pudo actualizar el servicio: {_form_error(exc)}", "danger")

        return render_template("admin/service_form.html", cliente=cliente, servicio=servicio, title="Editar servicio")


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
    cliente.direccion = request.form.get("direccion", "").strip()
    cliente.prompt = request.form.get("prompt", "").strip()
    cliente.activo = request.form.get("activo") == "on"
    return cliente


def _service_from_form(servicio: Servicio) -> Servicio:
    servicio.nombre = _required("nombre")
    servicio.precio = _parse_decimal(request.form.get("precio", "").strip())
    servicio.duracion_minutos = int(_required("duracion_minutos"))
    if servicio.duracion_minutos <= 0:
        raise ValueError("La duracion debe ser mayor a cero.")
    servicio.activo = request.form.get("activo") == "on"
    return servicio


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
