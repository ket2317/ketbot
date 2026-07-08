import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent


def _database_url() -> str:
    url = os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'app.db'}"
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SQLALCHEMY_DATABASE_URI: str = _database_url()
    FLASK_HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "5000"))
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    GOOGLE_OAUTH_LOCAL_FLOW: bool = os.getenv("GOOGLE_OAUTH_LOCAL_FLOW", "false").lower() == "true"
    DEFAULT_APPOINTMENT_MINUTES: int = int(os.getenv("APPOINTMENT_MINUTES", "60"))

    RPM_ASSISTANT_ID: str = os.getenv("RPM_ASSISTANT_ID", "rpm-automotive")
    RPM_CALENDAR_ID: str = os.getenv("RPM_CALENDAR_ID") or os.getenv("GOOGLE_CALENDAR_ID", "primary")
    RPM_CREDENTIALS_FILE: str = os.getenv(
        "RPM_CREDENTIALS_FILE",
        os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/rpm_automotive.json"),
    )
    RPM_CREDENTIALS_ENV_VAR: str = os.getenv("RPM_CREDENTIALS_ENV_VAR", "GOOGLE_CREDENTIALS_RPM_AUTOMOTIVE_JSON")
    RPM_TIMEZONE: str = os.getenv("RPM_TIMEZONE", os.getenv("TIMEZONE", "America/Mexico_City"))

    UNAS_ASSISTANT_ID: str = os.getenv("UNAS_ASSISTANT_ID", "unas-la-comer")
    UNAS_CALENDAR_ID: str = os.getenv("UNAS_CALENDAR_ID", "primary")
    UNAS_CREDENTIALS_FILE: str = os.getenv("UNAS_CREDENTIALS_FILE", "credentials/unas_la_comer.json")
    UNAS_CREDENTIALS_ENV_VAR: str = os.getenv("UNAS_CREDENTIALS_ENV_VAR", "GOOGLE_CREDENTIALS_UNAS_LA_COMER_JSON")
    UNAS_TIMEZONE: str = os.getenv("UNAS_TIMEZONE", "America/Mexico_City")
