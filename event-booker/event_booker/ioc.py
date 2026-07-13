from dishka import Provider, Scope, provide

from event_booker.config import Settings, get_settings


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return get_settings()
