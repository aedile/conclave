"""FastAPI router for Settings endpoints.

Implements CRUD for key-value application :class:`Setting` resources.
Settings use ``key`` as the primary key; PUT performs an upsert.

All 404 responses use RFC 7807 Problem Details format.

Authentication: All endpoints require a valid JWT Bearer token via the
:func:`~synth_engine.bootstrapper.dependencies.auth.get_current_operator`
dependency (ADV-021).

Task: P5-T5.1 — Task Orchestration API Core
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.settings import (
    Setting,
    SettingListResponse,
    SettingResponse,
    SettingUpsertRequest,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingListResponse)
def list_settings(
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> SettingListResponse:
    """List all application settings.

    Args:
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`SettingListResponse` with all stored key-value pairs.
    """
    settings = session.exec(select(Setting)).all()
    return SettingListResponse(
        items=[SettingResponse.model_validate(s) for s in settings],
    )


@router.put("/{key}", response_model=SettingResponse)
def upsert_setting(
    key: str,
    body: SettingUpsertRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> SettingResponse:
    """Create or update a setting by key.

    Upsert semantics: if ``key`` exists, update its value; otherwise create
    a new entry.

    Args:
        key: The setting key (URL path parameter).
        body: Request body containing the new value.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        The upserted :class:`SettingResponse`.
    """
    setting = session.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=body.value)
    else:
        setting.value = body.value
    session.add(setting)
    session.commit()
    session.refresh(setting)
    return SettingResponse.model_validate(setting)


@router.get("/{key}", response_model=SettingResponse)
def get_setting(
    key: str,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> SettingResponse | JSONResponse:
    """Get a setting by key.

    Args:
        key: The setting key to look up.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`SettingResponse` on success, or RFC 7807 404 on not found.
    """
    setting = session.get(Setting, key)
    if setting is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Setting with key='{key}' not found.",
            ),
        )
    return SettingResponse.model_validate(setting)


@router.delete("/{key}", status_code=204)
def delete_setting(
    key: str,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> Response:
    """Delete a setting by key.

    Args:
        key: The setting key to delete.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        HTTP 204 No Content on success, or RFC 7807 404 on not found.
    """
    setting = session.get(Setting, key)
    if setting is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Setting with key='{key}' not found.",
            ),
        )
    session.delete(setting)
    session.commit()
    return Response(status_code=204)
