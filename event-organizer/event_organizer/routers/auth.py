from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter

from event_organizer.schemas.auth import LoginRequest, LoginResponse
from event_organizer.services.login_service import LoginService

auth_router = APIRouter(tags=["auth"], route_class=DishkaRoute)


@auth_router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, service: FromDishka[LoginService]) -> LoginResponse:
    token = await service.login(str(body.email), body.password)
    return LoginResponse(access_token=token)
