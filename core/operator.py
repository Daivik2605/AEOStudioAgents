"""Who is doing the work. AEO_OPERATOR in .env, else the name of the person logged in to this Mac."""

from __future__ import annotations

import getpass
import os

from core.db import ENV_FILE, _load_env_file


def operator_name() -> str:
    _load_env_file(ENV_FILE)
    return (os.environ.get("AEO_OPERATOR") or "").strip() or getpass.getuser()
