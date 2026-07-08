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


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("clientes"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("clientes")}
    if "credentials_env_var" not in existing_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE clientes ADD COLUMN credentials_env_var VARCHAR(255)"))


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
