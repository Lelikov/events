from collections.abc import AsyncGenerator

import structlog
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_scheduling.adapters.event_type_db import EventTypeDBAdapter
from event_scheduling.adapters.schedule_db import ScheduleDBAdapter
from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.busy_source import BookingBusyTimesSource
from event_scheduling.booking.interfaces import IBookingReadAdapter, IBookingService, IBookingWriteAdapter
from event_scheduling.booking.read_adapter import BookingReadAdapter
from event_scheduling.booking.service import BookingService
from event_scheduling.booking.write_adapter import BookingWriteAdapter
from event_scheduling.config import Settings, get_settings
from event_scheduling.controllers.event_type import EventTypeController
from event_scheduling.controllers.schedule import ScheduleController
from event_scheduling.interfaces.busy_times import BusyTimesSource
from event_scheduling.interfaces.event_type import IEventTypeController, IEventTypeDBAdapter
from event_scheduling.interfaces.schedule import IScheduleController, IScheduleDBAdapter
from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.publishing.interfaces import IOutboxWriter
from event_scheduling.publishing.outbox_writer import OutboxWriter
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

    @provide(scope=Scope.APP)
    def provide_clock(self) -> Clock:
        return SystemClock()

    @provide(scope=Scope.REQUEST)
    def provide_busy_source(self, sql: ISqlExecutor) -> BusyTimesSource:
        return BookingBusyTimesSource(sql)

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
    def provide_booking_service(
        self,
        slots_read: ISlotsReadAdapter,
        read: IBookingReadAdapter,
        write: IBookingWriteAdapter,
        busy: BusyTimesSource,
        clock: Clock,
        outbox: IOutboxWriter,
    ) -> IBookingService:
        return BookingService(slots_read, read, write, busy, clock, outbox)
