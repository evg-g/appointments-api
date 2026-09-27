"""User management: create (platform admin) and read (self or platform admin)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from appointments_api.api.deps import CurrentUser, UserRepoDep, require_roles
from appointments_api.api.errors import ConflictError, ForbiddenError, NotFoundError
from appointments_api.api.schemas import UserCreate, UserOut
from appointments_api.enums import UserRole
from appointments_api.models import User
from appointments_api.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(UserRole.PLATFORM_ADMIN))],
)
async def create_user(body: UserCreate, users: UserRepoDep) -> User:
    if await users.get_by_email(body.email) is not None:
        raise ConflictError("A user with that email already exists.")
    user = User(
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        hashed_password=hash_password(body.password),
        is_active=True,
    )
    return await users.add(user)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, current: CurrentUser, users: UserRepoDep) -> User:
    if current.role is not UserRole.PLATFORM_ADMIN and current.id != user_id:
        raise ForbiddenError("You may only read your own user record.")
    user = await users.get(user_id)
    if user is None:
        raise NotFoundError("No such user.")
    return user
