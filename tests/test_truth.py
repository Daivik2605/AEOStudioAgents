"""The truth document: draft, import, versions, and claim checking against confirmed facts only (PLAN.md section 16)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb
from typer.testing import CliRunner

from aeo import cli
from audit import cost_gate, store
from audit.fill_in import generate_fill_in, import_fill_in
from audit.manual import run_manual_audit
from conftest import FakeAnthropicClient
from core import client_files
from core.db import get_connection
from core.profiles import confirmed_facts, latest_profile
from tests.test_audit import known_as_acme_refill, Operator, answer, biz, claims_reply, confirm_facts, extraction, qfile, query  # noqa: F401
from tests.test_fill_in import HEADER, all_done, done, fill, generate  # noqa: F401
from truth import draft as truth_draft, format as fmt
from truth.importer import (TruthImportAborted, TruthImportError, TruthImportRefused, diff_versions, import_truth)
from truth.prefill import from_diagnosis

TODAY = date(2026, 10, 14)
LD = json.dumps({
    "@context": "https://schema.org", "@type": "LocalBusiness", "name": "11297775 Canada Inc",
    "telephone": "(514) 555-0100", "url": "https://acme.example", "email": "hi@acme.example",
    "address": {"@type": "PostalAddress", "streetAddress": "1 Main St", "addressLocality": "Montreal",
                "addressRegion": "QC", "postalCode": "H1A 1A1", "addressCountry": "CA"},
    "openingHoursSpecification": [{"dayOfWeek": ["Monday", "Tuesday"], "opens": "09:00", "closes": "17:00"}],
    "sameAs": ["https://facebook.com/acme", "https://instagram.com/acme"],
    "areaServed": ["Montreal", "Laval"], "foundingDate": "2019-05-01", "priceRange": "$$"})


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: True)


@pytest.fixture
def no_tty(monkeypatch):
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: False)


def add_diagnosis(conn, biz, *, jsonld=(LD,), findings=(), render=None, checked="2026-10-10"):
    return str(conn.execute(
        "INSERT INTO diagnoses (business_id, checkpoint, url, verdict, existing_jsonld, findings, render, checked_at) "
        "VALUES (%s, 'before', 'https://acme.example', 'serve', %s, %s, %s, %s) RETURNING id",
        (biz["id"], Jsonb(list(jsonld)), Jsonb([{"code": c} for c in findings]),
         Jsonb(render) if render else None, f"{checked}T12:00:00+00")).fetchone()[0])


def draft(conn, biz, tmp_path):
    return truth_draft.generate_draft(conn, biz, clients_root=tmp_path / "clients", today=TODAY)


# ---- editing a draft the way a client would -------------------------------------------------------------------------------

def edit(text: str, label: str, *, occurrence: int = 0, new_label=None, fact=None, status=None, by=None, on=None) -> str:
    lines = text.split("\n")
    starts = [i for i, l in enumerate(lines) if l.startswith("### F")]
    hits = [i for i in starts if lines[i + 1].rstrip() == f"LABEL: {label}".rstrip()]
    start = hits[occurrence]
    end = next((i for i in starts if i > start), len(lines))
    for prefix, value in (("LABEL:", new_label), ("FACT:", fact), ("STATUS:", status), ("CONFIRMED BY:", by),
                          ("CONFIRMED ON:", on)):
        if value is not None:
            j = next(i for i in range(start, end) if lines[i].startswith(prefix))
            lines[j] = f"{prefix} {value}".rstrip()
    return "\n".join(lines)


def confirm(text, label, fact=None, by="Jean Tremblay", on="2020-01-15"):
    return edit(text, label, fact=fact, status="confirmed", by=by, on=on)


def set_story(text: str, **boxes) -> str:
    for key, value in boxes.items():
        label = key.upper().replace("_", " ")
        lines = text.split("\n")
        i = next(i for i, l in enumerate(lines) if l.startswith(f"{label}:"))
        lines[i] = f"{label}: {value}"
        text = "\n".join(lines)
    return text


def filled(conn, biz, tmp_path, *edits):
    """A draft with edits applied, saved as a new file. Returns its path."""
    made = draft(conn, biz, tmp_path)
    text = made.path.read_text(encoding="utf-8")
    for e in edits:
        text = e(text)
    path = tmp_path / f"filled_{uuid.uuid4().hex[:6]}.md"
    path.write_text(text, encoding="utf-8")
    return path


def imp(biz, path, tmp_path, *, reply="yes", **kw):
    said = []
    res = import_truth(biz, path, ask=lambda p: reply, say=said.append, clients_root=tmp_path / "clients",
                       today=TODAY, operator="Sam", **kw)
    return res, said


def profiles(conn, biz):
    return conn.execute("SELECT version, source, facts, story FROM business_profiles WHERE business_id = %s "
                        "ORDER BY version", (biz["id"],)).fetchall()


def count_journal(conn, biz):
    return conn.execute("SELECT count(*) FROM client_journal WHERE business_id = %s AND entry_type = 'questionnaire_received'",
                        (biz["id"],)).fetchone()[0]


# ---- the draft ----------------------------------------------------------------------------------------------------------------

def test_the_draft_is_a_dated_file_in_generated_and_never_overwrites(conn, biz, tmp_path):
    first = draft(conn, biz, tmp_path)
    second = draft(conn, biz, tmp_path)
    folder = tmp_path / "clients" / biz["domain"] / "generated"
    assert first.path == folder / "2026-10-14_truth_draft.md"
    assert second.path == folder / "2026-10-14_truth_draft_2.md"
    assert first.path.exists()


def test_the_draft_has_both_parts_every_standard_line_and_empty_extra_lines(conn, biz, tmp_path):
    text = draft(conn, biz, tmp_path).path.read_text(encoding="utf-8")
    assert text.startswith(fmt.MAGIC) and fmt.PART1 in text and fmt.PART2 in text
    labels = [l[len("LABEL: "):] for l in text.splitlines() if l.startswith("LABEL:")]
    for needed in ("Name as customers know it", "Legal name", "Location 1 address", "Location 1 phone",
                   "Location 1 hours", "Services", "Areas we serve", "Website", "Social profiles"):
        assert needed in labels
    assert labels.count("") == fmt.EXTRA_SLOTS                                   # empty lines for extra facts
    for story_label in ("DESCRIPTION:", "GOALS:", "CUSTOMERS:", "CUSTOMER QUESTIONS:"):
        assert story_label in text
    starts = lambda prefix: sum(1 for l in text.splitlines() if l.startswith(prefix))
    assert starts("CONFIRMED BY:") == starts("CONFIRMED ON:") == starts("### F")              # boxes on every fact


def test_every_value_in_a_fresh_draft_is_unconfirmed(conn, biz, tmp_path):
    add_diagnosis(conn, biz)
    text = draft(conn, biz, tmp_path).path.read_text(encoding="utf-8")
    status_lines = [l for l in text.splitlines() if l.startswith("STATUS:")]
    assert status_lines and set(status_lines) == {"STATUS: unconfirmed"}
    assert not [l for l in text.splitlines() if l.startswith("CONFIRMED BY: ") or l.startswith("CONFIRMED ON: ")]


def test_values_from_the_latest_diagnosis_are_prefilled_and_marked_unconfirmed(conn, biz, tmp_path):
    add_diagnosis(conn, biz, jsonld=['{"@type": "Organization", "name": "Old Name"}'], checked="2026-09-01")
    newest = add_diagnosis(conn, biz, checked="2026-10-10")
    made = draft(conn, biz, tmp_path)
    assert made.diagnosis_used == newest and made.from_site >= 9 and made.from_record == 1
    parsed = fmt.parse_document(made.path.read_text(encoding="utf-8"), today=TODAY)
    assert parsed.problems == []
    by_label = {f["label"]: f for f in parsed.facts}
    assert by_label["Legal name"]["value"] == "11297775 Canada Inc"                # looks like the legal entity
    assert by_label["Name as customers know it"]["value"] == biz["name"]           # so the brand name comes from `aeo add`
    assert by_label["Location 1 address"]["value"] == "1 Main St, Montreal, QC, H1A 1A1, CA"
    assert by_label["Location 1 phone"]["value"] == "(514) 555-0100"
    assert by_label["Location 1 hours"]["value"] == "Monday, Tuesday 09:00-17:00"
    assert by_label["Areas we serve"]["value"] == "Montreal; Laval"
    assert by_label["Year founded"]["value"] == "2019" and by_label["Email"]["value"] == "hi@acme.example"
    assert by_label["Social profiles"]["value"] == "https://facebook.com/acme\nhttps://instagram.com/acme"
    assert all(f["status"] == "unconfirmed" and f["confirmed_by"] is None for f in parsed.facts)


def test_a_finding_in_the_diagnosis_becomes_a_direct_question_under_its_fact(conn, biz, tmp_path):
    add_diagnosis(conn, biz, findings=["SCHEMA_NAME_IS_LEGAL_ENTITY", "SCHEMA_OPENING_HOURS_EMPTY"],
                  render={"h1_texts": ["Water for every event"]})
    text = draft(conn, biz, tmp_path).path.read_text(encoding="utf-8")
    assert "Your site's data names the business '11297775 Canada Inc'. What name should AI use?" in text
    assert "When are you open, including winter" in text and 'The main heading on your page says: "Water for every event"' in text


def test_with_no_diagnosis_nothing_is_guessed_beyond_what_was_typed_when_the_business_was_added(conn, biz, tmp_path):
    made = draft(conn, biz, tmp_path)
    assert made.diagnosis_used is None
    facts = {f["label"]: f["value"] for f in fmt.parse_document(made.path.read_text(encoding="utf-8")).facts}
    assert set(facts) == {"Name as customers know it", "Website"}


def test_the_prefill_reads_what_is_there_and_survives_junk():
    business = {"name": None, "website_url": None}
    assert from_diagnosis(None, business) == ({}, {}, set())
    junk = {"existing_jsonld": ["{not json", "[]", '{"@type": "Organization"}'], "findings": None, "render": None}
    assert from_diagnosis(junk, business)[0] == {}


def test_no_industry_or_city_is_written_into_the_code():
    root = Path(fmt.__file__).parent
    source = " ".join(p.read_text(encoding="utf-8").lower() for p in root.glob("*.py"))
    for word in ("o'land", "oland", "montreal", "station", "refill", "restaurant", "dentist"):
        assert not re.search(rf"\b{re.escape(word)}", source), word


def test_a_new_draft_carries_over_confirmed_facts_and_the_story(conn, biz, tmp_path):
    path = filled(conn, biz, tmp_path, lambda t: confirm(t, "Location 1 phone", fact="514-555-0100"),
                  lambda t: edit(t, "", fact="Delivery in 2 days", new_label="Delivery time"),
                  lambda t: set_story(t, description="We rent water stations."))
    imp(biz, path, tmp_path)
    again = draft(conn, biz, tmp_path)
    assert again.based_on_version == 2 and again.carried_forward == 4
    parsed = fmt.parse_document(again.path.read_text(encoding="utf-8"), today=TODAY)
    by_label = {f["label"]: f for f in parsed.facts}
    assert by_label["Location 1 phone"]["status"] == "confirmed" and by_label["Location 1 phone"]["confirmed_by"] == "Jean Tremblay"
    assert by_label["Delivery time"]["value"] == "Delivery in 2 days"
    assert parsed.story["description"] == "We rent water stations."
    res, _ = imp(biz, again.path, tmp_path)               # importing the untouched re-draft changes nothing
    assert res.saved is False


# ---- reading a file: every problem is listed, nothing is saved --------------------------------------------------------------

def refused(biz, path, tmp_path) -> list[str]:
    with pytest.raises(TruthImportRefused) as caught:
        import_truth(biz, path, ask=lambda p: pytest.fail("must not ask"), say=lambda s: None,
                     clients_root=tmp_path / "clients", today=TODAY)
    return caught.value.problems


def nothing_saved(conn, biz, tmp_path, versions=1):
    assert len(profiles(conn, biz)) == versions
    assert count_journal(conn, biz) == 0
    assert not (tmp_path / "clients" / biz["domain"] / "evidence").exists()


def test_a_fact_marked_confirmed_needs_both_confirmed_by_and_confirmed_on(conn, biz, tmp_path):
    path = filled(conn, biz, tmp_path,
                  lambda t: edit(t, "Legal name", fact="Acme Inc", status="confirmed"),
                  lambda t: edit(t, "Location 1 phone", fact="514", status="confirmed", by="Jean"),
                  lambda t: edit(t, "Location 1 hours", fact="9-5", status="confirmed", on="2020-01-15"))
    problems = refused(biz, path, tmp_path)
    assert any("Legal name" in p and "CONFIRMED BY is empty" in p for p in problems)
    assert any("Legal name" in p and "CONFIRMED ON is empty" in p for p in problems)
    assert any("Location 1 phone" in p and "CONFIRMED ON is empty" in p for p in problems)
    assert any("Location 1 hours" in p and "CONFIRMED BY is empty" in p for p in problems)
    assert len(problems) == 4                                   # all of them at once, not one at a time
    nothing_saved(conn, biz, tmp_path)


def test_other_broken_facts_are_each_listed(conn, biz, tmp_path):
    path = filled(conn, biz, tmp_path,
                  lambda t: edit(t, "Legal name", fact="Acme Inc", status="maybe"),
                  lambda t: edit(t, "Year founded", fact="2019", status="confirmed", by="Jean", on="15 Jan 2020"),
                  lambda t: edit(t, "Website", fact="https://a.example", status="confirmed", by="Jean", on="2999-01-01"),
                  lambda t: edit(t, "Email", fact="a@a.example", by="Jean"),                       # by filled, but unconfirmed
                  lambda t: edit(t, "Languages", status="confirmed", by="Jean", on="2020-01-15"),  # confirmed, no fact
                  lambda t: edit(t, "Services", fact="Rentals"),
                  lambda t: edit(t, "", fact="No label given"),
                  lambda t: edit(t, "", occurrence=1, new_label="Services", fact="duplicate of a printed label"))
    problems = "\n".join(refused(biz, path, tmp_path))
    assert "STATUS must be 'confirmed' or 'unconfirmed', not 'maybe'" in problems
    assert "CONFIRMED ON must be a date like 2026-10-14, not '15 Jan 2020'" in problems
    assert "CONFIRMED ON 2999-01-01 is in the future" in problems
    assert "CONFIRMED BY/ON filled but STATUS is 'unconfirmed'" in problems
    assert "has confirmation details but no fact written" in problems
    assert "this fact has no LABEL" in problems
    assert "the label is already used by" in problems
    nothing_saved(conn, biz, tmp_path)


@pytest.mark.parametrize("damage, message", [
    (lambda t: t.replace(fmt.MAGIC, "SOMETHING ELSE"), "is not a truth document"),
    (lambda t: t.replace(fmt.PART1, "## PART ONE"), "heading '## PART 1 · CONFIRMED FACTS' is missing"),
    (lambda t: t.replace(fmt.PART2, "## STORY"), "heading '## PART 2 · THE STORY' is missing"),
    (lambda t: t.replace("\nSTATUS: unconfirmed", "", 1), "the line 'STATUS:' is missing"),
    (lambda t: t.replace("\nGOALS:", "\nAIMS:"), "the story line 'GOALS:' is missing"),
])
def test_a_broken_file_is_refused_and_says_what_is_wrong(conn, biz, tmp_path, damage, message):
    path = filled(conn, biz, tmp_path, damage)
    assert any(message in p for p in refused(biz, path, tmp_path))
    nothing_saved(conn, biz, tmp_path)


def test_two_blocks_with_the_same_number_are_refused(conn, biz, tmp_path):
    path = filled(conn, biz, tmp_path, lambda t: t.replace("### F2\n", "### F1\n", 1))
    assert any("two blocks have this number" in p for p in refused(biz, path, tmp_path))


def test_a_file_for_another_business_is_refused(conn, biz, tmp_path):
    other = {"id": str(uuid.uuid4()), "name": "Someone else", "domain": "else.example"}
    path = filled(conn, biz, tmp_path, lambda t: confirm(t, "Legal name", fact="Acme Inc"))
    with pytest.raises(TruthImportRefused, match="1 problem"):
        import_truth(other, path, ask=lambda p: "yes", say=lambda s: None, clients_root=tmp_path / "c2", today=TODAY)
    nothing_saved(conn, biz, tmp_path)


def test_a_missing_file_is_an_error(biz, tmp_path):
    with pytest.raises(TruthImportError, match="file not found"):
        import_truth(biz, tmp_path / "nope.md", say=lambda s: None)


# ---- saving ------------------------------------------------------------------------------------------------------------------------

def first_import(conn, biz, tmp_path, *edits):
    path = filled(conn, biz, tmp_path, lambda t: confirm(t, "Legal name", fact="Acme Water Inc"),
                  lambda t: confirm(t, "Location 1 phone", fact="514-555-0100"),
                  lambda t: edit(t, "Services", fact="Rentals; delivery"),                     # typed but NOT confirmed
                  lambda t: set_story(t, description="We rent water stations.", goals="More festivals"), *edits)
    res, said = imp(biz, path, tmp_path)
    return path, res, said


def test_an_import_saves_a_new_version_and_never_touches_the_old_one(conn, biz, tmp_path):
    before = profiles(conn, biz)
    assert [(v, s) for v, s, *_ in before] == [(1, "client")]
    path, res, said = first_import(conn, biz, tmp_path)
    after = profiles(conn, biz)
    assert res.saved and res.version == 2 and res.previous_version == 1
    assert after[0] == before[0]                                                  # version 1: exactly as it was
    version, source, facts, story = after[1]
    assert (version, source) == (2, "client")
    by_label = {f["label"]: f for f in facts}
    assert by_label["Legal name"] == {"label": "Legal name", "value": "Acme Water Inc", "status": "confirmed",
                                      "confirmed_by": "Jean Tremblay", "confirmed_on": "2020-01-15"}
    assert story["description"] == "We rent water stations." and story["customers"] == ""
    # 2 confirmed (legal name, phone); 3 unconfirmed (Services typed but not confirmed, plus the name and website
    # that were pre-filled from what `aeo add` recorded)
    assert (res.confirmed, res.unconfirmed) == (2, 3)


def test_an_unconfirmed_fact_is_stored_as_unconfirmed_never_as_confirmed(conn, biz, tmp_path):
    first_import(conn, biz, tmp_path)
    facts = {f["label"]: f for f in profiles(conn, biz)[1][2]}
    for label in ("Services", "Name as customers know it", "Website"):
        assert facts[label]["status"] == "unconfirmed"
        assert facts[label]["confirmed_by"] is None and facts[label]["confirmed_on"] is None
    assert {f["field"] for f in confirmed_facts(profiles(conn, biz)[1][2])} == {"Legal name", "Location 1 phone"}


def test_the_filled_file_is_copied_into_evidence_and_a_journal_entry_is_written(conn, biz, tmp_path):
    path, res, _ = first_import(conn, biz, tmp_path)
    expected = tmp_path / "clients" / biz["domain"] / "evidence" / f"2026-10-14_{path.stem}_filled.md"
    assert res.evidence_copy == expected and expected.read_bytes() == path.read_bytes()
    assert path.exists()                                                           # the original is left alone
    (entry,) = conn.execute("SELECT description, actor, related_table, details FROM client_journal "
                            "WHERE business_id = %s AND entry_type = 'questionnaire_received'", (biz["id"],)).fetchall()
    description, actor, table, details = entry
    assert "profile version 2" in description and "2 confirmed" in description and actor == "Sam"
    assert table == "business_profiles"
    assert details["version"] == 2 and details["previous_version"] == 1 and details["facts_added"] == 5
    assert details["file"] == f"evidence/{expected.name}" and details["story_changed"] == ["description", "goals"]


def test_the_diff_is_printed_before_asking_and_lists_added_changed_removed_and_story(conn, biz, tmp_path):
    first_import(conn, biz, tmp_path)
    path = filled(conn, biz, tmp_path,
                  lambda t: confirm(t, "Location 1 phone", fact="438-555-0199"),          # changed (was confirmed)
                  lambda t: edit(t, "Services", fact="", status="unconfirmed"),             # removed
                  lambda t: confirm(t, "Email", fact="hi@acme.example"),                    # added
                  lambda t: set_story(t, description="We rent water stations and taps."))   # story changed
    # the re-draft carried everything over, so the edits above are what differs from version 2
    res, said = imp(biz, path, tmp_path)
    text = "\n".join(said)
    assert "Changes compared with version 2 (this would become version 3)" in text
    assert "Facts added (1)" in text and "+ Email:" in text
    assert "Facts changed (1)" in text and '~ Location 1 phone: "514-555-0100" [confirmed by Jean Tremblay on 2020-01-15]' in text
    assert '->  "438-555-0199" [confirmed' in text
    assert "Facts removed (1)" in text and "- Services:" in text
    assert "Story changed:" in text and "description: changed" in text
    assert "Look twice: 1 fact(s) that were confirmed before are changed or removed." in text
    assert "After this: 3 confirmed fact(s)" in text
    assert res.version == 3 and len(profiles(conn, biz)) == 3


def test_the_first_import_shows_everything_as_added(conn, biz, tmp_path):
    _, _, said = first_import(conn, biz, tmp_path)
    text = "\n".join(said)
    assert "Changes compared with version 1" in text and "Facts added (5)" in text
    assert "description: added" in text and "Facts changed" not in text and "Facts removed" not in text


def test_nothing_changed_means_nothing_is_saved(conn, biz, tmp_path):
    path, *_ = first_import(conn, biz, tmp_path)
    again = filled(conn, biz, tmp_path)                               # a re-draft with no edits
    res, said = imp(biz, again, tmp_path)
    assert res.saved is False and "Nothing changed since version 2" in said[-1]
    assert len(profiles(conn, biz)) == 2 and count_journal(conn, biz) == 1


def test_a_person_must_type_yes(conn, biz, tmp_path, tty):
    path = filled(conn, biz, tmp_path, lambda t: confirm(t, "Legal name", fact="Acme Inc"))
    for reply in ("no", "y", "", "yes please"):
        with pytest.raises(TruthImportAborted):
            imp(biz, path, tmp_path, reply=reply)
        nothing_saved(conn, biz, tmp_path)
    res, _ = imp(biz, path, tmp_path, reply="  YES ")
    assert res.saved


def test_with_nobody_at_the_keyboard_it_refuses_without_asking(conn, biz, tmp_path, no_tty):
    path = filled(conn, biz, tmp_path, lambda t: confirm(t, "Legal name", fact="Acme Inc"))
    said = []
    with pytest.raises(TruthImportAborted):
        import_truth(biz, path, ask=lambda p: pytest.fail("a piped run must not be asked"), say=said.append,
                     clients_root=tmp_path / "clients", today=TODAY)
    assert said[-1] == "A person must be at the keyboard to approve this. Run this again from a terminal."
    assert any("Facts added" in l for l in said)                                   # the changes are still shown, for the log
    nothing_saved(conn, biz, tmp_path)


def test_end_of_input_counts_as_no(conn, biz, tmp_path):
    path = filled(conn, biz, tmp_path, lambda t: confirm(t, "Legal name", fact="Acme Inc"))
    def eof(prompt):
        raise EOFError
    with pytest.raises(TruthImportAborted):
        import_truth(biz, path, ask=eof, say=lambda s: None, clients_root=tmp_path / "clients", today=TODAY)
    nothing_saved(conn, biz, tmp_path)


def test_text_boxes_keep_blank_lines_markdown_and_label_words(conn, biz, tmp_path):
    tricky = "First paragraph.\n\n- a bullet\n---\nGOALS are not a label mid-line, and FACT: neither."
    path = filled(conn, biz, tmp_path, lambda t: edit(t, "Services", fact="Rentals;\nDelivery\n\nInstallation"),
                  lambda t: t.replace("DESCRIPTION:", "DESCRIPTION: " + tricky.replace("\n", "\n"), 1))
    imp(biz, path, tmp_path)
    version, _, facts, story = profiles(conn, biz)[1]
    assert {f["label"]: f["value"] for f in facts}["Services"] == "Rentals;\nDelivery\n\nInstallation"
    assert story["description"] == tricky


def test_the_database_itself_refuses_to_edit_or_delete_a_saved_version(conn, biz, tmp_path):
    first_import(conn, biz, tmp_path)
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be changed"):
        conn.execute("UPDATE business_profiles SET facts = '[]'::jsonb WHERE business_id = %s AND version = 2", (biz["id"],))
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be changed"):
        conn.execute("UPDATE business_profiles SET story = '{}'::jsonb WHERE business_id = %s AND version = 2", (biz["id"],))
    with pytest.raises(psycopg.errors.RaiseException, match="never deleted"):
        conn.execute("DELETE FROM business_profiles WHERE business_id = %s AND version = 1", (biz["id"],))
    assert len(profiles(conn, biz)) == 2


def test_diff_versions_matches_facts_by_label_ignoring_case():
    old = [{"label": "Phone", "value": "1", "status": "confirmed", "confirmed_by": "A", "confirmed_on": "2020-01-01"}]
    same = [{"label": "phone ", "value": "1", "status": "confirmed", "confirmed_by": "A", "confirmed_on": "2020-01-01"}]
    assert diff_versions(old, same, {}, {}).empty
    other = [{**same[0], "confirmed_by": "B"}]
    assert len(diff_versions(old, other, {}, {}).changed) == 1


# ---- claim checking uses only confirmed facts, from the version current when the run started ----------------------------------

Q = "who supplies water refill stations for events?"


def test_the_story_and_unconfirmed_facts_never_reach_claim_checking(conn, biz, tmp_path):
    first_import(conn, biz, tmp_path)
    loaded = store.load_business_and_profile(conn, biz["id"])
    assert loaded["profile_version"] == 2
    assert {f["field"]: f["value"] for f in loaded["facts"]} == {"Legal name": "Acme Water Inc", "Location 1 phone": "514-555-0100"}
    assert "We rent water stations" not in json.dumps(loaded)                        # the story is not in there at all


def run_with_facts(biz, qfile, facts_edit, tmp_path, conn):
    path = filled(conn, biz, tmp_path, facts_edit)
    imp(biz, path, tmp_path)


def test_a_fill_in_run_is_judged_by_the_version_current_when_it_started(conn, biz, qfile, tmp_path):
    known_as_acme_refill(biz, "Acme")
    run_with_facts(biz, qfile, lambda t: confirm(t, "Location 1 phone", fact="OLD-111"), tmp_path, conn)       # version 2
    path, made = generate(biz, qfile, tmp_path, questions=(Q,))
    run = store.get_run(conn, made.probe_run_id)
    assert run["profile_id"] == str(conn.execute("SELECT id FROM business_profiles WHERE business_id = %s AND version = 2",
                                                 (biz["id"],)).fetchone()[0])
    started = store.started_details(conn, made.probe_run_id)
    assert started["profile_version"] == 2                                            # recorded on the run's start entry too

    # The client changes their phone AFTER the run started: version 3.
    run_with_facts(biz, qfile, lambda t: confirm(t, "Location 1 phone", fact="NEW-222"), tmp_path, conn)
    assert latest_profile(conn, biz["id"])["version"] == 3

    all_done(made.path, question_count=1)
    client = FakeAnthropicClient([extraction(("Acme", True, True)), claims_reply(("Phone is OLD-111", "Location 1 phone", "correct")),
                                  extraction(("Acme", True, True)), claims_reply(("Phone is OLD-111", "Location 1 phone", "correct"))])
    import_fill_in(made.path, client=client, ask=lambda p: "yes", say=lambda s: None, clients_root=tmp_path / "clients")
    sent = json.loads(client.messages.calls[1]["messages"][0]["content"])
    assert sent["confirmed_facts"] == [{"field": "Location 1 phone", "value": "OLD-111"}]       # not NEW-222


def test_a_run_started_before_any_confirmed_fact_stays_unchecked_even_after_facts_arrive(conn, biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path, questions=(Q,))                      # version 1: nothing confirmed
    run_with_facts(biz, qfile, lambda t: confirm(t, "Location 1 phone", fact="514-555-0100"), tmp_path, conn)
    all_done(made.path, question_count=1)
    client = FakeAnthropicClient([extraction(("Acme", True, True))] * 2)
    res = import_fill_in(made.path, client=client, ask=lambda p: "yes", say=lambda s: None, clients_root=tmp_path / "clients")
    assert res.derive.claims_checked == 0 and len(client.messages.calls) == 2         # extraction only


def test_a_terminal_run_records_the_version_it_started_with(conn, biz, qfile, tmp_path):
    known_as_acme_refill(biz, "Acme")
    run_with_facts(biz, qfile, lambda t: confirm(t, "Location 1 phone", fact="514-555-0100"), tmp_path, conn)
    typed = ["y", ""] + answer("a1") + answer("a2")        # logged out? / location, then the answers
    op = Operator(typed)
    out = run_manual_audit(biz["id"], str(qfile(questions=(Q,))), checkpoint=None, ask=op.ask, say=op.say,
                           client=FakeAnthropicClient([extraction(("Acme", True, True)), claims_reply(("x", None, "unverifiable")),
                                                       extraction(("Acme", True, True)), claims_reply(("x", None, "unverifiable"))]))
    stored = conn.execute("SELECT p.version FROM probe_runs r JOIN business_profiles p ON p.id = r.business_profile_id "
                          "WHERE r.id = %s", (out.probe_run_id,)).fetchone()
    assert stored == (2,) and out.derive.claims_checked == 2


# ---- the commands --------------------------------------------------------------------------------------------------------------

def run(*args, **kw):
    return CliRunner().invoke(cli.app, list(args), **kw)


def test_cli_draft_then_import(conn, biz, tmp_path, tty):
    add_diagnosis(conn, biz)
    made = run("truth", "draft", "--business", biz["domain"])
    assert made.exit_code == 0, made.output
    assert "_truth_draft.md" in made.output and "pre-filled from the site's structured data" in made.output
    draft_file = next(client_files.CLIENTS_ROOT.rglob("*_truth_draft.md"))
    draft_file.write_text(confirm(draft_file.read_text(encoding="utf-8"), "Legal name", fact="Acme Inc"), encoding="utf-8")

    said_no = run("truth", "import", "--business", biz["name"], str(draft_file), input="no\n")
    assert said_no.exit_code == 1 and "Aborted. Nothing was saved." in said_no.output
    assert len(profiles(conn, biz)) == 1

    ok = run("truth", "import", "--business", biz["id"], str(draft_file), input="yes\n")
    assert ok.exit_code == 0, ok.output
    assert "Facts added" in ok.output and "Saved as profile version 2." in ok.output
    assert "1 confirmed fact(s)" in ok.output and "_truth_draft_filled.md" in ok.output
    assert len(profiles(conn, biz)) == 2


def test_cli_lists_every_problem_and_exits_non_zero(conn, biz, tmp_path):
    made = draft(conn, biz, tmp_path)
    bad = tmp_path / "bad.md"
    bad.write_text(edit(edit(made.path.read_text(encoding="utf-8"), "Legal name", fact="A", status="confirmed"),
                        "Website", fact="B", status="confirmed", by="Jean"), encoding="utf-8")
    result = run("truth", "import", "--business", biz["id"], str(bad))
    assert result.exit_code == 1 and "Nothing was saved. Fix these and import again:" in result.output
    assert result.output.count("  - ") == 3
    assert len(profiles(conn, biz)) == 1


def test_cli_import_without_a_terminal_refuses(conn, biz, tmp_path, no_tty):
    path = filled(conn, biz, tmp_path, lambda t: confirm(t, "Legal name", fact="Acme Inc"))
    result = run("truth", "import", "--business", biz["id"], str(path), input="yes\n")
    assert result.exit_code == 1 and "A person must be at the keyboard" in result.output
    assert len(profiles(conn, biz)) == 1


def test_cli_draft_without_a_diagnosis_says_so(biz):
    result = run("truth", "draft", "--business", biz["id"])
    assert result.exit_code == 0 and "no diagnosis for this business yet" in result.output
    assert "pre-filled from what was entered when the business was added" in result.output
