import csv
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from models import ActivityEvent, ActivityInteraction, Cita, Cliente, Servicio
from services import normalize_phone


OUTCOME_LABELS = {
    "appointment_created": "Cita creada",
    "appointment_cancelled": "Cita cancelada",
    "appointment_rescheduled": "Cita reagendada",
    "availability_checked": "Disponibilidad consultada",
    "information_provided": "Información proporcionada",
    "human_transfer_requested": "Solicitud de atención humana",
    "no_availability": "Sin disponibilidad",
    "abandoned": "Abandonada",
    "failed": "Fallida",
    "service_not_identified": "Servicio no identificado",
    "other": "Otro",
}
STATUS_LABELS = {
    "started": "Iniciada",
    "completed": "Completada",
    "failed": "Fallida",
    "abandoned": "Abandonada",
}
CHANNEL_LABELS = {
    "vapi": "Llamada",
    "call": "Llamada",
    "llamada": "Llamada",
    "whatsapp": "WhatsApp",
}


@dataclass(frozen=True)
class Period:
    label: str
    start_utc: datetime
    end_utc: datetime
    previous_start_utc: datetime
    previous_end_utc: datetime
    display_start: date
    display_end: date


def record_activity(
    session: Session,
    cliente: Cliente,
    *,
    channel: str,
    outcome: str,
    event_type: str,
    external_id: str | None = None,
    external_event_id: str | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    status: str = "completed",
    requested_service: Servicio | None = None,
    requested_service_name: str | None = None,
    appointment: Cita | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    duration_seconds: int | None = None,
    summary: str | None = None,
    transcript: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ActivityInteraction:
    normalized_channel = normalize_channel(channel)
    activity = None
    if external_id:
        activity = session.scalar(
            select(ActivityInteraction).where(
                ActivityInteraction.cliente_id == cliente.id,
                ActivityInteraction.channel == normalized_channel,
                ActivityInteraction.external_id == external_id,
            )
        )

    now = _utcnow()
    if activity is None:
        activity = ActivityInteraction(
            cliente_id=cliente.id,
            channel=normalized_channel,
            external_id=external_id,
            started_at=_naive_utc(started_at or now),
            created_at=now,
        )
        session.add(activity)

    activity.customer_name = customer_name or activity.customer_name
    activity.customer_phone = customer_phone or activity.customer_phone
    activity.ended_at = _naive_utc(ended_at) if ended_at else activity.ended_at
    activity.duration_seconds = duration_seconds if duration_seconds is not None else activity.duration_seconds
    activity.status = status or activity.status
    activity.outcome = outcome or activity.outcome
    activity.requested_service_id = requested_service.id if requested_service else activity.requested_service_id
    activity.requested_service_name_snapshot = (
        requested_service.nombre if requested_service else requested_service_name or activity.requested_service_name_snapshot
    )
    activity.appointment_id = appointment.id if appointment else activity.appointment_id
    activity.summary = summary or activity.summary
    activity.transcript = transcript or activity.transcript
    activity.error_code = error_code or activity.error_code
    activity.error_message = error_message or activity.error_message
    activity.metadata_json = json.dumps({**_metadata(activity), **(metadata or {})}, ensure_ascii=False)
    activity.updated_at = now
    session.flush()

    add_activity_event(
        session,
        activity,
        event_type,
        external_event_id=external_event_id or external_id,
        metadata=metadata,
    )
    return activity


def add_activity_event(
    session: Session,
    activity: ActivityInteraction,
    event_type: str,
    *,
    external_event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ActivityEvent | None:
    if external_event_id:
        existing = session.scalar(
            select(ActivityEvent).where(
                ActivityEvent.activity_id == activity.id,
                ActivityEvent.event_type == event_type,
                ActivityEvent.external_event_id == external_event_id,
            )
        )
        if existing:
            return existing

    event = ActivityEvent(
        activity_id=activity.id,
        event_type=event_type,
        external_event_id=external_event_id,
        occurred_at=_utcnow(),
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
    )
    session.add(event)
    return event


def resolve_period(args: dict[str, Any], timezone: str) -> Period:
    tz = _timezone(timezone)
    today = datetime.now(tz).date()
    period = str(args.get("period") or "this_month")

    if period == "today":
        start = today
        end = today + timedelta(days=1)
    elif period == "last_7_days":
        start = today - timedelta(days=6)
        end = today + timedelta(days=1)
    elif period == "previous_month":
        first_this_month = today.replace(day=1)
        last_previous = first_this_month - timedelta(days=1)
        start = last_previous.replace(day=1)
        end = first_this_month
    elif period == "custom":
        start = _parse_date_arg(args.get("start_date")) or today.replace(day=1)
        end_inclusive = _parse_date_arg(args.get("end_date")) or today
        end = end_inclusive + timedelta(days=1)
    else:
        period = "this_month"
        start = today.replace(day=1)
        end = today + timedelta(days=1)

    if end <= start:
        end = start + timedelta(days=1)

    start_local = datetime.combine(start, time.min, tzinfo=tz)
    end_local = datetime.combine(end, time.min, tzinfo=tz)
    delta = end_local - start_local
    previous_start = start_local - delta
    previous_end = start_local
    return Period(
        label=period,
        start_utc=_to_utc_naive(start_local),
        end_utc=_to_utc_naive(end_local),
        previous_start_utc=_to_utc_naive(previous_start),
        previous_end_utc=_to_utc_naive(previous_end),
        display_start=start,
        display_end=end - timedelta(days=1),
    )


def activity_query(cliente: Cliente, period: Period, args: dict[str, Any]) -> Select:
    query = (
        select(ActivityInteraction)
        .where(
            ActivityInteraction.cliente_id == cliente.id,
            ActivityInteraction.started_at >= period.start_utc,
            ActivityInteraction.started_at < period.end_utc,
        )
        .options(
            selectinload(ActivityInteraction.requested_service),
            selectinload(ActivityInteraction.appointment),
            selectinload(ActivityInteraction.events),
        )
    )

    channel = str(args.get("channel") or "").strip()
    if channel:
        query = query.where(ActivityInteraction.channel == normalize_channel(channel))
    outcome = str(args.get("outcome") or "").strip()
    if outcome:
        query = query.where(ActivityInteraction.outcome == outcome)
    service_id = str(args.get("service_id") or "").strip()
    if service_id.isdigit():
        query = query.where(ActivityInteraction.requested_service_id == int(service_id))
    search = str(args.get("q") or "").strip()
    if search:
        phone_digits = normalize_phone(search)
        conditions = [ActivityInteraction.customer_name.ilike(f"%{search}%")]
        if phone_digits:
            conditions.append(ActivityInteraction.customer_phone.ilike(f"%{phone_digits[-4:]}"))
        query = query.where(or_(*conditions))

    sort = str(args.get("sort") or "date_desc")
    if sort == "date_asc":
        query = query.order_by(ActivityInteraction.started_at.asc())
    elif sort == "duration_desc":
        query = query.order_by(ActivityInteraction.duration_seconds.desc().nullslast())
    elif sort == "duration_asc":
        query = query.order_by(ActivityInteraction.duration_seconds.asc().nullsfirst())
    else:
        query = query.order_by(ActivityInteraction.started_at.desc())
    return query


def load_activities(session: Session, cliente: Cliente, period: Period, args: dict[str, Any]) -> tuple[list[ActivityInteraction], int, int, int]:
    page = max(int(args.get("page") or 1), 1)
    per_page = min(max(int(args.get("per_page") or 25), 1), 100)
    base_query = activity_query(cliente, period, args)
    count_query = select(func.count()).select_from(base_query.order_by(None).subquery())
    total = session.scalar(count_query) or 0
    items = session.scalars(base_query.offset((page - 1) * per_page).limit(per_page)).all()
    return list(items), total, page, per_page


def dashboard_data(session: Session, cliente: Cliente, period: Period, args: dict[str, Any]) -> dict[str, Any]:
    current = _activities_for_range(session, cliente.id, period.start_utc, period.end_utc)
    previous = _activities_for_range(session, cliente.id, period.previous_start_utc, period.previous_end_utc)
    metrics = _metrics(current)
    previous_metrics = _metrics(previous)
    return {
        "metrics": _metric_cards(metrics, previous_metrics),
        "services": _service_stats(current),
        "demand": _demand_stats(current, cliente.timezone),
        "outcomes": _outcome_stats(current),
        "summary": deterministic_summary(metrics, current),
    }


def deterministic_summary(metrics: dict[str, Any], activities: list[ActivityInteraction]) -> str:
    if not activities:
        return "No hay actividad registrada para este periodo."
    return (
        f"En el periodo se registraron {metrics['total_calls']} interacciones, "
        f"{metrics['appointments_created']} citas creadas y una conversión de {metrics['conversion_rate']:.1f}%."
    )


def serialize_activity(activity: ActivityInteraction, timezone: str) -> dict[str, Any]:
    return {
        "id": activity.id,
        "started_at": format_local_datetime(activity.started_at, timezone),
        "customer_name": activity.customer_name or "Sin nombre",
        "customer_phone": mask_phone(activity.customer_phone),
        "channel": CHANNEL_LABELS.get(activity.channel, activity.channel),
        "service": activity.requested_service_name_snapshot or "Sin servicio",
        "outcome": OUTCOME_LABELS.get(activity.outcome, activity.outcome),
        "duration": format_duration(activity.duration_seconds),
        "appointment_id": activity.appointment_id,
        "status": STATUS_LABELS.get(activity.status, activity.status),
    }


def serialize_activity_detail(activity: ActivityInteraction, timezone: str) -> dict[str, Any]:
    payload = serialize_activity(activity, timezone)
    payload.update(
        {
            "ended_at": format_local_datetime(activity.ended_at, timezone) if activity.ended_at else "",
            "summary": activity.summary or "",
            "transcript": activity.transcript or "",
            "error_code": activity.error_code or "",
            "error_message": activity.error_message or "",
            "external_id": activity.external_id or "",
            "timeline": [
                {
                    "event_type": event.event_type,
                    "label": OUTCOME_LABELS.get(event.event_type, event.event_type.replace("_", " ")),
                    "occurred_at": format_local_datetime(event.occurred_at, timezone),
                }
                for event in sorted(activity.events, key=lambda item: item.occurred_at)
            ],
        }
    )
    return payload


def export_csv(activities: list[ActivityInteraction], timezone: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["fecha", "cliente", "telefono", "canal", "servicio", "resultado", "duracion", "cita", "estado"])
    for activity in activities:
        row = serialize_activity(activity, timezone)
        writer.writerow(
            [
                row["started_at"],
                row["customer_name"],
                row["customer_phone"],
                row["channel"],
                row["service"],
                row["outcome"],
                row["duration"],
                row["appointment_id"] or "",
                row["status"],
            ]
        )
    return output.getvalue()


def generate_pdf_report(cliente: Cliente, period: Period, data: dict[str, Any]) -> bytes:
    lines = [
        "KET - Reporte mensual de actividad",
        f"Negocio: {cliente.nombre}",
        f"Periodo: {period.display_start.isoformat()} a {period.display_end.isoformat()}",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "Resumen ejecutivo",
        data["summary"],
        "",
        "Metricas",
    ]
    for metric in data["metrics"]:
        lines.append(f"- {metric['label']}: {metric['value']} ({metric['delta_label']})")
    lines.append("")
    lines.append("Servicios mas solicitados")
    for service in data["services"]["rows"][:8]:
        lines.append(f"- {service['service']}: {service['requests']} solicitudes, {service['conversion']:.1f}% conversion")
    lines.append("")
    lines.append("Recomendaciones")
    lines.append("- Revisa los servicios con consultas sin disponibilidad.")
    lines.append("- Ajusta horarios si hay demanda recurrente fuera del horario comercial.")
    return _simple_pdf(lines)


def format_local_datetime(value: datetime, timezone: str) -> str:
    tz = _timezone(timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def mask_phone(value: str | None) -> str:
    digits = normalize_phone(value)
    if not digits:
        return "Sin teléfono"
    return f"***{digits[-4:]}"


def normalize_channel(channel: str | None) -> str:
    raw = (channel or "other").strip().lower()
    if raw in ("llamada", "call", "voice"):
        return "call"
    if raw == "vapi":
        return "vapi"
    if raw in ("whatsapp", "wa"):
        return "whatsapp"
    return raw or "other"


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "0:00"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _activities_for_range(session: Session, client_id: int, start: datetime, end: datetime) -> list[ActivityInteraction]:
    return list(
        session.scalars(
            select(ActivityInteraction)
            .where(
                ActivityInteraction.cliente_id == client_id,
                ActivityInteraction.started_at >= start,
                ActivityInteraction.started_at < end,
            )
            .options(selectinload(ActivityInteraction.events))
        )
    )


def _metrics(activities: list[ActivityInteraction]) -> dict[str, Any]:
    completed = [item for item in activities if item.status == "completed"]
    failed = [item for item in activities if item.status in ("failed", "abandoned") or item.outcome in ("failed", "abandoned")]
    created = [item for item in activities if item.outcome == "appointment_created"]
    total_duration = sum(item.duration_seconds or 0 for item in activities)
    completed_count = len(completed)
    return {
        "total_calls": len(activities),
        "completed_calls": completed_count,
        "failed_calls": len(failed),
        "total_minutes": round(total_duration / 60, 1),
        "average_duration": round((total_duration / completed_count) / 60, 1) if completed_count else 0,
        "unique_customers": len({normalize_phone(item.customer_phone) for item in activities if normalize_phone(item.customer_phone)}),
        "appointments_created": len(created),
        "appointments_cancelled": sum(1 for item in activities if item.outcome == "appointment_cancelled"),
        "appointments_rescheduled": sum(1 for item in activities if item.outcome == "appointment_rescheduled"),
        "conversion_rate": (len(created) / completed_count * 100) if completed_count else 0,
    }


def _metric_cards(current: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
    labels = [
        ("total_calls", "Total de llamadas"),
        ("completed_calls", "Llamadas completadas"),
        ("failed_calls", "Fallidas o abandonadas"),
        ("total_minutes", "Minutos atendidos"),
        ("average_duration", "Duración promedio"),
        ("unique_customers", "Clientes únicos"),
        ("appointments_created", "Citas creadas"),
        ("appointments_cancelled", "Citas canceladas"),
        ("appointments_rescheduled", "Citas reagendadas"),
        ("conversion_rate", "Conversión a cita"),
    ]
    cards = []
    for key, label in labels:
        current_value = current[key]
        previous_value = previous[key]
        delta = _delta_percent(current_value, previous_value)
        suffix = "%" if key == "conversion_rate" else ""
        cards.append(
            {
                "key": key,
                "label": label,
                "value": f"{current_value:.1f}{suffix}" if isinstance(current_value, float) else f"{current_value}{suffix}",
                "delta": delta,
                "delta_label": "Sin periodo anterior" if delta is None else f"{delta:+.1f}%",
            }
        )
    return cards


def _service_stats(activities: list[ActivityInteraction]) -> dict[str, Any]:
    grouped: dict[str, list[ActivityInteraction]] = defaultdict(list)
    no_availability = Counter()
    for item in activities:
        name = item.requested_service_name_snapshot or "Sin servicio"
        grouped[name].append(item)
        if item.outcome == "no_availability":
            no_availability[name] += 1

    total = sum(len(items) for items in grouped.values())
    rows = []
    for name, items in grouped.items():
        created = sum(1 for item in items if item.outcome == "appointment_created")
        cancelled = sum(1 for item in items if item.outcome == "appointment_cancelled")
        rescheduled = sum(1 for item in items if item.outcome == "appointment_rescheduled")
        rows.append(
            {
                "service": name,
                "requests": len(items),
                "percent": (len(items) / total * 100) if total else 0,
                "appointments": created,
                "conversion": (created / len(items) * 100) if items else 0,
                "cancelled": cancelled,
                "rescheduled": rescheduled,
                "no_availability": no_availability[name],
            }
        )
    rows.sort(key=lambda row: row["requests"], reverse=True)
    most_requested = rows[0]["service"] if rows else "Sin datos"
    highest_conversion = max(rows, key=lambda row: row["conversion"])["service"] if rows else "Sin datos"
    most_no_availability = max(rows, key=lambda row: row["no_availability"])["service"] if rows else "Sin datos"
    return {
        "rows": rows,
        "most_requested": most_requested,
        "highest_conversion": highest_conversion,
        "most_no_availability": most_no_availability,
    }


def _demand_stats(activities: list[ActivityInteraction], timezone: str) -> dict[str, Any]:
    by_day = Counter()
    appointments_by_day = Counter()
    by_hour = Counter()
    by_weekday = Counter()
    for item in activities:
        local = _as_local(item.started_at, timezone)
        day_key = local.date().isoformat()
        by_day[day_key] += 1
        by_hour[f"{local.hour:02d}:00"] += 1
        by_weekday[local.strftime("%A")] += 1
        if item.appointment_id or item.outcome == "appointment_created":
            appointments_by_day[day_key] += 1
    return {
        "calls_by_day": dict(sorted(by_day.items())),
        "appointments_by_day": dict(sorted(appointments_by_day.items())),
        "by_hour": dict(sorted(by_hour.items())),
        "top_weekday": by_weekday.most_common(1)[0][0] if by_weekday else "Sin datos",
        "top_hour": by_hour.most_common(1)[0][0] if by_hour else "Sin datos",
        "outside_business_hours": 0,
        "closed_business_calls": 0,
    }


def _outcome_stats(activities: list[ActivityInteraction]) -> list[dict[str, Any]]:
    counts = Counter(item.outcome for item in activities)
    return [
        {"outcome": outcome, "label": OUTCOME_LABELS.get(outcome, outcome), "count": count}
        for outcome, count in counts.most_common()
    ]


def _delta_percent(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100


def _metadata(activity: ActivityInteraction) -> dict[str, Any]:
    try:
        parsed = json.loads(activity.metadata_json or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _to_utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _timezone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _as_local(value: datetime, timezone: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_timezone(timezone))


def _parse_date_arg(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _simple_pdf(lines: list[str]) -> bytes:
    escaped_lines = [_pdf_escape(line) for line in lines]
    content_lines = ["BT", "/F1 12 Tf", "50 780 Td", "16 TL"]
    for line in escaped_lines:
        content_lines.append(f"({line}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]
    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(pdf.tell())
        pdf.write(f"{index} 0 obj\n".encode("ascii"))
        pdf.write(obj)
        pdf.write(b"\nendobj\n")
    xref = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return pdf.getvalue()


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
