"""Shared configuration checks for mower write controls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import CONF_ENABLE_CONTROLS, ROCKMOW_Z1_MODEL



def write_controls_enabled(
    options: Mapping[str, Any] | None,
    model: str | None,
) -> bool:
    """Return whether write controls are explicitly enabled for this model."""

    return model == ROCKMOW_Z1_MODEL and bool((options or {}).get(CONF_ENABLE_CONTROLS, False))
