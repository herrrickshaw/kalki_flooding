"""Constants and credential parsing shared by collector.py and request_access.py."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

CATALOGUE = "https://cos.iudx.org.in/iudx/cat/v1"
AAA = "https://authorization.iudx.org.in/auth/v1"
CATALOGUE_PAGE_SIZE = 500  # max rows per catalogue search page
HTTP_TIMEOUT_S = 30


def parse_env(text: str) -> Dict[str, str]:
    """Parse KEY=VALUE lines (``#`` lines and lines without ``=`` are ignored)."""
    env: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def read_credentials(env_file: Path) -> Tuple[str, str]:
    """Return (IUDX_CLIENT_ID, IUDX_CLIENT_SECRET) from ``env_file``.

    Raises FileNotFoundError if the file is absent and KeyError if a key is missing.
    """
    env = parse_env(env_file.read_text())
    return env["IUDX_CLIENT_ID"], env["IUDX_CLIENT_SECRET"]
