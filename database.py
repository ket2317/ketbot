from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import Config


class Base(DeclarativeBase):
    pass


engine = create_engine(
    Config.SQLALCHEMY_DATABASE_URI,
    connect_args={"check_same_thread": False} if Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def init_db() -> None:
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_citas_cliente_fecha_hora "
                "ON citas (cliente_id, fecha, hora)"
            )
        )
        if inspect(connection).has_table("whatsapp_accounts"):
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_whatsapp_accounts_phone_number_id "
                    "ON whatsapp_accounts (phone_number_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_whatsapp_accounts_verify_token "
                    "ON whatsapp_accounts (verify_token)"
                )
            )


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("clientes"):
        return

    _add_columns(
        "clientes",
        {
            "credentials_env_var": "VARCHAR(255)",
            "email": "VARCHAR(160)",
            "descripcion": "TEXT",
            "mensaje_bienvenida": "TEXT",
            "informacion_general": "TEXT",
            "instrucciones_asistente": "TEXT",
            "duracion_cita_minutos": "INTEGER NOT NULL DEFAULT 60",
        },
    )
    if inspector.has_table("servicios"):
        _add_columns(
            "servicios",
            {
                "descripcion": "TEXT",
                "requiere_cita": "BOOLEAN NOT NULL DEFAULT TRUE",
                "disponible_por_llamada": "BOOLEAN NOT NULL DEFAULT TRUE",
                "disponible_por_whatsapp": "BOOLEAN NOT NULL DEFAULT TRUE",
                "notas_internas": "TEXT",
            },
        )
    if inspector.has_table("whatsapp_accounts"):
        _add_columns(
            "whatsapp_accounts",
            {
                "access_token_env_var": "VARCHAR(255)",
                "access_token": "TEXT",
                "activo": "BOOLEAN NOT NULL DEFAULT TRUE",
                "created_at": "DATETIME",
                "updated_at": "DATETIME",
            },
        )


def _add_columns(table_name: str, column_sql: dict[str, str]) -> None:
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    statements = [
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        for column_name, definition in column_sql.items()
        if column_name not in existing_columns
    ]
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
