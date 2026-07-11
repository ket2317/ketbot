from datetime import date, datetime, time
from decimal import Decimal
from typing import List

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint
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
    email: Mapped[str] = mapped_column(String(160), nullable=True)
    direccion: Mapped[str] = mapped_column(String(255), nullable=True)
    descripcion: Mapped[str] = mapped_column(Text, nullable=True)
    mensaje_bienvenida: Mapped[str] = mapped_column(Text, nullable=True)
    informacion_general: Mapped[str] = mapped_column(Text, nullable=True)
    instrucciones_asistente: Mapped[str] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=True)
    duracion_cita_minutos: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    servicios: Mapped[List["Servicio"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")
    citas: Mapped[List["Cita"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")
    horarios: Mapped[List["ClientBusinessHour"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")
    actividades: Mapped[List["ActivityInteraction"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")
    whatsapp_accounts: Mapped[List["WhatsAppAccount"]] = relationship(
        back_populates="cliente",
        cascade="all, delete-orphan",
    )


class WhatsAppAccount(Base):
    __tablename__ = "whatsapp_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False, index=True)
    phone_number_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    verify_token: Mapped[str] = mapped_column(String(255), nullable=True, unique=True, index=True)
    access_token_env_var: Mapped[str] = mapped_column(String(255), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    cliente: Mapped[Cliente] = relationship(back_populates="whatsapp_accounts")


class Servicio(Base):
    __tablename__ = "servicios"
    __table_args__ = (UniqueConstraint("cliente_id", "nombre", name="uq_servicios_cliente_nombre"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=True)
    precio: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=True)
    duracion_minutos: Mapped[int] = mapped_column(Integer, nullable=False)
    requiere_cita: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disponible_por_llamada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disponible_por_whatsapp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notas_internas: Mapped[str] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    cliente: Mapped[Cliente] = relationship(back_populates="servicios")
    citas: Mapped[List["Cita"]] = relationship(back_populates="servicio")
    disponibilidad: Mapped[List["ServiceAvailability"]] = relationship(
        back_populates="servicio",
        cascade="all, delete-orphan",
    )


class ClientBusinessHour(Base):
    __tablename__ = "client_business_hours"
    __table_args__ = (UniqueConstraint("cliente_id", "weekday", name="uq_client_business_hours_cliente_weekday"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    breaks_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    cliente: Mapped[Cliente] = relationship(back_populates="horarios")


class ServiceAvailability(Base):
    __tablename__ = "service_availability"
    __table_args__ = (UniqueConstraint("service_id", "weekday", name="uq_service_availability_service_weekday"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("servicios.id"), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=True)
    end_time: Mapped[time] = mapped_column(Time, nullable=True)
    use_business_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    servicio: Mapped[Servicio] = relationship(back_populates="disponibilidad")


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


class ActivityInteraction(Base):
    __tablename__ = "activity_interactions"
    __table_args__ = (
        UniqueConstraint("cliente_id", "channel", "external_id", name="uq_activity_cliente_channel_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(160), nullable=True)
    customer_phone: Mapped[str] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="completed", index=True)
    outcome: Mapped[str] = mapped_column(String(80), nullable=False, default="other", index=True)
    requested_service_id: Mapped[int] = mapped_column(ForeignKey("servicios.id"), nullable=True, index=True)
    requested_service_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("citas.id"), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    transcript: Mapped[str] = mapped_column(Text, nullable=True)
    error_code: Mapped[str] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    cliente: Mapped[Cliente] = relationship(back_populates="actividades")
    requested_service: Mapped[Servicio] = relationship()
    appointment: Mapped[Cita] = relationship()
    events: Mapped[List["ActivityEvent"]] = relationship(back_populates="activity", cascade="all, delete-orphan")


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (
        UniqueConstraint("activity_id", "event_type", "external_event_id", name="uq_activity_event_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activity_interactions.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    activity: Mapped[ActivityInteraction] = relationship(back_populates="events")
