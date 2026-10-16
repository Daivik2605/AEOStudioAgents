"""Where a fix goes on the client's platform. All the knowledge is in recommend/platforms.yaml; this only looks it up."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

DATA_FILE = Path(__file__).parent / "platforms.yaml"
THINGS = ("robots", "llms", "jsonld", "content")

# What the result's "status" means to a reader.
CAN, GATED, CANNOT, BLOCKED_BY_PLAN, UNKNOWN = "can", "gated", "cannot", "blocked_by_plan", "unknown"


@lru_cache(maxsize=1)
def _data() -> dict:
    return yaml.safe_load(DATA_FILE.read_text())


def platform_label(platform: str | None) -> str:
    entry = _data()["platforms"].get(platform or "")
    return entry["label"] if entry else _data()["unknown_platform"]["label"]


def route(platform: str | None, plan: str | None, thing: str) -> dict:
    """Where `thing` ('robots', 'llms', 'jsonld' or 'content' (page text)) goes on this platform.

    Returns {status, where, alternative, by_hand, source}. `status` is can / gated / cannot / blocked_by_plan /
    unknown. `blocked_by_plan` means the platform can do it but not on the plan the client is on.
    """
    if thing not in THINGS:
        raise ValueError(f"unknown thing '{thing}'")
    data = _data()
    entry = data["platforms"].get(platform or "")
    source = data["source"]
    item = entry[thing] if entry else data["unknown_platform"][thing]
    level = item["level"]
    status = {"can": CAN, "gated": GATED, "cannot": CANNOT}.get(level, UNKNOWN)
    if status == GATED and plan and plan.lower() in [p.lower() for p in item.get("blocked_plans", [])]:
        status = BLOCKED_BY_PLAN
    return {"status": status, "where": item["where"], "alternative": item.get("alternative"),
            "by_hand": bool(item.get("by_hand", False)), "source": source}


def edge_route(cdn: str | None) -> dict:
    """Where to fix a bot being refused or challenged at the edge. `cdn` is 'cloudflare', 'wp_engine' or None."""
    edge = _data()["edge"]
    item = edge.get(cdn or "", edge["default"])
    status = {"can": CAN, "cannot": CANNOT}.get(item["level"], UNKNOWN)
    return {"status": status, "where": item["where"], "alternative": item.get("alternative"),
            "by_hand": False, "source": item["source"]}
