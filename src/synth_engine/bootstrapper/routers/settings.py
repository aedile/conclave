"""FastAPI router for Settings endpoints.

Implements CRUD for key-value application :class:`Setting` resources.
Settings use ``key`` as the primary key; PUT performs an upsert.

All 404 responses use RFC 7807 Problem Details format.

Authentication: All endpoints require a valid JWT Bearer token via the
:func:`~synth_engine.bootstrapper.dependencies.auth.get_current_operator`
dependency (ADV-021).

Scope-based authorization (T47.3):
- GET endpoints are NOT scope-gated — any authenticated operator can read
  settings.  This ensures read-only observability is broadly available.
- PUT (upsert) and DELETE require the ``settings:write`` scope.  These
  mutations change application behavior and must be restricted to operators
  that hold the write permission.

Task: P5-T5.1 — Task Orchestration API Core
Task: T47.3 — Scope-based auth for settings write endpoints
Task: T62.1 — Wrap Database Commits in Exception Handlers
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.auth import get_current_operator, require_scope
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.bootstrapper.schemas.settings import (
    Setting,
    SettingListResponse,
    SettingResponse,
    SettingUpsertRequest,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get(
    "",
    summary="List settings",
    description="Return all application settings. All authenticated operators can read settings.",
    responses=COMMON_ERROR_RESPONSES,
    response_model=SettingListResponse,
)
def list_settings(
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> SettingListResponse:
    """List all application settings.

    No scope restriction — any authenticated operator may read settings.

    Args:
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`SettingListResponse` with up to 100 stored key-value pairs.
    """
    settings = session.exec(select(Setting).limit(100)).all()
    return SettingListResponse(
        items=[SettingResponse.model_validate(s) for s in settings],
    )


@router.put(
    "/{key}",
    summary="Upsert a setting",
    description="Create or update an application setting. Requires the settings:write scope.",
    responses=COMMON_ERROR_RESPONSES,
    response_model=SettingResponse,
)
def upsert_setting(
    key: str,
    body: SettingUpsertRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(require_scope("settings:write"))],
) -> SettingResponse | JSONResponse:
    """Create or update a setting by key.

    Upsert semantics: if ``key`` exists, update its value; otherwise create
    a new entry.

    Requires scope: ``settings:write`` (T47.3).

    Args:
        key: The setting key (URL path parameter).
        body: Request body containing the new value.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim, verified to hold
            the ``settings:write`` scope (injected by FastAPI DI).

    Returns:
        The upserted :class:`SettingResponse`, or RFC 7807 500 on database error.
    """
    setting = session.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=body.value)
    else:
        setting.value = body.value
    session.add(setting)
    try:
        session.commit()
        session.refresh(setting)
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "upsert_setting: SQLAlchemyError for key=%s operator=%s",
            key,
            current_operator,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Database operation failed. Please retry.",
            },
        )
    return SettingResponse.model_validate(setting)


@router.get("/{key}", response_model=SettingResponse)
def get_setting(
    key: str,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> SettingResponse | JSONResponse:
    """Get a setting by key.

    No scope restriction — any authenticated operator may read settings.

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


@router.delete(
    "/{key}",
    summary="Delete a setting",
    description="Delete an application setting. Requires the settings:write scope.",
    responses=COMMON_ERROR_RESPONSES,
    status_code=204,
)
def delete_setting(
    key: str,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(require_scope("settings:write"))],
) -> Response:
    """Delete a setting by key.

    Requires scope: ``settings:write`` (T47.3).

    Args:
        key: The setting key to delete.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim, verified to hold
            the ``settings:write`` scope (injected by FastAPI DI).

    Returns:
        HTTP 204 No Content on success, RFC 7807 404 on not found, or
        RFC 7807 500 on database errors.
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
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "delete_setting: SQLAlchemyError for key=%s operator=%s",
            key,
            current_operator,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Database operation failed. Please retry.",
            },
        )
    return Response(status_code=204)
