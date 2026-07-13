from dishka import Provider, Scope, provide

from event_booker.adapters.scheduling_client import SchedulingClient
from event_booker.adapters.users_client import UsersClient
from event_booker.config import Settings, get_settings
from event_booker.interfaces.clients import ISchedulingClient, IUsersClient
from event_booker.services.guest_booking import GuestBookingService


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return get_settings()

    @provide(scope=Scope.APP)
    def provide_scheduling_client(self, settings: Settings) -> ISchedulingClient:
        return SchedulingClient(settings.event_scheduling_url, settings.scheduling_api_key)

    @provide(scope=Scope.APP)
    def provide_users_client(self, settings: Settings) -> IUsersClient:
        return UsersClient(settings.event_users_url, settings.event_users_token)

    @provide(scope=Scope.APP)
    def provide_guest_booking_service(self, scheduling: ISchedulingClient, users: IUsersClient) -> GuestBookingService:
        return GuestBookingService(scheduling, users)
