import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import BASE_DIR, Config


SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_DIR = BASE_DIR / "tokens"


class CalendarServiceError(Exception):
    pass


class CalendarService:
    def __init__(self, timezone: ZoneInfo, calendar_id: str, credentials_file: str, credentials_env_var: str | None = None):
        self.timezone = timezone
        self.calendar_id = calendar_id
        self.credentials_env_var = credentials_env_var
        credentials_path = Path(credentials_file)
        self.credentials_file = credentials_path if credentials_path.is_absolute() else BASE_DIR / credentials_path
        TOKEN_DIR.mkdir(exist_ok=True)
        self.token_file = TOKEN_DIR / f"{_safe_token_name(self.credentials_file)}.json"
        self._service = None

    def is_available(self, start, end, exclude_event_id: str | None = None) -> bool:
        try:
            events_result = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError as exc:
            raise CalendarServiceError("Google Calendar rechazó la consulta de disponibilidad.") from exc

        events = events_result.get("items", [])
        if exclude_event_id:
            events = [event for event in events if event.get("id") != exclude_event_id]
        return len(events) == 0

    def create_calendar_event(self, nombre: str, telefono: str, motivo: str, start, end) -> dict:
        event = {
            "summary": f"Cita: {motivo}",
            "description": f"Nombre: {nombre}\nTelefono: {telefono}\nMotivo: {motivo}",
            "start": {"dateTime": start.isoformat(), "timeZone": str(self.timezone)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(self.timezone)},
        }

        try:
            return (
                self.service.events()
                .insert(calendarId=self.calendar_id, body=event)
                .execute()
            )
        except HttpError as exc:
            raise CalendarServiceError("Google Calendar rechazó la creación del evento.") from exc

    def update_calendar_event(self, event_id: str, nombre: str, telefono: str, motivo: str, start, end) -> dict:
        event = {
            "summary": f"Cita: {motivo}",
            "description": f"Nombre: {nombre}\nTelefono: {telefono}\nMotivo: {motivo}",
            "start": {"dateTime": start.isoformat(), "timeZone": str(self.timezone)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(self.timezone)},
        }

        try:
            return (
                self.service.events()
                .update(calendarId=self.calendar_id, eventId=event_id, body=event)
                .execute()
            )
        except HttpError as exc:
            raise CalendarServiceError("Google Calendar rechazó la actualización del evento.") from exc

    def delete_calendar_event(self, event_id: str) -> None:
        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in (404, 410):
                return
            raise CalendarServiceError("Google Calendar rechazó la cancelación del evento.") from exc

    @property
    def service(self):
        if self._service is None:
            self._service = build("calendar", "v3", credentials=self._load_credentials())
        return self._service

    def _load_credentials(self):
        credential_data = self._load_credentials_from_env()
        if credential_data:
            return self._credentials_from_data(credential_data)

        if not self.credentials_file.exists():
            raise CalendarServiceError(
                "No existe el archivo de credenciales configurado para este cliente."
            )

        try:
            with self.credentials_file.open("r", encoding="utf-8") as file:
                credential_data = json.load(file)
        except json.JSONDecodeError as exc:
            raise CalendarServiceError("El archivo de credenciales no contiene JSON válido.") from exc

        return self._credentials_from_data(credential_data)

    def _load_credentials_from_env(self) -> dict | None:
        if not self.credentials_env_var:
            return None

        raw_credentials = os.getenv(self.credentials_env_var)
        if not raw_credentials:
            return None

        try:
            credential_data = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            raise CalendarServiceError("La variable de entorno de credenciales no contiene JSON válido.") from exc

        if not isinstance(credential_data, dict):
            raise CalendarServiceError("La variable de entorno de credenciales debe contener un objeto JSON.")

        return credential_data

    def _credentials_from_data(self, credential_data: dict):
        credential_type = credential_data.get("type")
        if credential_type == "service_account":
            return service_account.Credentials.from_service_account_info(
                credential_data,
                scopes=SCOPES,
            )

        if credential_type == "authorized_user":
            return UserCredentials.from_authorized_user_info(credential_data, SCOPES)

        return self._load_user_credentials()

    def _load_user_credentials(self):
        credentials = None
        if self.token_file.exists():
            credentials = UserCredentials.from_authorized_user_file(self.token_file, SCOPES)

        if credentials and credentials.valid:
            return credentials

        if credentials and credentials.expired and credentials.refresh_token:
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
            self.token_file.write_text(credentials.to_json(), encoding="utf-8")
            return credentials

        if not Config.GOOGLE_OAUTH_LOCAL_FLOW:
            raise CalendarServiceError(
                "No hay token OAuth válido para este cliente. Usa service account o autoriza el token localmente."
            )

        flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
        credentials = flow.run_local_server(port=0)
        self.token_file.write_text(credentials.to_json(), encoding="utf-8")
        return credentials


def _safe_token_name(credentials_file: Path) -> str:
    raw_name = str(credentials_file.relative_to(BASE_DIR)) if credentials_file.is_relative_to(BASE_DIR) else credentials_file.name
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_name).strip("_")
