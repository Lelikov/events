"""ORM models for event-scheduling — used by Alembic only.

All runtime queries use raw SQL via SqlExecutor; these classes exist solely so
Alembic can autogenerate / compare migrations via Base.metadata.
"""

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

from event_scheduling.db.base import Base


def _uuid_pk() -> MappedColumn:
    """Return a fresh UUID PK column with a server-side gen_random_uuid() default.

    Returns a fresh MappedColumn each time so the same Column object is never
    assigned to more than one Table (SQLAlchemy raises ArgumentError otherwise).
    """
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


class Schedule(Base):
    __tablename__ = "schedule"

    id: Mapped[str] = _uuid_pk()
    owner_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    time_zone: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (UniqueConstraint("owner_user_id", name="uq_schedule_owner"),)


class WeeklyHour(Base):
    __tablename__ = "weekly_hours"

    id: Mapped[str] = _uuid_pk()
    schedule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 1 AND 7", name="ck_weekly_hours_dow"),
        CheckConstraint("end_time > start_time", name="ck_weekly_hours_range"),
    )


class DateOverride(Base):
    __tablename__ = "date_override"

    id: Mapped[str] = _uuid_pk()
    schedule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time)",
            name="ck_date_override_range",
        ),
    )


class TravelSchedule(Base):
    __tablename__ = "travel_schedule"

    id: Mapped[str] = _uuid_pk()
    schedule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False
    )
    time_zone: Mapped[str] = mapped_column(Text, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    prev_time_zone: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventType(Base):
    __tablename__ = "event_type"

    id: Mapped[str] = _uuid_pk()
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    scheduling_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'round_robin'"))
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_booking_notice_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (UniqueConstraint("slug", name="uq_event_type_slug"),)


class Host(Base):
    __tablename__ = "host"

    event_type_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_type.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="RESTRICT"), nullable=False
    )


class BookingLimit(Base):
    __tablename__ = "booking_limit"

    id: Mapped[str] = _uuid_pk()
    event_type_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False
    )
    limit_type: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("value > 0", name="ck_booking_limit_value"),
        UniqueConstraint("event_type_id", "limit_type", "period", name="uq_booking_limit"),
    )


class BookingField(Base):
    __tablename__ = "booking_field"

    id: Mapped[str] = _uuid_pk()
    event_type_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False
    )
    field_key: Mapped[str] = mapped_column(Text, nullable=False)
    field_type: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    placeholder: Mapped[str | None] = mapped_column(Text, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    options: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        UniqueConstraint("event_type_id", "field_key", name="uq_booking_field_key"),
        CheckConstraint(
            "field_type IN ('text','textarea','select','radio','checkbox','boolean')", name="ck_booking_field_type"
        ),
        Index("ix_booking_field_event_type", "event_type_id", "position"),
    )


class Booking(Base):
    """Booking write-side table.

    NOTE: the `EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&)
    WHERE (status = 'confirmed')` constraint (ex_booking_no_overlap) is NOT expressible in
    SQLAlchemy's ORM __table_args__ cleanly — it is created via raw DDL in the alembic
    migration (0002_booking.py) instead. This ORM class is alembic-autogenerate-only, so
    omitting it here is acceptable; the migration is the source of truth for it.
    """

    __tablename__ = "booking"

    id: Mapped[str] = _uuid_pk()
    event_type_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_type.id", ondelete="RESTRICT"), nullable=False
    )
    host_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'confirmed'"))
    attendee_time_zone: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    field_answers: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_booking_range"),
        CheckConstraint("status IN ('confirmed','cancelled')", name="ck_booking_status"),
        Index("ix_booking_host", "host_user_id", "status", "start_time"),
        Index("ix_booking_event_type", "event_type_id", "status", "start_time"),
        Index("ix_booking_client", "client_user_id"),
    )


class BookingChangeLog(Base):
    __tablename__ = "booking_change_log"

    id: Mapped[str] = _uuid_pk()
    booking_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)  # no FK: survives all
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    from_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    from_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    to_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    to_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor_source: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (CheckConstraint("kind IN ('created','rescheduled','cancelled')", name="ck_booking_log_kind"),)


class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[str] = _uuid_pk()
    event_ce_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    booking_uid: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending','sent','failed')", name="ck_outbox_status"),
        CheckConstraint(
            "event_type IN ('booking.created','booking.rescheduled','booking.cancelled')", name="ck_outbox_type"
        ),
        Index("ix_outbox_dispatch", "status", "next_attempt_at"),
    )


class ScheduleChangeLog(Base):
    __tablename__ = "schedule_change_log"

    id: Mapped[str] = _uuid_pk()
    owner_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    schedule_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)  # no FK: audit survives delete
    actor_source: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
