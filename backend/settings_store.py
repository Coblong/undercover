"""The platform's settings: kept in the database, changed by a senior handler."""

from typing import Any

from sqlalchemy import Connection, insert, select, update

from agent import settings as agent_settings
from backend import tables as t
from backend.db import load, now, transaction
from backend.models import (
    AllowedModel,
    Handler,
    SettingChange,
    SettingsValues,
    SettingsView,
)

KEYS = (
    "approval_limit",
    "objectives",
    "noise",
    "tolerance",
    "spending_limit",
    "agents_at_once",
)


def seed_values() -> SettingsValues:
    """The starting settings, from company.json and improvement.json."""
    improvement = load("improvement.json")
    return SettingsValues.model_validate(
        {"approval_limit": load("company.json")["payout_approval_limit"], **improvement}
    )


def seed(conn: Connection) -> None:
    """Store the starting settings. Nobody changed them, so the changes say so."""
    values = seed_values().model_dump()
    conn.execute(insert(t.settings), [{"key": k, "value": values[k]} for k in KEYS])
    conn.execute(
        insert(t.setting_changes),
        [
            {"key": k, "value": values[k], "changed_by": None, "changed_at": now()}
            for k in KEYS
        ],
    )


def current(conn: Connection) -> SettingsValues:
    """Return the settings in force."""
    rows = conn.execute(select(t.settings.c.key, t.settings.c.value))
    return SettingsValues.model_validate({row.key: row.value for row in rows})


def approval_limit(conn: Connection) -> float:
    """Return the payout above which a handler must sign off."""
    return current(conn).approval_limit


def allowed_models() -> list[AllowedModel]:
    """Return the models the claim agent may use, with their efforts and prices.

    With MODEL_NAME set, that one model at reasoning effort `default` and a price of 0.
    """
    named = agent_settings.model_name()
    if named:
        return [
            AllowedModel(
                model=named,
                reasoning_efforts=["default"],
                input_price=0,
                output_price=0,
            )
        ]
    return [AllowedModel.model_validate(m) for m in load("models.json")]


def view(conn: Connection) -> SettingsView:
    """Return the settings, who last changed each and when, and the models allowed."""
    latest: dict[str, SettingChange] = {}
    rows = conn.execute(
        select(
            t.setting_changes.c.key,
            t.handlers.c.name,
            t.setting_changes.c.changed_at,
        )
        .select_from(
            t.setting_changes.outerjoin(
                t.handlers, t.handlers.c.handler_id == t.setting_changes.c.changed_by
            )
        )
        .order_by(t.setting_changes.c.id)
    )
    for row in rows:
        latest[row.key] = SettingChange(
            key=row.key, changed_by=row.name, changed_at=row.changed_at
        )
    return SettingsView(
        values=current(conn),
        changes=[latest[k] for k in KEYS if k in latest],
        models=allowed_models(),
    )


def update_settings(
    conn: Connection, handler: Handler, body: SettingsValues
) -> SettingsView:
    """Change the settings, recording who changed each and when."""
    wanted: dict[str, Any] = body.model_dump()
    with transaction(conn):
        before = current(conn).model_dump()
        for key in KEYS:
            if wanted[key] == before[key]:
                continue
            conn.execute(
                update(t.settings)
                .where(t.settings.c.key == key)
                .values(value=wanted[key])
            )
            conn.execute(
                insert(t.setting_changes).values(
                    key=key,
                    value=wanted[key],
                    changed_by=handler.handler_id,
                    changed_at=now(),
                )
            )
    return view(conn)
