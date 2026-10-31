"""`aeo questions draft` (PLAN.md step 13b). The AI is always the scripted fake client, and the homepage is a fake
transport: nothing here calls a model or touches the network. The database is the real test database."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from aeo import cli
from audit import cost_gate
from audit.cost_gate import CostGateAborted
from audit.question_set import load_question_set, resolve_tags
from core import client_files
from core.db import get_connection
from diagnose.fetch import Fetcher
from dossier import questions as dossier_questions
from questions_draft import plan
from questions_draft.inputs import UNCONFIRMED
from questions_draft.run import AGENT, DraftError, draft_questions, summary_lines
from tests.conftest import FakeAnthropicClient, text_response
from tests.helpers_diagnose import page, response
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)


@pytest.fixture(autouse=True)
def person_at_keyboard(monkeypatch):
    """Under pytest nobody is at a keyboard. Tests that need the gate to pass say there is."""
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: True)


# ---- building a scripted reply -------------------------------------------------------------------------------------------------

_SYLLABLES = ["ba", "ko", "ri", "tu", "mel", "sa", "do", "vin", "lu", "pe", "gar", "no", "fi", "zan", "he", "wo"]


class Words:
    """Pronounceable, never-repeating made-up words, so no two questions look alike to the near-duplicate check."""
    def __init__(self, seed: int = 7):
        self.rng, self.used = random.Random(seed), set()

    def one(self) -> str:
        while True:
            w = "".join(self.rng.choice(_SYLLABLES) for _ in range(3))
            if w not in self.used:
                self.used.add(w)
                return w

    def many(self, n: int) -> str:
        return " ".join(self.one() for _ in range(n))


def q(text, kind="discovery", language="en", intent="commercial", is_target=False, control=False, relies=None):
    return {"text": text, "kind": kind, "language": language, "intent": None if kind == "brand" else intent,
            "is_target": is_target, "control": control, "relies_on_fact_ids": relies or []}


def clean_set(b: dict, languages=("en", "fr"), words: Words | None = None) -> list[dict]:
    """A reply that follows the plan for 32 questions: per language 10 discovery, 2 control, 4 brand."""
    w = words or Words()
    name, domain = b["name"], b["domain"]
    brand = {"en": ["What does {n} do?", "Tell me about {d}", "Where is {n} based?", "Who are the customers of {n}?"],
             "fr": ["Que fait {n}?", "Parle-moi de {d}", "Où se trouve {n}?", "Qui sont les clients de {n}?"]}
    out = []
    for lang in languages:
        sizes = ["short"] * 4 + ["medium"] * 4 + ["long"] * 2
        for i, size in enumerate(sizes):
            n = {"short": 5, "medium": 14, "long": 40}[size]
            out.append(q(w.many(n), language=lang, is_target=i < 2))
        out += [q(w.many(6) + "?", language=lang, intent="informational", control=True) for _ in range(2)]
        out += [q(t.format(n=name, d=domain), kind="brand", language=lang) for t in brand.get(lang, brand["en"])]
    return out


def reply(items) -> str:
    return json.dumps({"questions": items})


def fake(*replies) -> FakeAnthropicClient:
    return FakeAnthropicClient([text_response(r if isinstance(r, str) else reply(r), input_tokens=3000, output_tokens=1800)
                                for r in replies])


def diagnosed(b: dict, tmp_path: Path, html: str | None = None, jsonld: str | None = None) -> Path:
    """Gives the business a diagnosis and the page it saved, the way `aeo diagnose` leaves them."""
    now = datetime.now(timezone.utc)
    html = html or page(300, h1="Water stations for events", title="Acme", extra_head='<meta name="description" content="We rent water refill stations.">')
    conn = get_connection()
    try:
        conn.execute("INSERT INTO diagnoses (business_id, checkpoint, url, verdict, checked_at, existing_jsonld) VALUES (%s, 'before', %s, 'serve', %s, %s)",
                     (b["id"], f"https://{b['domain']}/", now, json.dumps([jsonld] if jsonld else [])))
    finally:
        conn.close()
    folder = client_files.evidence_dir(b["domain"], tmp_path / "clients")
    folder.mkdir(parents=True)
    saved = folder / f"{now.date().isoformat()}_raw_page_run1.html"
    saved.write_text(html, encoding="utf-8")
    return saved


def run(b, tmp_path, client, *, languages=("en", "fr"), size=32, note=None, ask=lambda p: "yes", fetcher=None, said=None):
    conn = get_connection()
    try:
        return draft_questions(conn, b, languages=list(languages) if languages else None, size=size, note=note, ask=ask,
                               say=(said if said is not None else []).append, client=client, fetcher=fetcher,
                               clients_root=tmp_path / "clients")
    finally:
        conn.close()


def sent_to_model(client, call=0) -> dict:
    return json.loads(client.messages.calls[call]["messages"][0]["content"])


def file_items(result) -> list[dict]:
    return [json.loads(l.strip()[2:]) for l in result.path.read_text().splitlines() if l.startswith("  - {")]


# ---- a clean draft ------------------------------------------------------------------------------------------------------------------

def test_a_clean_draft_has_the_right_counts_and_tags(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    r = run(biz, tmp_path, client)
    items = file_items(r)
    assert len(items) == 32 and r.calls == 1 and len(client.messages.calls) == 1
    assert sum(i["type"] == "discovery" for i in items) == 24 and sum(i["type"] == "brand" for i in items) == 8
    assert sum(i["control"] for i in items) == 4
    for lang in ("en", "fr"):
        assert sum(i["language"] == lang and i["type"] == "brand" for i in items) == 4
        assert sum(i["language"] == lang and i["type"] == "discovery" and not i["control"] for i in items) == 10
    for i in items:
        assert set(i) >= {"text", "type", "length", "language", "is_target", "control", "spot_check"}
        assert i["length"] in plan.LENGTHS and i["language"] in ("en", "fr")
        assert ("intent" in i) == (i["type"] == "discovery")            # brand questions have no intent, as in the v0 draft
        if i["control"] or i["type"] == "brand":
            assert i["is_target"] is False
    assert any(i["is_target"] for i in items) and r.rejected == [] and r.missing == {}


def test_the_draft_is_logged_like_other_ai_runs(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    conn = get_connection()
    try:
        row = conn.execute("SELECT model, prompt_version, input_tokens, output_tokens, cost_usd, status FROM runs "
                           "WHERE business_id = %s AND agent = %s", (biz["id"], AGENT)).fetchone()
    finally:
        conn.close()
    assert row[0] == "claude-sonnet-5" and row[1] == "v2" and (row[2], row[3]) == (3000, 1800) and float(row[4]) > 0 and row[5] == "success"
    assert r.cost["calls"] == 1 and r.cost["input_tokens"] == 3000 and abs(r.cost["cost_usd"] - float(row[4])) < 1e-9


def test_the_summary_shows_counts_the_spot_check_and_the_cost(biz, tmp_path):
    diagnosed(biz, tmp_path)
    text = "\n".join(summary_lines(run(biz, tmp_path, fake(clean_set(biz)))))
    assert "24 discovery (of which 4 control) and 8 brand" in text and "By language: 16 en, 16 fr" in text and "AI cost: 1 call(s)" in text
    assert text.count("\n  ") >= 8 and "spot-check questions" in text


# ---- length is counted -------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("words, expected", [(1, "short"), (6, "short"), (7, "medium"), (30, "medium"), (31, "long"), (60, "long")])
def test_length_comes_from_the_word_count(words, expected):
    assert plan.length_of(" ".join(["word"] * words)) == expected


def test_the_models_own_length_label_is_ignored(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[0]["length"] = "long"                                   # the model says long; it is five words
    r = run(biz, tmp_path, fake(items))
    first = next(i for i in file_items(r) if i["text"] == items[0]["text"])
    assert first["length"] == "short"
    assert all(i["length"] == plan.length_of(i["text"]) for i in file_items(r))


# ---- what the checks reject -----------------------------------------------------------------------------------------------------------

def test_a_discovery_question_with_the_brand_is_retagged_brand_and_flagged_not_dropped(biz, tmp_path):
    diagnosed(biz, tmp_path)
    bad = clean_set(biz)
    w = Words(99)
    bad[0] = q(f"Is {biz['name']} a good water station supplier?")
    bad[1] = q(f"best water stations near {biz['domain']}", is_target=True)
    replacements = [q(w.many(5)), q(w.many(14))]
    client = fake(bad, replacements)
    said: list = []
    r = run(biz, tmp_path, client, said=said)
    assert r.calls == 2 and r.rejected == [] and r.missing == {}                  # nothing dropped; the 2 missing discovery were asked for
    items = file_items(r)
    for text in (bad[0]["text"], bad[1]["text"]):
        item = next(i for i in items if i["text"] == text)
        assert item["type"] == "brand" and item["is_target"] is False and item["control"] is False and item["spot_check"] is False
        assert "intent" not in item and item["needs_review"] is True and "re-tagged brand" in item["review_reason"]
    assert not any(biz["name"] in i["text"] or biz["domain"] in i["text"] for i in items if i["type"] == "discovery")
    retry = sent_to_model(client, 1)["retry"]
    assert retry["rejected"] == [] and retry["needed"] == [{"group": "discovery", "language": "en", "count": 2}]
    assert any("asking once more" in s for s in said)


def test_a_retry_that_still_fails_keeps_the_good_questions_and_says_what_is_missing(biz, tmp_path):
    diagnosed(biz, tmp_path)
    bad = clean_set(biz)
    bad[0] = {**bad[0], "text": ""}                                            # nothing to keep: dropped
    bad[1] = {**bad[1], "text": "   "}
    client = fake(bad, [q(bad[2]["text"])])                                    # the retry only repeats a question
    r = run(biz, tmp_path, client)
    assert r.calls == 2 and len(file_items(r)) == 30                           # never padded
    assert r.missing == {("discovery", "en"): 2} and len(r.rejected) == 3
    text = "\n".join(summary_lines(r))
    assert "STILL MISSING" in text and "2 discovery question(s) in en" in text and "Dropped, not in the file: 3" in text
    assert "STILL MISSING: 2 discovery in en" in r.path.read_text()


def test_a_brand_question_that_does_not_name_the_business_is_rejected(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[-1] = q("What do they sell and where?", kind="brand", language="fr")
    r = run(biz, tmp_path, fake(items, [q("Que fait quelqu'un d'autre?", kind="brand", language="fr")]))
    assert any("must name the business" in x.reasons[0] for x in r.rejected)
    assert r.missing == {("brand", "fr"): 1}


def test_a_claim_question_is_kept_and_flagged_but_one_relying_on_an_unconfirmed_fact_is_dropped(biz, tmp_path):
    diagnosed(biz, tmp_path)                                       # a prospect: nothing is confirmed
    items = clean_set(biz)
    items[10] = q(f"Does {biz['name']} offer water stations for festivals?", kind="brand")      # states a claim, relies on nothing
    items[11] = q(f"What events does {biz['name']} serve?", kind="brand", relies=[1])            # relies on a fact that is not confirmed
    r = run(biz, tmp_path, fake(items, items[10:12]))
    assert any("not confirmed facts" in " ".join(x.reasons) for x in r.rejected)
    assert not any("What events does" in i["text"] for i in file_items(r))
    claim = next(i for i in file_items(r) if "offer water stations" in i["text"])
    assert claim["needs_review"] is True and "states a claim" in claim["review_reason"]


def test_a_brand_question_relying_on_a_confirmed_fact_is_allowed(biz, tmp_path):
    confirm_facts(biz, Services="water refill station rental")
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[10] = q(f"Does {biz['name']} offer water refill station rental?", kind="brand", relies=[1])
    r = run(biz, tmp_path, fake(items))
    assert r.rejected == [] and any("Does" in i["text"] for i in file_items(r))


def test_duplicates_and_near_duplicates_are_dropped(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[1] = dict(items[0], text=items[0]["text"].upper())                  # the same question in capitals
    items[2] = dict(items[3], text=items[3]["text"] + " today")               # one word added
    r = run(biz, tmp_path, fake(items, []))
    assert len(r.rejected) == 2
    assert "exact repeat" in r.rejected[0].reasons[0] and "repeats an earlier question" in r.rejected[1].reasons[0]
    assert plan.near_duplicate("best water stations in Toronto", "Best water stations in Toronto!")
    assert not plan.near_duplicate("best water stations in Toronto", "how do I choose a caterer for a wedding")


def test_contradictory_tags_are_corrected_not_trusted(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    control = next(i for i in items if i["control"])
    control["is_target"], control["intent"] = True, "commercial"
    brand = next(i for i in items if i["kind"] == "brand")
    brand["is_target"] = True
    r = run(biz, tmp_path, fake(items))
    saved = {i["text"]: i for i in file_items(r)}
    assert saved[control["text"]]["is_target"] is False and saved[control["text"]]["intent"] == "informational"
    assert saved[brand["text"]]["is_target"] is False


def test_extra_questions_beyond_the_plan_are_left_out_and_reported(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz) + [q(Words(5).many(6), language="en")]
    r = run(biz, tmp_path, fake(items))
    assert len(file_items(r)) == 32 and len(r.surplus) == 1 and "extra question(s) beyond the plan" in "\n".join(summary_lines(r))


def test_unusable_replies_stop_the_run_without_saving(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake("not json at all", "still { not json")
    with pytest.raises(DraftError, match="did not return a usable draft"):
        run(biz, tmp_path, client)
    assert not list((tmp_path / "clients").rglob("question_set_draft_*"))
    conn = get_connection()
    try:
        assert conn.execute("SELECT count(*) FROM human_queue WHERE business_id = %s", (biz["id"],)).fetchone()[0] == 1
    finally:
        conn.close()


# ---- the spot check ---------------------------------------------------------------------------------------------------------------------

def candidates(b, languages=("en", "fr")):
    items = clean_set(b, languages)
    kept, _ = plan.check_questions(items, name=b["name"], domain=b["domain"], alternates=[], languages=list(languages),
                                   confirmed_fact_ids=set(), already=[])
    return plan.order_for_file(kept, list(languages))


def test_the_spot_check_is_eight_questions_five_discovery_and_three_brand_with_no_controls(biz):
    ordered = candidates(biz)
    spot = plan.choose_spot_check(ordered, ["en", "fr"], biz["domain"])
    chosen = [ordered[i] for i in spot]
    assert len(chosen) == 8 == len(set(spot))
    assert sum(c.kind == "discovery" for c in chosen) == 5 and sum(c.kind == "brand" for c in chosen) == 3
    assert not any(c.control for c in chosen)
    assert {c.language for c in chosen} == {"en", "fr"}
    assert {c.language for c in chosen if c.kind == "brand"} == {"en", "fr"}
    assert {plan.length_of(c.text) for c in chosen if c.kind == "discovery"} == set(plan.LENGTHS)
    assert any(biz["domain"] in c.text for c in chosen if c.kind == "brand")           # one uses the web address


def test_the_spot_check_choice_is_deterministic_and_does_not_depend_on_the_model(biz, tmp_path):
    ordered = candidates(biz)
    assert plan.choose_spot_check(ordered, ["en", "fr"], biz["domain"]) == plan.choose_spot_check(list(ordered), ["en", "fr"], biz["domain"])
    diagnosed(biz, tmp_path)
    a = run(biz, tmp_path, fake(clean_set(biz, words=Words(1))))
    b = run(biz, tmp_path, fake(clean_set(biz, words=Words(1))))            # the same model reply again gives the same eight
    assert a.spot_check == b.spot_check and len(a.spot_check) == 8


def test_the_spot_check_covers_every_requested_language(biz):
    langs = ["en", "fr", "es"]
    items = clean_set(biz, langs[:2])
    es = [dict(i, language="es", text=i["text"] + " mas") for i in items[:8]]
    es_words = Words(33)
    es = [q(es_words.many(6 + k), language="es") for k in range(5)] + [q(f"Qué hace {biz['name']}?", kind="brand", language="es")]
    kept, _ = plan.check_questions(items + es, name=biz["name"], domain=biz["domain"], alternates=[], languages=langs,
                                   confirmed_fact_ids=set(), already=[])
    ordered = plan.order_for_file(kept, langs)
    chosen = [ordered[i] for i in plan.choose_spot_check(ordered, langs, biz["domain"])]
    assert {c.language for c in chosen} == set(langs) and len(chosen) == 8


def test_a_small_set_gives_as_many_as_it_can_and_never_a_control(biz):
    items = [q(Words(2).many(6))] + [q(f"What does {biz['name']} do?", kind="brand"), q("How long do tortoises live?", intent="informational", control=True)]
    kept, _ = plan.check_questions(items, name=biz["name"], domain=biz["domain"], alternates=[], languages=["en"],
                                   confirmed_fact_ids=set(), already=[])
    chosen = plan.choose_spot_check(kept, ["en"], biz["domain"])
    assert len(chosen) == 2 and not any(kept[i].control for i in chosen)


# ---- languages and the plan --------------------------------------------------------------------------------------------------------------

def test_the_plan_scales_with_the_size_and_splits_across_languages():
    t = plan.make_targets(32, ["en", "fr"])
    assert (t.discovery, t.control, t.brand) == (20, 4, 8) and t.by_language["fr"] == {"discovery": 10, "control": 2, "brand": 4}
    assert t.length_aim == {"short": 13, "medium": 13, "long": 6}
    small = plan.make_targets(16, ["en"])
    assert (small.discovery, small.control, small.brand) == (10, 2, 4) and small.discovery + small.control + small.brand == 16
    three = plan.make_targets(32, ["en", "fr", "es"])
    assert sum(r["discovery"] for r in three.by_language.values()) == 20 and three.by_language["en"]["discovery"] == 7
    for bad in (5, 11, 101):
        with pytest.raises(ValueError):
            plan.make_targets(bad, ["en"])


def test_two_languages_give_questions_in_both(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    r = run(biz, tmp_path, client, languages=("en", "fr"))
    assert {i["language"] for i in file_items(r)} == {"en", "fr"}
    assert sent_to_model(client)["languages"] == ["en", "fr"] and sent_to_model(client)["plan"]["per_language"]["fr"]["brand"] == 4


def test_the_default_language_is_the_pages_own_else_english(biz, tmp_path):
    diagnosed(biz, tmp_path, html=page(300).replace("<html>", '<html lang="fr-CA">'))
    client = fake(clean_set(biz, ["fr"]))
    said: list = []
    r = run(biz, tmp_path, client, languages=None, size=16, said=said)
    assert r.languages == ["fr"] and sent_to_model(client)["languages"] == ["fr"]
    assert any("Language: fr (the page's lang attribute)" in s for s in said)


def test_with_no_stated_language_the_default_is_english(biz, tmp_path):
    diagnosed(biz, tmp_path)                                         # the test page has no lang attribute
    r = run(biz, tmp_path, fake(clean_set(biz, ["en"])), languages=None, size=16)
    assert r.languages == ["en"] and any("defaulting to en" in s for s in r.source_lines)


# ---- what the model is allowed to know ------------------------------------------------------------------------------------------------------

def test_with_no_truth_document_every_fact_is_labelled_unconfirmed_and_the_site_text_is_sent(biz, tmp_path):
    jsonld = json.dumps({"@context": "https://schema.org", "@type": "LocalBusiness", "name": "Acme Water", "areaServed": "Toronto"})
    diagnosed(biz, tmp_path, jsonld=jsonld)
    client = fake(clean_set(biz))
    run(biz, tmp_path, client)
    sent = sent_to_model(client)
    assert sent["facts"] and all(f["status"] == UNCONFIRMED == "from website, unconfirmed" for f in sent["facts"])
    assert {"Areas we serve", "Meta description", "Main heading of the page"} <= {f["field"] for f in sent["facts"]}
    assert "unconfirmed" in sent["facts_source"] and sent["site_text"]
    assert all("confirmed" not in f["status"].replace("unconfirmed", "") for f in sent["facts"])


def test_with_a_truth_document_only_confirmed_facts_go_to_the_model_and_no_site_text(biz, tmp_path):
    confirm_facts(biz, Services="water refill station rental", Areas_we_serve="Toronto and Montreal")
    from core.profiles import save_new_version
    conn = get_connection()
    try:
        save_new_version(conn, biz["id"], story={}, facts=[
            {"label": "Services", "value": "water refill station rental", "status": "confirmed", "confirmed_by": "J", "confirmed_on": "2026-10-01"},
            {"label": "Price ranges", "value": "$500 a day", "status": "unconfirmed"}])
    finally:
        conn.close()
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    r = run(biz, tmp_path, client)
    sent = sent_to_model(client)
    assert [f["field"] for f in sent["facts"]] == ["Services"] and sent["facts"][0]["status"] == "confirmed"
    assert "site_text" not in sent and "truth document" in sent["facts_source"]
    assert "truth document version" in r.path.read_text() and "confirmed facts only" in r.path.read_text()


def test_the_operator_note_is_passed_on_as_context(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    r = run(biz, tmp_path, client, note="serves Quebec only")
    assert sent_to_model(client)["operator_note"] == "serves Quebec only" and "Operator note: serves Quebec only" in r.path.read_text()


def test_the_prompt_forbids_adding_facts_and_fixes_the_reply_shape():
    text = (Path(__file__).parent.parent / "prompts" / "questions_draft" / "v1.md").read_text()
    assert "do not add facts" in text.lower() and "ONLY a JSON object" in text and '"questions"' in text
    assert "never contain the business name" in text and "unrelated" in text


def test_with_no_diagnosis_the_homepage_is_fetched_once_and_the_source_is_stated(biz, tmp_path):
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return response(200, page(300, h1="Water stations for events"))
    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True), min_interval=0)
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), fetcher=fetcher, said=said)
    assert len(seen) == 1 and any("homepage, fetched once just now (there is no diagnosis" in s for s in said)


def test_with_nothing_to_base_it_on_the_command_stops_and_says_what_to_do(biz, tmp_path):
    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(lambda r: response(503, "down")), follow_redirects=True), min_interval=0)
    client = fake(clean_set(biz))
    with pytest.raises(DraftError, match="nothing to base the draft on"):
        run(biz, tmp_path, client, fetcher=fetcher)
    assert client.messages.calls == []


def test_a_diagnosis_source_is_named_in_words(biz, tmp_path):
    diagnosed(biz, tmp_path)
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said)
    assert any("Site text: the latest diagnosis (before," in s and "the page it saved" in s for s in said)


# ---- the cost gate -------------------------------------------------------------------------------------------------------------------------------

def test_without_a_keyboard_the_cost_gate_refuses_and_nothing_is_spent_or_saved(biz, tmp_path, monkeypatch):
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: False)
    diagnosed(biz, tmp_path)
    client, said = fake(clean_set(biz)), []
    with pytest.raises(CostGateAborted):
        run(biz, tmp_path, client, said=said)
    assert client.messages.calls == [] and not list((tmp_path / "clients").rglob("question_set_draft_*"))
    assert any("person must be at the keyboard" in s for s in said) and any("Up to $" in s for s in said)


def test_anything_but_a_typed_yes_aborts(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    with pytest.raises(CostGateAborted):
        run(biz, tmp_path, client, ask=lambda p: "y")
    assert client.messages.calls == []


def test_the_cost_card_shows_the_worst_case_of_three_calls(biz, tmp_path):
    diagnosed(biz, tmp_path)
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said)
    card = "\n".join(said)
    assert "Up to 3 calls" in card and "claude-sonnet-5" in card and "Up to $" in card
    assert "output capped at 8,000 tokens per call" in card and "tokens per call" in card


def test_dash_dash_yes_is_not_supported():
    result = CliRunner().invoke(cli.app, ["questions", "draft", "x.example", "--yes"])
    assert result.exit_code == 1 and "not supported" in result.output


# ---- the file ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_file_loads_with_the_existing_loader_and_resolve_tags(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    qs = load_question_set(r.path)
    assert qs.version == "question_set_draft_v1" and len(qs.items) == 32
    tags, warnings = resolve_tags(qs, biz["name"], biz["domain"])
    assert warnings == [] and sum(t["type"] == "brand" for t in tags) == 8 and all(t["type_source"] == "tag" for t in tags)
    assert [t["length"] for t in tags] == [i["length"] for i in file_items(r)]        # the tags are what the loader sees
    read = dossier_questions.read_questions(r.path)                                    # and the dossier reads it too
    assert len(read["items"]) == 32 and sum(i["spot_check"] for i in read["items"]) == 8


def test_the_header_says_draft_not_frozen_and_where_it_came_from(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    head = "\n".join(r.path.read_text().splitlines()[:8])
    assert "DRAFT v1 (NOT FROZEN)" in head and "model claude-sonnet-5" in head and "prompt questions_draft/v2" in head
    assert datetime.now().date().isoformat() in head or datetime.now(timezone.utc).date().isoformat() in head
    assert "diagnosis before of" in head and "website only, every fact unconfirmed" in head and "never writes there" in head


def test_drafts_are_versioned_and_an_earlier_one_is_never_touched(biz, tmp_path):
    folder = client_files.client_dir(biz["domain"], tmp_path / "clients")
    folder.mkdir(parents=True)
    old = {"question_set_draft_v0.yaml": "# hand made\nquestions:\n  - a\n", "question_set_draft_v0.md": "notes\n"}
    for name, body in old.items():
        (folder / name).write_text(body)
    diagnosed(biz, tmp_path)
    first = run(biz, tmp_path, fake(clean_set(biz, words=Words(1))))
    assert first.version == 1 and first.path.name == "question_set_draft_v1.yaml"
    v1_bytes = first.path.read_bytes()
    second = run(biz, tmp_path, fake(clean_set(biz, words=Words(2))))
    assert second.version == 2 and second.path.read_bytes() != v1_bytes and first.path.read_bytes() == v1_bytes
    assert all((folder / n).read_text() == body for n, body in old.items())                # the hand-made v0 is untouched
    assert "DRAFT v2" in second.path.read_text()


def test_a_frozen_set_is_never_touched_or_replaced(biz, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    frozen_dir = tmp_path / "question_sets"
    frozen_dir.mkdir()
    frozen = frozen_dir / "acme_v1.yaml"
    frozen.write_text("questions:\n  - \"a frozen question\"\n")
    before = hashlib.sha256(frozen.read_bytes()).hexdigest()
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    run(biz, tmp_path, fake(clean_set(biz, words=Words(4))))
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == before and [p.name for p in frozen_dir.iterdir()] == ["acme_v1.yaml"]
    assert frozen_dir not in r.path.parents and "question_sets" not in str(r.path)


def test_the_run_is_noted_in_the_journal(biz, tmp_path):
    diagnosed(biz, tmp_path)
    run(biz, tmp_path, fake(clean_set(biz)))
    conn = get_connection()
    try:
        details = conn.execute("SELECT details FROM client_journal WHERE business_id = %s AND actor = 'aeo questions draft'",
                               (biz["id"],)).fetchone()[0]
    finally:
        conn.close()
    assert details["draft"] == "question_set_draft_v1.yaml" and details["model"] == "claude-sonnet-5" and details["prompt_version"] == "v2"


# ---- the command ----------------------------------------------------------------------------------------------------------------------------------

def test_the_command_cleans_up_the_languages_and_passes_the_options(biz, tmp_path, monkeypatch):
    captured = {}

    def stub(conn, business, **kw):
        captured.update(kw)
        raise DraftError("stop here")
    monkeypatch.setattr(cli.qdraft, "draft_questions", stub)
    result = CliRunner().invoke(cli.app, ["questions", "draft", biz["domain"], "--languages", "EN, fr,en", "--size", "20", "--note", "serves Quebec only"])
    assert captured["languages"] == ["en", "fr"] and captured["size"] == 20 and captured["note"] == "serves Quebec only"
    assert result.exit_code == 1 and "stop here" in result.output
    bad = CliRunner().invoke(cli.app, ["questions", "draft", biz["domain"], "--languages", "english"])
    assert bad.exit_code == 1 and "short codes" in bad.output


def test_a_size_that_is_too_small_is_a_plain_error(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    with pytest.raises(DraftError, match="between 12 and 100"):
        run(biz, tmp_path, client, size=8)
    assert client.messages.calls == []
