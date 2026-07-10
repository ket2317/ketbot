import re
import unicodedata
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


def normalize_date_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_text = re.sub(r"[,.]", " ", ascii_text)
    return re.sub(r"\s+", " ", ascii_text).strip()


def resolve_date_text(date_text: str, today: date | None = None) -> date | None:
    current_date = today or date.today()
    normalized = normalize_date_text(date_text)

    iso_date = _resolve_iso_date(normalized, current_date)
    if iso_date:
        return iso_date

    if normalized in RELATIVE_DATES:
        return current_date + timedelta(days=RELATIVE_DATES[normalized])

    if normalized == "proxima semana":
        return current_date + timedelta(days=(7 - current_date.weekday()))

    weekday = _extract_weekday(normalized)
    if weekday is not None:
        return _next_weekday(current_date, weekday)

    month_match = re.fullmatch(r"(\d{1,2})(?:\s+de)?\s+([a-z]+)", normalized)
    if month_match:
        day = int(month_match.group(1))
        month = MONTHS.get(month_match.group(2))
        if month is None:
            return None
        return _next_month_day(current_date, month, day)

    numeric_match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", normalized)
    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        return _next_month_day(current_date, month, day)

    return None


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
