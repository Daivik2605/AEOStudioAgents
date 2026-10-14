"""The client timeline and work log (PLAN.md section 10).

Both are built from the database every time and never edited by hand. The same database always
produces the same text (no "generated at" stamp), so the files can be compared with it.
"""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

from core import client_files

# Order used only to break a tie when two things happened at the same instant.
_KIND_ORDER = {"diagnosis": 0, "audit": 1, "evidence": 2, "work": 3, "event": 4, "status change": 5}


def _when(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def collect_timeline(conn, business_id: str) -> list[dict]:
    """Every diagnosis, audit run, evidence record, work entry, event and status change, oldest first.
    Each is {when, kind, label, text}; `label` is the checkpoint where there is one."""
    items: list[dict] = []
    with conn.cursor() as cur:
        cur.execute("SELECT id, checked_at, checkpoint, verdict, platform, jsonb_array_length(findings) "
                    "FROM diagnoses WHERE business_id = %s", (business_id,))
        for id_, when, checkpoint, verdict, platform, n in cur.fetchall():
            items.append({"id": str(id_), "when": when, "kind": "diagnosis", "label": checkpoint or "",
                          "text": f"Diagnosis: verdict {verdict}" + (f" on {platform}" if platform else "")
                                  + f", {n} finding(s)"})

        cur.execute("SELECT id, COALESCE(started_at, created_at), checkpoint, run_type, mode, status "
                    "FROM probe_runs WHERE business_id = %s", (business_id,))
        for id_, when, checkpoint, run_type, mode, status in cur.fetchall():
            items.append({"id": str(id_), "when": when, "kind": "audit", "label": checkpoint or "",
                          "text": f"Audit run ({run_type}, {mode}): {status}"})

        cur.execute("SELECT id, created_at, checkpoint, file_path, evidence_type, source, captured_on, period_start, "
                    "period_end, note, finding_code, client_visible, operator FROM evidence WHERE business_id = %s",
                    (business_id,))
        for (id_, when, checkpoint, path, etype, source, captured, p_start, p_end, note, finding, visible,
             operator) in cur.fetchall():
            bits = [b for b in [etype, f"from {source}" if source else None,
                                f"captured {captured}" if captured else None,
                                f"covers {p_start} to {p_end}" if p_start else None,
                                None if visible else "internal only", f"finding {finding}" if finding else None,
                                f"added by {operator}"] if b]
            items.append({"id": str(id_), "when": when, "kind": "evidence", "label": checkpoint,
                          "text": f"Evidence: {path} ({'; '.join(bits)})" + (f". {note}" if note else "")})

        cur.execute("SELECT id, created_at, entry_type, description, actor, details FROM client_journal "
                    "WHERE business_id = %s AND entry_type IN ('work', 'event', 'status_change')", (business_id,))
        for id_, when, entry_type, description, actor, details in cur.fetchall():
            details = details or {}
            kind = "status change" if entry_type == "status_change" else entry_type
            extra = ""
            if entry_type == "work":
                extra = (f" [{details['finding']}]" if details.get("finding") else "")
            items.append({"id": str(id_), "when": when, "kind": kind,
                          "label": (details.get("checkpoint") or "") if entry_type == "work" else "",
                          "text": f"{description}{extra} ({actor})" if entry_type != "status_change" else description})

    items.sort(key=lambda i: (i["when"], _KIND_ORDER[i["kind"]], i["id"]))
    return items


def collect_work(conn, business_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, created_at, actor, description, details FROM client_journal "
                    "WHERE business_id = %s AND entry_type = 'work' ORDER BY created_at, id", (business_id,))
        return [{"id": str(r[0]), "when": r[1], "operator": r[2], "text": r[3], "details": r[4] or {}}
                for r in cur.fetchall()]


def _title(business: dict) -> str:
    return business.get("name") or business["domain"]


def render_timeline(business: dict, items: list[dict]) -> str:
    lines = [f"# Timeline: {_title(business)}", "",
             "_Made from the database each time `aeo timeline` runs. Do not edit by hand._", ""]
    if not items:
        lines.append("Nothing recorded yet.")
    for i in items:
        label = f" · `{i['label']}`" if i["label"] else ""
        lines.append(f"- **{_when(i['when'])}** · {i['kind']}{label} — {i['text']}")
    return "\n".join(lines) + "\n"


def render_work_log(business: dict, entries: list[dict]) -> str:
    lines = [f"# Work log: {_title(business)}", "",
             "_Made from the database each time `aeo timeline` runs. Do not edit by hand. "
             "Add entries with `aeo log`._", ""]
    if not entries:
        lines.append("No work recorded yet.")
    for e in entries:
        d = e["details"]
        tags = [t for t in [f"`{d['checkpoint']}`" if d.get("checkpoint") else None,
                            f"closes `{d['finding']}`" if d.get("finding") else None,
                            f"file: {d['file']}" if d.get("file") else None] if t]
        lines.append(f"- **{_when(e['when'])}** · {e['operator']} — {e['text']}" + (f" ({'; '.join(tags)})" if tags else ""))
    return "\n".join(lines) + "\n"


def regenerate(conn, business: dict, clients_root: Path | None = None) -> tuple[list[dict], Path, Path]:
    """Rebuilds timeline.md and work_log.md. These two are the only files in clients/ that are
    overwritten on purpose: they are views of the database, not history."""
    items = collect_timeline(conn, business["id"])
    work = collect_work(conn, business["id"])
    folder = client_files.client_dir(business["domain"], clients_root)
    folder.mkdir(parents=True, exist_ok=True)
    timeline_path, work_path = folder / "timeline.md", folder / "work_log.md"
    timeline_path.write_text(render_timeline(business, items), encoding="utf-8")
    work_path.write_text(render_work_log(business, work), encoding="utf-8")
    return items, timeline_path, work_path
