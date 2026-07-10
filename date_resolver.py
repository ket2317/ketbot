import re
import unicodedata
import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DATE_FORMAT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RELATIVE_DATES = {
    "hoy": 0,
    "manana": 1,
    "pasado manana": 2,
}
WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
MONTH_NAMES = {value: key for key, value in MONTHS.items() if key != "setiembre"}
AMBIGUOUS_RANGES = ("principios", "mediados", "finales")


@dataclass(frozen=True)
class DateResolution:
    success: bool
    date: date | None = None
    needs_clarification: bool = False
    resolved_month: int | None = None
    resolved_year: int | None = None
    message: str = ""
    interpreted_from: str = ""
    timezone: str | None = None
    resolution_rule: str = "unresolved"

    @property
    def display_date(self) -> str | None:
        return format_display_date(self.date) if self.date else None


def normalize_date_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_text = re.sub(r"[,.]", " ", ascii_text)
    return re.sub(r"\s+", " ", ascii_text).strip()


def resolve_date_context(date_text: str, today: date | None = None, timezone: str | None = None) -> DateResolution:
    current_date = today or date.today()
    normalized = normalize_date_text(date_text)
    if not normalized:
        return _unresolved(date_text, timezone)

    iso_date = _resolve_iso_date(normalized, current_date)
    if iso_date:
        return _resolved(iso_date, date_text, timezone, "iso_date")
    if DATE_FORMAT_RE.fullmatch(normalized):
        return _clarify_past(current_date, date_text, timezone)

    if normalized in RELATIVE_DATES:
        resolved = current_date + timedelta(days=RELATIVE_DATES[normalized])
        return _resolved(resolved, date_text, timezone, f"relative_{normalized.replace(' ', '_')}")

    if normalized == "proxima semana":
        resolved = current_date + timedelta(days=(7 - current_date.weekday()))
        return _resolved(resolved, date_text, timezone, "next_week")

    range_resolution = _resolve_ambiguous_range(normalized, current_date, date_text, timezone)
    if range_resolution:
        return range_resolution

    month_only = _resolve_month_only(normalized, current_date, date_text, timezone)
    if month_only:
        return month_only

    weekday_resolution = _resolve_weekday_text(normalized, current_date, date_text, timezone)
    if weekday_resolution:
        return weekday_resolution

    day_this_month = _resolve_day_with_this_month(normalized, current_date, date_text, timezone)
    if day_this_month:
        return day_this_month

    month_match = re.fullmatch(r"(\d{1,2})(?:\s+de)?\s+([a-z]+)", normalized)
    if month_match:
        day = int(month_match.group(1))
        month = MONTHS.get(month_match.group(2))
        if month is None:
            return _unresolved(date_text, timezone)
        return _resolve_month_day_context(current_date, month, day, date_text, timezone, "day_month_no_year")

    month_day_match = re.fullmatch(r"([a-z]+)\s+(\d{1,2})", normalized)
    if month_day_match:
        month = MONTHS.get(month_day_match.group(1))
        day = int(month_day_match.group(2))
        if month is not None:
            return _resolve_month_day_context(current_date, month, day, date_text, timezone, "month_day_no_year")

    numeric_match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", normalized)
    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        return _resolve_month_day_context(current_date, month, day, date_text, timezone, "numeric_day_month_no_year")

    day_only_match = re.fullmatch(r"(?:el\s+)?(?:dia\s+)?(\d{1,2})", normalized)
    if day_only_match:
        return _resolve_day_only_context(current_date, int(day_only_match.group(1)), date_text, timezone)

    return _unresolved(date_text, timezone)


def resolve_date_text(date_text: str, today: date | None = None) -> date | None:
    resolution = resolve_date_context(date_text, today)
    return resolution.date if resolution.success else None


def client_today(timezone: str) -> date:
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return date.today()


def _resolve_iso_date(value: str, current_date: date) -> date | None:
    if not DATE_FORMAT_RE.fullmatch(value):
        return None
    try:
        resolved = date.fromisoformat(value)
    except ValueError:
        return None
    return resolved if resolved >= current_date else None


def _extract_weekday(value: str) -> int | None:
    weekday_text = value
    for prefix in ("este ", "esta ", "proximo ", "proxima "):
        if weekday_text.startswith(prefix):
            weekday_text = weekday_text.removeprefix(prefix)
            break
    return WEEKDAYS.get(weekday_text)


def _next_weekday(current_date: date, weekday: int) -> date:
    days_ahead = (weekday - current_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return current_date + timedelta(days=days_ahead)


def _next_month_day(current_date: date, month: int, day: int) -> date | None:
    for year in (current_date.year, current_date.year + 1):
        try:
            resolved = date(year, month, day)
        except ValueError:
            return None
        if resolved >= current_date:
            return resolved
    return None


def _resolved(resolved_date: date, original: str, timezone: str | None, rule: str) -> DateResolution:
    return DateResolution(
        success=True,
        date=resolved_date,
        resolved_month=resolved_date.month,
        resolved_year=resolved_date.year,
        message="Fecha resuelta correctamente.",
        interpreted_from=original,
        timezone=timezone,
        resolution_rule=rule,
    )


def _clarification(
    original: str,
    timezone: str | None,
    month: int,
    year: int,
    message: str,
    rule: str,
) -> DateResolution:
    return DateResolution(
        success=False,
        needs_clarification=True,
        resolved_month=month,
        resolved_year=year,
        message=message,
        interpreted_from=original,
        timezone=timezone,
        resolution_rule=rule,
    )


def _unresolved(original: str, timezone: str | None) -> DateResolution:
    return DateResolution(
        success=False,
        message="No pude determinar la fecha. Pide al cliente una fecha exacta.",
        interpreted_from=original,
        timezone=timezone,
        resolution_rule="unresolved",
    )


def _clarify_past(current_date: date, original: str, timezone: str | None) -> DateResolution:
    return _clarification(
        original,
        timezone,
        current_date.month,
        current_date.year,
        "Esa fecha ya pasó. Pide al cliente una fecha futura.",
        "past_date_rejected",
    )


def _month_label(month: int) -> str:
    return MONTH_NAMES.get(month, f"mes {month}")


def _next_month_year(current_date: date) -> tuple[int, int]:
    if current_date.month == 12:
        return 1, current_date.year + 1
    return current_date.month + 1, current_date.year


def _valid_day(year: int, month: int, day: int) -> bool:
    if month < 1 or month > 12:
        return False
    return 1 <= day <= calendar.monthrange(year, month)[1]


def _resolve_month_day_context(
    current_date: date,
    month: int,
    day: int,
    original: str,
    timezone: str | None,
    rule: str,
) -> DateResolution:
    if month < 1 or month > 12:
        return _unresolved(original, timezone)

    for year in (current_date.year, current_date.year + 1):
        if not _valid_day(year, month, day):
            return _clarification(
                original,
                timezone,
                month,
                year,
                f"Ese día no existe en {_month_label(month)}. Pide otro día.",
                f"{rule}_invalid_day",
            )
        resolved = date(year, month, day)
        if resolved >= current_date:
            return _resolved(resolved, original, timezone, rule)

    return _clarify_past(current_date, original, timezone)


def _resolve_day_only_context(
    current_date: date,
    day: int,
    original: str,
    timezone: str | None,
) -> DateResolution:
    month, year = current_date.month, current_date.year
    if _valid_day(year, month, day):
        resolved = date(year, month, day)
        if resolved >= current_date:
            return _resolved(resolved, original, timezone, "day_only_current_month")

    month, year = _next_month_year(current_date)
    if not _valid_day(year, month, day):
        return _clarification(
            original,
            timezone,
            month,
            year,
            f"Ese día no existe en {_month_label(month)}. Pide otro día.",
            "day_only_invalid_next_month",
        )
    return _resolved(date(year, month, day), original, timezone, "day_only_next_month")


def _resolve_day_with_this_month(
    normalized: str,
    current_date: date,
    original: str,
    timezone: str | None,
) -> DateResolution | None:
    match = re.fullmatch(r"(?:el\s+)?(?:dia\s+)?(\d{1,2})\s+de\s+este\s+mes", normalized)
    if not match:
        return None
    day = int(match.group(1))
    month, year = current_date.month, current_date.year
    if not _valid_day(year, month, day):
        return _clarification(
            original,
            timezone,
            month,
            year,
            f"Ese día no existe en {_month_label(month)}. Pide otro día.",
            "day_this_month_invalid",
        )
    resolved = date(year, month, day)
    if resolved < current_date:
        return _clarification(
            original,
            timezone,
            month,
            year,
            f"El {day} de {_month_label(month)} ya pasó. Pide un día futuro.",
            "day_this_month_past",
        )
    return _resolved(resolved, original, timezone, "day_this_month")


def _resolve_month_only(
    normalized: str,
    current_date: date,
    original: str,
    timezone: str | None,
) -> DateResolution | None:
    if normalized in ("este mes", "en este mes"):
        return _clarification(
            original,
            timezone,
            current_date.month,
            current_date.year,
            f"¿Qué día de {_month_label(current_date.month)} prefieres?",
            "current_month_needs_day",
        )
    if normalized in ("proximo mes", "el proximo mes", "mes que viene", "el mes que viene"):
        month, year = _next_month_year(current_date)
        return _clarification(
            original,
            timezone,
            month,
            year,
            f"¿Qué día de {_month_label(month)} prefieres?",
            "next_month_needs_day",
        )

    month = MONTHS.get(normalized)
    if month:
        year = current_date.year if month >= current_date.month else current_date.year + 1
        return _clarification(
            original,
            timezone,
            month,
            year,
            f"¿Qué día de {_month_label(month)} prefieres?",
            "month_name_needs_day",
        )
    return None


def _resolve_ambiguous_range(
    normalized: str,
    current_date: date,
    original: str,
    timezone: str | None,
) -> DateResolution | None:
    if not normalized.startswith(AMBIGUOUS_RANGES):
        return None

    month, year = current_date.month, current_date.year
    if "proximo mes" in normalized or "mes que viene" in normalized:
        month, year = _next_month_year(current_date)
    elif "este mes" in normalized:
        month, year = current_date.month, current_date.year
    else:
        for month_name, month_number in MONTHS.items():
            if month_name in normalized:
                month = month_number
                year = current_date.year if month >= current_date.month else current_date.year + 1
                break

    return _clarification(
        original,
        timezone,
        month,
        year,
        f"¿Qué día exacto de {_month_label(month)} prefieres?",
        "month_range_needs_day",
    )


def _resolve_weekday_text(
    normalized: str,
    current_date: date,
    original: str,
    timezone: str | None,
) -> DateResolution | None:
    weekday = _extract_weekday(normalized)
    if weekday is None:
        return None

    if normalized.startswith(("proximo ", "proxima ")):
        days_since_monday = current_date.weekday()
        start_next_week = current_date - timedelta(days=days_since_monday) + timedelta(days=7)
        resolved = start_next_week + timedelta(days=weekday)
        return _resolved(resolved, original, timezone, "weekday_next_week")

    days_ahead = (weekday - current_date.weekday()) % 7
    resolved = current_date + timedelta(days=days_ahead)
    if normalized.startswith(("este ", "esta ")) and resolved < current_date:
        resolved += timedelta(days=7)
    return _resolved(resolved, original, timezone, "weekday_current_or_next")


def format_display_date(value: date | None) -> str | None:
    if value is None:
        return None
    weekday_names = {
        0: "lunes",
        1: "martes",
        2: "miércoles",
        3: "jueves",
        4: "viernes",
        5: "sábado",
        6: "domingo",
    }
    month_names = {
        1: "enero",
        2: "febrero",
        3: "marzo",
        4: "abril",
        5: "mayo",
        6: "junio",
        7: "julio",
        8: "agosto",
        9: "septiembre",
        10: "octubre",
        11: "noviembre",
        12: "diciembre",
    }
    return f"{weekday_names[value.weekday()]} {value.day} de {month_names[value.month]} de {value.year}"
