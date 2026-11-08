"""Order desk retailer directory: names and ids for the desk's pickers.

Read-only and order-desk only. No prices, credit or contact details: a
retailer's credit is shown on its orders, where the desk needs it.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.dependencies import DbSession, DeskUser
from app.schemas.auth import ErrorResponse
from app.services import desk_order_service

router = APIRouter(prefix="/desk/retailers", tags=["order desk retailers"])


class RetailerDirectoryEntry(BaseModel):
    id: UUID
    business_name: str
    is_active: bool


class RetailerDirectory(BaseModel):
    items: list[RetailerDirectoryEntry]


@router.get(
    "",
    response_model=RetailerDirectory,
    summary="Retailers, by business name",
    responses={
        401: {"model": ErrorResponse, "description": "Not signed in."},
        403: {"model": ErrorResponse, "description": "Not an order desk account."},
    },
)
def list_retailers(
    desk_user: DeskUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[
        int, Query(ge=1, le=desk_order_service.RETAILER_DIRECTORY_LIMIT)
    ] = 20,
) -> RetailerDirectory:
    retailers = desk_order_service.list_retailers(
        db, search=(search or "").strip() or None, limit=limit
    )
    return RetailerDirectory(
        items=[
            RetailerDirectoryEntry(
                id=retailer.id,
                business_name=retailer.business_name,
                is_active=retailer.is_active,
            )
            for retailer in retailers
        ]
    )
