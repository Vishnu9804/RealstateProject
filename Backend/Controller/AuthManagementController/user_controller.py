"""Employee accounts — admin only. Creating or changing a login also needs
owner verification; removing one never does, so access can always be revoked.
Only rows with role "employee" can ever be touched here."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from Model.AuthManagementModel.user_record import UserSummary
from Service.AuthManagementService import password_service, user_store
from Service.AuthManagementService.auth_dependencies import require_admin, require_owner_verification

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])


class CreateEmployeeRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class UpdateEmployeeRequest(BaseModel):
    username: Optional[str] = Field(default=None, max_length=64)
    password: Optional[str] = Field(default=None, max_length=256)


def _reject_invalid(username: str, password: Optional[str]) -> None:
    problem = user_store.username_problem(username)
    if problem is None and password is not None:
        problem = password_service.password_problem(password, username)
    if problem:
        raise HTTPException(status_code=400, detail=problem)


@router.get("", response_model=List[UserSummary])
def list_employees() -> List[UserSummary]:
    return [user_store.to_summary(user) for user in user_store.list_employees()]


@router.post("", response_model=UserSummary, status_code=201, dependencies=[Depends(require_owner_verification)])
def create_employee(body: CreateEmployeeRequest) -> UserSummary:
    username = user_store.normalize_username(body.username)
    _reject_invalid(username, body.password)
    try:
        return user_store.to_summary(user_store.create_employee(username, body.password))
    except user_store.UsernameTakenError:
        raise HTTPException(status_code=409, detail="That username is already taken.")


@router.patch("/{user_id}", response_model=UserSummary, dependencies=[Depends(require_owner_verification)])
def update_employee(user_id: str, body: UpdateEmployeeRequest) -> UserSummary:
    current = user_store.get_by_id(user_id)
    if current is None or current.role != "employee":
        raise HTTPException(status_code=404, detail="Employee not found.")
    username = user_store.normalize_username(body.username) if body.username is not None else None
    if username == current.username:
        username = None
    password = body.password or None
    if username is None and password is None:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    _reject_invalid(username or current.username, password)
    try:
        updated = user_store.update_employee(user_id, username, password)
    except user_store.UsernameTakenError:
        raise HTTPException(status_code=409, detail="That username is already taken.")
    if updated is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    return user_store.to_summary(updated)


@router.delete("/{user_id}", status_code=204)
def delete_employee(user_id: str) -> None:
    if not user_store.delete_employee(user_id):
        raise HTTPException(status_code=404, detail="Employee not found.")
