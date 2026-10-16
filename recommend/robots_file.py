"""Builds the robots.txt we recommend, starting from the live file (PLAN.md step 19).

Rules this file keeps:
  * Every existing line stays. We add lines. The only time a line is changed is when a rule inside a bot's
    OWN group stops that bot from fetching the site and adding an Allow line did not fix it. That line is
    commented out (not deleted) and listed under `changes`, so a person sees exactly what was touched.
  * Search bots (the "retrieval" bots in diagnose/crawler.py) must be allowed. The tool checks this with
    protego, the same RFC 9309 parser the diagnosis uses, not by reading the text itself.
  * Training bots follow `training`: "keep" (touch nothing, the default), "allow", or "block".
  * A Sitemap line is added if the file has none and we found a sitemap.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from protego import Protego

from diagnose.crawler import BOTS

TRAINING_MODES = ("keep", "allow", "block")
ADDED = "# added by AEO Studio"
REMOVED = "# removed by AEO Studio (was): "

_AGENT_RE = re.compile(r"^\s*user-agent\s*:\s*(.*?)\s*$", re.I)
_RULE_RE = re.compile(r"^\s*(allow|disallow)\s*:\s*(.*?)\s*$", re.I)
_SITEMAP_RE = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.I)


@dataclass
class RobotsBuild:
    text: str
    live_used: bool                       # False when we started from nothing
    changes: list[dict] = field(default_factory=list)   # [{bot, kind, detail}]
    sitemap_added: bool = False
    diff: str = ""
    notes: list[str] = field(default_factory=list)
    final_access: dict[str, bool] = field(default_factory=dict)   # bot token -> may fetch every checked page

    @property
    def changed(self) -> bool:
        return bool(self.changes) or self.sitemap_added or not self.live_used


def _groups(lines: list[str]) -> list[dict]:
    """[{agents: [lowercase tokens], first: line index, last: line index of its final rule or agent line}]"""
    groups: list[dict] = []
    current = None
    for i, line in enumerate(lines):
        agent = _AGENT_RE.match(line)
        if agent:
            if current is None or current["has_rules"]:
                current = {"agents": [], "first": i, "last": i, "has_rules": False}
                groups.append(current)
            current["agents"].append(agent.group(1).lower())
            current["last"] = i
        elif _RULE_RE.match(line) and current is not None:
            current["has_rules"] = True
            current["last"] = i
        elif _SITEMAP_RE.match(line):
            current = None          # a Sitemap line ends the group in practice and is not part of it
    return groups


def _may_fetch_all(lines: list[str], token: str, urls: list[str]) -> bool:
    parser = Protego.parse("\n".join(lines) + "\n")
    return all(parser.can_fetch(u, token) for u in urls)


def _own_group(lines: list[str], token: str) -> dict | None:
    return next((g for g in _groups(lines) if token.lower() in g["agents"]), None)


def _insert_after(lines: list[str], index: int, new: list[str]) -> None:
    lines[index + 1:index + 1] = new


def _comment_out(lines: list[str], group: dict, kind: str) -> list[str]:
    """Comments out the group's rules of this kind ('allow' or 'disallow'). Returns the lines it touched."""
    touched = []
    for i in range(group["first"], group["last"] + 1):
        m = _RULE_RE.match(lines[i])
        if m and m.group(1).lower() == kind:
            touched.append(lines[i].strip())
            lines[i] = REMOVED + lines[i].strip()
    return touched


def build_robots(live: str | None, *, check_urls: list[str], training: str = "keep",
                 sitemap_url: str | None = None) -> RobotsBuild:
    """`live` is the file as served (None if there is no usable one). `check_urls` are the pages a bot must be
    able to fetch: the home page first, then the main pages from the sitemap."""
    if training not in TRAINING_MODES:
        raise ValueError(f"--training-bots must be one of: {', '.join(TRAINING_MODES)}")
    if not check_urls:
        raise ValueError("robots.txt needs at least one page to check")

    live_used = live is not None and live.strip() != ""
    original = live.splitlines() if live_used else []
    lines = list(original)
    build = RobotsBuild(text="", live_used=live_used)
    if not live_used:
        lines = ["User-agent: *", "Allow: /"]

    for bot in BOTS:
        want_allowed = True if bot.kind == "retrieval" else {"allow": True, "block": False}.get(training)
        if want_allowed is None:        # training bots with "keep": no change about them at all
            continue
        if want_allowed:
            _make_allowed(lines, bot.token, check_urls, build)
        else:
            _make_blocked(lines, bot.token, check_urls[0], build)

    has_sitemap = any(_SITEMAP_RE.match(l) for l in lines)
    if sitemap_url and not has_sitemap:
        lines += ["", ADDED, f"Sitemap: {sitemap_url}"]
        build.sitemap_added = True

    build.text = "\n".join(lines).rstrip("\n") + "\n"
    after = Protego.parse(build.text)
    build.final_access = {b.token: all(after.can_fetch(u, b.token) for u in check_urls) for b in BOTS}
    build.diff = "".join(difflib.unified_diff(
        [l + "\n" for l in original], build.text.splitlines(keepends=True),
        fromfile="live robots.txt" if live_used else "live robots.txt (none)", tofile="generated robots.txt"))
    return build


def _make_allowed(lines: list[str], token: str, urls: list[str], build: RobotsBuild) -> None:
    if _may_fetch_all(lines, token, urls):
        return
    group = _own_group(lines, token)
    if group is None:
        # Blocked by the "*" group (or nothing names this bot): give the bot its own group. That leaves every
        # existing line exactly as it was.
        lines += ["", ADDED, f"User-agent: {token}", "Allow: /"]
        build.changes.append({"bot": token, "kind": "added_group",
                              "detail": f"added a group that allows {token}; the existing rules are untouched"})
        return
    # The bot has its own group and it blocks something. Try the gentlest fix first: add `Allow: /`.
    _insert_after(lines, group["last"], [ADDED, "Allow: /"])
    if _may_fetch_all(lines, token, urls):
        build.changes.append({"bot": token, "kind": "added_allow",
                              "detail": f"added `Allow: /` to the existing {token} group; its Disallow lines are kept (longer paths still win)"})
        return
    # Still blocked (for example `Disallow: /*`): comment out that group's Disallow lines, and say so.
    group = _own_group(lines, token)
    touched = _comment_out(lines, group, "disallow")
    build.changes.append({"bot": token, "kind": "removed_rule",
                          "detail": f"commented out {', '.join('`' + t + '`' for t in touched)} in the {token} group (it stopped {token} reading the site)"})
    if not _may_fetch_all(lines, token, urls):
        build.notes.append(f"{token} is still blocked after the edits; check the file by hand.")


def _make_blocked(lines: list[str], token: str, home: str, build: RobotsBuild) -> None:
    if not _may_fetch_all(lines, token, [home]):
        return
    group = _own_group(lines, token)
    if group is None:
        lines += ["", ADDED, f"User-agent: {token}", "Disallow: /"]
        build.changes.append({"bot": token, "kind": "added_group",
                              "detail": f"added a group that blocks {token} (training bot, as requested)"})
        return
    touched = _comment_out(lines, group, "allow")
    group = _own_group(lines, token)
    _insert_after(lines, group["last"], [ADDED, "Disallow: /"])
    detail = f"added `Disallow: /` to the existing {token} group (training bot, as requested)"
    if touched:
        detail += f"; commented out {', '.join('`' + t + '`' for t in touched)}"
    build.changes.append({"bot": token, "kind": "added_disallow", "detail": detail})
