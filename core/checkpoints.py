"""The four checkpoint labels (PLAN.md section 10). Same for every client; anything else is refused.

The database enforces the same list (migration 017). Checking here first gives a clear message
instead of a raw Postgres error, and does it before any slow work (like fetching a website) starts.
"""

from __future__ import annotations

CHECKPOINTS = ("before", "after_site", "after_aeo", "check")

# The two labels used before v4.7. Named in the error so nobody has to guess what replaced them.
RENAMED = {"old_site": "before", "new_site_no_aeo": "after_site"}


class CheckpointError(ValueError):
    """The label is not one of the four."""


def check_checkpoint(value: str | None, *, required: bool = False) -> str | None:
    """Returns the label if it is valid. None is allowed unless `required` (a diagnosis of a bare URL has none)."""
    if value is None:
        if required:
            raise CheckpointError(f"a checkpoint is required. Valid labels: {', '.join(CHECKPOINTS)}")
        return None
    if value in CHECKPOINTS:
        return value
    hint = f" ('{value}' is now called '{RENAMED[value]}')" if value in RENAMED else ""
    raise CheckpointError(f"'{value}' is not a checkpoint label{hint}. Valid labels: {', '.join(CHECKPOINTS)}")
