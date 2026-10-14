"""`aeo truth draft`: write the questionnaire, pre-filled from the latest diagnosis (PLAN.md section 16)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core import client_files
from core.profiles import STORY_FIELDS, UNCONFIRMED, latest_profile
from truth import format as fmt
from truth.prefill import from_diagnosis


@dataclass
class DraftResult:
    path: Path
    diagnosis_used: str | None        # id of the diagnosis the pre-fills came from, None if there was none
    based_on_version: int | None
    from_site: int                    # facts pre-filled from the site's structured data (all unconfirmed)
    from_record: int                  # facts pre-filled from what was entered in `aeo add` (all unconfirmed)
    carried_forward: int              # facts taken over from the previous profile version, status kept


def latest_diagnosis(conn, business_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, checked_at, checkpoint, existing_jsonld, render, findings FROM diagnoses "
                    "WHERE business_id = %s ORDER BY checked_at DESC, created_at DESC LIMIT 1", (business_id,))
        r = cur.fetchone()
    return {"id": str(r[0]), "checked_at": r[1], "checkpoint": r[2], "existing_jsonld": r[3] or [],
            "render": r[4], "findings": r[5] or []} if r else None


def generate_draft(conn, business: dict, *, clients_root: Path | None = None, today: date | None = None) -> DraftResult:
    day = today or date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT name, domain, website_url FROM businesses WHERE id = %s", (business["id"],))
        name, domain, website_url = cur.fetchone()
    business = {"id": business["id"], "name": name, "domain": domain, "website_url": website_url}
    diagnosis = latest_diagnosis(conn, business["id"])
    previous = latest_profile(conn, business["id"])
    values, notes, site_keys = from_diagnosis(diagnosis, business)

    # A re-draft starts from what we already know, so confirmed facts are carried over (with who and when)
    # instead of being asked again, and an import of the new file does not read as "everything removed".
    known = {f["label"].casefold(): f for f in (previous["facts"] if previous else [])}
    used: set[str] = set()
    blocks: list[dict] = []
    from_site = from_record = carried = 0
    for slot in fmt.SLOTS:
        lines = [slot.help] if slot.help else []
        old = known.get(slot.label.casefold())
        if old:
            used.add(slot.label.casefold())
            carried += 1
            lines.append(f"Carried over from profile version {previous['version']}.")
            block = {"label": slot.label, "value": old["value"], "status": old["status"],
                     "confirmed_by": old.get("confirmed_by") or "", "confirmed_on": old.get("confirmed_on") or ""}
        else:
            value = values.get(slot.key, "")
            from_site += slot.key in site_keys
            from_record += bool(value) and slot.key not in site_keys
            lines += notes.get(slot.key, [])
            block = {"label": slot.label, "value": value, "status": UNCONFIRMED, "confirmed_by": "", "confirmed_on": ""}
        block["note"] = " ".join(lines)
        blocks.append(block)
    for label, old in known.items():                      # facts the client added last time under their own label
        if label not in used:
            carried += 1
            blocks.append({"label": old["label"], "value": old["value"], "status": old["status"],
                           "confirmed_by": old.get("confirmed_by") or "", "confirmed_on": old.get("confirmed_on") or "",
                           "note": f"Carried over from profile version {previous['version']}."})
    for _ in range(fmt.EXTRA_SLOTS):
        blocks.append({"label": "", "value": "", "status": UNCONFIRMED, "confirmed_by": "", "confirmed_on": "",
                       "note": "Extra fact. Write what it is in LABEL (for example 'Delivery time'), then the fact."})

    drafted_from = (f"diagnosis {diagnosis['id']} ({diagnosis['checked_at']:%Y-%m-%d}"
                    + (f", {diagnosis['checkpoint']}" if diagnosis["checkpoint"] else "") + ")"
                    if diagnosis else "no diagnosis yet")
    text = fmt.render_document(business=business, day=day, drafted_from=drafted_from,
                               based_on_version=previous["version"] if previous else None, facts=blocks,
                               story={k: (previous["story"].get(k) if previous else "") or "" for k in STORY_FIELDS})
    path = client_files.write_new(client_files.generated_dir(domain, clients_root),
                                  client_files.dated_name(day, "truth_draft", "md"), text.encode("utf-8"))
    return DraftResult(path=path, diagnosis_used=diagnosis["id"] if diagnosis else None,
                       based_on_version=previous["version"] if previous else None,
                       from_site=from_site, from_record=from_record, carried_forward=carried)
