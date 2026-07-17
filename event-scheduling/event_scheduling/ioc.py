from collections.abc import AsyncGenerator

import structlog
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_scheduling.adapters.event_type_db import EventTypeDBAdapter
from event_scheduling.adapters.schedule_db import ScheduleDBAdapter
from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.busy_source import BookingBusyTimesSource
from event_scheduling.booking.detail_service import BookingDetailService
from event_scheduling.booking.interfaces import (
    IBookingDetailService,
    IBookingReadAdapter,
    IBookingService,
    IBookingWriteAdapter,
)
from event_scheduling.booking.read_adapter import BookingReadAdapter
from event_scheduling.booking.service import BookingService
from event_scheduling.booking.write_adapter import BookingWriteAdapter
from event_scheduling.booking_fields.adapter import BookingFieldAdapter
from event_scheduling.booking_fields.controller import BookingFieldController
from event_scheduling.booking_fields.interfaces import IBookingFieldAdapter, IBookingFieldController
from event_scheduling.calendar.busy_source import ExternalCalendarBusyTimesSource
from event_scheduling.calendar.composite_busy import CompositeBusyTimesSource
from event_scheduling.calendar.ical_client import ICalClient
from event_scheduling.calendar.ical_parser import ICalParser
from event_scheduling.calendar.interfaces import ICalendarReadAdapter, ICalendarWriteAdapter, IICalClient, IICalParser
from event_scheduling.calendar.read_adapter import CalendarReadAdapter
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
from event_scheduling.config import Settings, get_settings
from event_scheduling.controllers.event_type import EventTypeController
from event_scheduling.controllers.schedule import ScheduleController
from event_scheduling.interfaces.busy_times import BusyTimesSource
from event_scheduling.interfaces.event_type import IEventTypeController, IEventTypeDBAdapter
from event_scheduling.interfaces.schedule import IScheduleController, IScheduleDBAdapter
from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.publishing.interfaces import IOutboxWriter, IReceiverClient, IUsersClient
from event_scheduling.publishing.outbox_writer import OutboxWriter
from event_scheduling.publishing.receiver_client import ReceiverClient
from event_scheduling.publishing.users_client import UsersClient
from event_scheduling.reminders.interfaces import IReminderReadAdapter, IReminderWriteAdapter
from event_scheduling.reminders.read_adapter import ReminderReadAdapter
from event_scheduling.reminders.write_adapter import ReminderWriteAdapter
from event_scheduling.slots.interfaces import Clock, ISlotService, ISlotsReadAdapter
from event_scheduling.slots.read_adapter import SlotsReadAdapter
from event_scheduling.slots.service import SlotService, SystemClock


logger = structlog.get_logger(__name__)


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        settings = get_settings()
        logger.info("Settings initialized", debug=settings.debug, log_level=settings.log_level)
        return settings

    @provide(scope=Scope.APP)
    async def provide_db_engine(self, settings: Settings) -> AsyncGenerator[AsyncEngine]:
        engine = create_async_engine(str(settings.postgres_dsn), pool_size=10, max_overflow=20, pool_pre_ping=True)
        try:
            yield engine
        finally:
            await engine.dispose()

    @provide(scope=Scope.APP)
    def provide_sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    @provide(scope=Scope.REQUEST)
    async def provide_session(self, sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @provide(scope=Scope.REQUEST)
    def provide_sql_executor(self, session: AsyncSession) -> ISqlExecutor:
        return SqlExecutor(session)

    @provide(scope=Scope.REQUEST)
    def provide_schedule_db(self, sql: ISqlExecutor) -> IScheduleDBAdapter:
        return ScheduleDBAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_schedule_controller(self, db: IScheduleDBAdapter) -> IScheduleController:
        return ScheduleController(db)

    @provide(scope=Scope.REQUEST)
    def provide_event_type_db(self, sql: ISqlExecutor) -> IEventTypeDBAdapter:
        return EventTypeDBAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_event_type_controller(self, db: IEventTypeDBAdapter) -> IEventTypeController:
        return EventTypeController(db)

    @provide(scope=Scope.REQUEST)
    def provide_booking_field_adapter(self, sql: ISqlExecutor) -> IBookingFieldAdapter:
        return BookingFieldAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_booking_field_controller(self, adapter: IBookingFieldAdapter) -> IBookingFieldController:
        return BookingFieldController(adapter)

    @provide(scope=Scope.APP)
    def provide_clock(self) -> Clock:
        return SystemClock()

    @provide(scope=Scope.REQUEST)
    def provide_busy_source(self, sql: ISqlExecutor) -> BusyTimesSource:
        return CompositeBusyTimesSource(BookingBusyTimesSource(sql), ExternalCalendarBusyTimesSource(sql))

    @provide(scope=Scope.REQUEST)
    def provide_slots_read_adapter(self, sql: ISqlExecutor) -> ISlotsReadAdapter:
        return SlotsReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_slot_service(
        self, read_adapter: ISlotsReadAdapter, busy_source: BusyTimesSource, clock: Clock
    ) -> ISlotService:
        return SlotService(read_adapter, busy_source, clock)

    @provide(scope=Scope.REQUEST)
    def provide_booking_read(self, sql: ISqlExecutor) -> IBookingReadAdapter:
        return BookingReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_booking_write(self, sql: ISqlExecutor) -> IBookingWriteAdapter:
        return BookingWriteAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_outbox_writer(self, sql: ISqlExecutor) -> IOutboxWriter:
        return OutboxWriter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_reminder_read(self, sql: ISqlExecutor) -> IReminderReadAdapter:
        return ReminderReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_reminder_write(self, sql: ISqlExecutor) -> IReminderWriteAdapter:
        return ReminderWriteAdapter(sql)

    @provide(scope=Scope.APP)
    def provide_receiver_client(self, settings: Settings) -> IReceiverClient:
        return ReceiverClient(settings.event_receiver_url, settings.booking_api_key)

    @provide(scope=Scope.APP)
    def provide_users_client(self, settings: Settings) -> IUsersClient:
        return UsersClient(settings.event_users_url, settings.event_users_token)

    @provide(scope=Scope.REQUEST)
    def provide_booking_service(
        self,
        slots_read: ISlotsReadAdapter,
        read: IBookingReadAdapter,
        write: IBookingWriteAdapter,
        busy: BusyTimesSource,
        clock: Clock,
        outbox: IOutboxWriter,
        fields: IBookingFieldAdapter,
    ) -> IBookingService:
        return BookingService(slots_read, read, write, busy, clock, outbox, fields)

    @provide(scope=Scope.REQUEST)
    def provide_booking_detail_service(self, read: IBookingReadAdapter, users: IUsersClient) -> IBookingDetailService:
        return BookingDetailService(read, users)

    @provide(scope=Scope.REQUEST)
    def provide_calendar_read(self, sql: ISqlExecutor) -> ICalendarReadAdapter:
        return CalendarReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_calendar_write(self, sql: ISqlExecutor) -> ICalendarWriteAdapter:
        return CalendarWriteAdapter(sql)

    @provide(scope=Scope.APP)
    def provide_ical_client(self, settings: Settings) -> IICalClient:
        return ICalClient(settings.calendar_fetch_timeout_seconds)

    @provide(scope=Scope.APP)
    def provide_ical_parser(self) -> IICalParser:
        return ICalParser()
