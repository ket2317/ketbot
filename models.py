from datetime import date, time
from decimal import Decimal
from typing import List

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    assistant_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    calendar_id: Mapped[str] = mapped_column(String(255), nullable=False)
    credentials_file: Mapped[str] = mapped_column(String(255), nullable=False)
    credentials_env_var: Mapped[str] = mapped_column(String(255), nullable=True)
    horario_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    horario_fin: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    telefono: Mapped[str] = mapped_column(String(40), nullable=True)
    direccion: Mapped[str] = mapped_column(String(255), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    servicios: Mapped[List["Servicio"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")
    citas: Mapped[List["Cita"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")


class Servicio(Base):
    __tablename__ = "servicios"
    __table_args__ = (UniqueConstraint("cliente_id", "nombre", name="uq_servicios_cliente_nombre"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    precio: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=True)
    duracion_minutos: Mapped[int] = mapped_column(Integer, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    cliente: Mapped[Cliente] = relationship(back_populates="servicios")
    citas: Mapped[List["Cita"]] = relationship(back_populates="servicio")


class Cita(Base):
    __tablename__ = "citas"
    __table_args__ = (UniqueConstraint("cliente_id", "fecha", "hora", name="uq_citas_cliente_fecha_hora"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False, index=True)
    nombre_cliente: Mapped[str] = mapped_column(String(160), nullable=False)
    telefono: Mapped[str] = mapped_column(String(40), nullable=False)
    servicio_id: Mapped[int] = mapped_column(ForeignKey("servicios.id"), nullable=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    hora: Mapped[time] = mapped_column(Time, nullable=False)
    google_event_id: Mapped[str] = mapped_column(String(255), nullable=True)
    estado: Mapped[str] = mapped_column(String(40), nullable=False, default="agendada")

    cliente: Mapped[Cliente] = relationship(back_populates="citas")
    servicio: Mapped[Servicio] = relationship(back_populates="citas")
