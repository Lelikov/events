from collections.abc import AsyncGenerator

import structlog
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_scheduling.adapters.schedule_db import ScheduleDBAdapter
from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.config import Settings, get_settings
from event_scheduling.controllers.schedule import ScheduleController
from event_scheduling.interfaces.schedule import IScheduleController, IScheduleDBAdapter
from event_scheduling.interfaces.sql import ISqlExecutor


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
