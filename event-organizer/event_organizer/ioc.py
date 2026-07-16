from collections.abc import AsyncGenerator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_organizer.adapters.interfaces import ISchedulingClient, IUsersClient
from event_organizer.adapters.scheduling_client import SchedulingClient
from event_organizer.adapters.sql import SqlExecutor
from event_organizer.adapters.users_client import UsersClient
from event_organizer.auth.password import PasswordService
from event_organizer.config import Settings, get_settings
from event_organizer.credentials.adapter import CredentialAdapter
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.interfaces.sql import ISqlExecutor
from event_organizer.services.login_service import LoginService
from event_organizer.services.provisioning_service import ProvisioningService


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return get_settings()

    @provide(scope=Scope.APP)
    async def provide_db_engine(self, settings: Settings) -> AsyncGenerator[AsyncEngine]:
        engine = create_async_engine(str(settings.postgres_dsn), pool_pre_ping=True)
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

    @provide(scope=Scope.APP)
    def provide_password_service(self) -> PasswordService:
        return PasswordService()

    @provide(scope=Scope.REQUEST)
    def provide_credential_adapter(self, sql: ISqlExecutor) -> ICredentialAdapter:
        return CredentialAdapter(sql)

    @provide(scope=Scope.APP)
    def provide_scheduling_client(self, settings: Settings) -> ISchedulingClient:
        return SchedulingClient(settings.event_scheduling_url, settings.scheduling_api_key)

    @provide(scope=Scope.APP)
    def provide_users_client(self, settings: Settings) -> IUsersClient:
        return UsersClient(settings.event_users_url, settings.event_users_token)

    @provide(scope=Scope.REQUEST)
    def provide_login_service(
        self, credentials: ICredentialAdapter, passwords: PasswordService, settings: Settings
    ) -> LoginService:
        return LoginService(credentials, passwords, settings)

    @provide(scope=Scope.REQUEST)
    def provide_provisioning_service(
        self, credentials: ICredentialAdapter, passwords: PasswordService, users: IUsersClient
    ) -> ProvisioningService:
        return ProvisioningService(credentials, passwords, users)
