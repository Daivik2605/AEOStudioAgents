"""Step 25d: a draft keeps every judgement call (flagged), drops only what would corrupt the numbers, tops up one
length bucket at most once, never makes more than three model calls, shows an honest cost card, and `aeo add` links
diagnoses made before the business existed. The model is the scripted fake; the database is the real test database."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from audit.question_set import QuestionSetError, load_question_set, resolve_tags
from core import businesses
from core.db import get_connection
from questions_draft import plan, run as qrun
from tests.conftest import FakeResponse, FakeUsage
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_questions_draft import (Words, clean_set, diagnosed, fake, file_items, person_at_keyboard,  # noqa: F401
                                        q, reply, run, sent_to_model)


def spot(items):
    return [i for i in items if i["spot_check"]]


# ---- 1. keep what is a judgement call -----------------------------------------------------------------------------------------------

def test_a_soft_flagged_question_is_in_the_file_with_its_flag_and_never_in_the_spot_check(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    claim = q(f"Does {biz['name']} offer water stations for festivals?", kind="brand")
    items[10] = claim                                                      # a brand slot, first in file order among brand
    r = run(biz, tmp_path, fake(items))
    saved = file_items(r)
    flagged = next(i for i in saved if i["text"] == claim["text"])
    assert flagged["needs_review"] is True and flagged["review_reason"] and flagged["spot_check"] is False
    assert all(not i["spot_check"] for i in saved if i.get("needs_review"))
    assert len(spot(saved)) == 8                                           # the eight still come from clean questions
    assert r.rejected == []


def test_flagged_questions_are_never_chosen_even_when_they_come_first(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[0] = q(f"Does {biz['name']} deliver on weekends?", kind="brand")             # flagged, would be first in its group
    items[1] = q(f"Is {biz['name']} open at night?", kind="brand")
    r = run(biz, tmp_path, fake(items))
    assert len(spot(file_items(r))) == 8
    assert not any(i.get("needs_review") and i["spot_check"] for i in file_items(r))
    # the rule itself, with no model: the flagged candidate comes first in every group and is still skipped
    def cand(text, kind, flagged):
        return plan.Candidate(text=text, kind=kind, language="en", intent=None if kind == "brand" else "commercial",
                              is_target=False, control=False, needs_review=flagged)
    ordered = [cand("flagged d", "discovery", True)] + [cand(f"clean d {i}", "discovery", False) for i in range(6)] \
        + [cand("flagged b", "brand", True)] + [cand(f"clean b {i}", "brand", False) for i in range(4)]
    chosen = plan.choose_spot_check(ordered, ["en"], None)
    assert len(chosen) == 8 and not any(ordered[i].needs_review for i in chosen)


def test_the_loader_reads_needs_review_and_review_reason(tmp_path):
    f = tmp_path / "set_v1.yaml"
    f.write_text('questions:\n  - {"text": "a", "needs_review": true, "review_reason": "check this"}\n  - "b"\n')
    qs = load_question_set(f)
    assert qs.items[0].needs_review is True and qs.items[0].review_reason == "check this" and qs.items[1].needs_review is False
    assert resolve_tags(qs, "Zed", "zed.example")[0][0]["needs_review"] is True
    plain = tmp_path / "plain_v1.yaml"
    plain.write_text('questions:\n  - {"text": "a"}\n  - "b"\n')
    assert load_question_set(plain).fingerprint != qs.fingerprint            # the flag is part of what the set says


@pytest.mark.parametrize("entry", ['{"text": "a", "needs_review": "yes"}', '{"text": "a", "needs_review": true, "review_reason": 5}'])
def test_the_loader_refuses_a_badly_typed_flag(tmp_path, entry):
    f = tmp_path / "set_v1.yaml"
    f.write_text(f"questions:\n  - {entry}\n")
    with pytest.raises(QuestionSetError):
        load_question_set(f)


def test_the_saved_draft_passes_the_audit_loader_with_flags_in_it(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[10] = q(f"Does {biz['name']} serve festivals?", kind="brand")
    r = run(biz, tmp_path, fake(items))
    qs = load_question_set(r.path)
    assert sum(1 for x in qs.items if x.needs_review) == 1 and '"needs_review": true' in r.path.read_text()
    assert "1 question(s) are flagged needs_review: true" in r.path.read_text()


def test_a_discovery_question_naming_the_business_is_retagged_with_target_and_spot_reset(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[0] = q(f"best water stations near {biz['domain']}", is_target=True)
    items[1] = q(f"Compare {biz['name']} and others", is_target=True, control=True, intent="informational")
    r = run(biz, tmp_path, fake(items, [q(Words(41).many(5)), q(Words(42).many(14))]))
    for text in (items[0]["text"], items[1]["text"]):
        i = next(x for x in file_items(r) if x["text"] == text)
        assert i["type"] == "brand" and not i["is_target"] and not i["control"] and not i["spot_check"] and i["needs_review"]
        assert "intent" not in i
    assert r.rejected == [] and len(file_items(r)) == 34 and any("more than the 32 asked for" in w for w in r.warnings)


def test_exact_duplicates_are_dropped_and_listed_in_the_summary(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[1] = dict(items[0])
    r = run(biz, tmp_path, fake(items, []))
    assert len(r.rejected) == 1 and "exact repeat" in r.rejected[0].reasons[0]
    out = "\n".join(qrun.summary_lines(r))
    assert "Dropped, not in the file: 1" in out and items[0]["text"] in out


def test_tags_are_repaired_where_clear_and_flagged_where_not(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[0] = {**items[0], "kind": "Discovery", "language": "EN", "intent": "Commercial", "is_target": "yes"}   # clear
    items[1] = {**items[1], "kind": "other"}                                                                      # worked out from the text
    items[2] = {**items[2], "language": "de"}                                                                     # two languages: cannot tell
    items[3] = {**items[3], "intent": "sales"}
    items[4] = {**items[4], "relies_on_fact_ids": "none"}
    items[5] = {**items[5], "text": ""}                                                                           # nothing to keep
    r = run(biz, tmp_path, fake(items, []))
    saved = {i["text"]: i for i in file_items(r)}
    fixed = saved[items[0]["text"]]
    assert fixed["type"] == "discovery" and fixed["language"] == "en" and fixed["intent"] == "commercial" and fixed["is_target"] is True
    assert "needs_review" not in fixed and r.repairs and any("Discovery" in x for x in r.repairs)
    for idx in (1, 2, 3, 4):
        assert saved[items[idx]["text"]]["needs_review"] is True and saved[items[idx]["text"]]["review_reason"]
    assert saved[items[2]["text"]]["language"] is None
    assert len(r.rejected) == 1 and "no question text" in r.rejected[0].reasons[0]


def test_a_brand_question_with_an_intent_or_control_flag_is_repaired(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    i = next(n for n, x in enumerate(items) if x["kind"] == "brand")
    items[i] = {**items[i], "intent": "commercial", "control": True}
    r = run(biz, tmp_path, fake(items))
    fixed = next(x for x in file_items(r) if x["text"] == items[i]["text"])
    assert "intent" not in fixed and fixed["control"] is False and "needs_review" not in fixed and r.rejected == []


def test_the_summary_prints_the_three_groups(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[10] = q(f"Does {biz['name']} serve festivals?", kind="brand")
    items[1] = dict(items[0])
    out = "\n".join(qrun.summary_lines(run(biz, tmp_path, fake(items, []))))
    assert "1. Kept, clean:" in out and "2. Kept and FLAGGED for review" in out and "3. Dropped, not in the file: 1" in out
    assert "states a claim" in out and "exact repeat" in out


# ---- 2. the length mix --------------------------------------------------------------------------------------------------------------

def test_the_prompt_and_the_payload_state_the_exact_count_per_length(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    run(biz, tmp_path, client)
    assert sent_to_model(client)["length_aim"] == {"short": 13, "medium": 13, "long": 6}
    system = qrun.DRAFT_AGENT.prompt_path.read_text()
    assert '"length_aim": the exact number of short, medium and long questions' in system and "exact counts" in system
    for size in (12, 40, 100):
        t = plan.make_targets(size, ["en"]).length_aim
        assert sum(t.values()) == size and abs(t["short"] - size * 0.4) <= 1 and abs(t["long"] - size * 0.2) <= 1


def lopsided(b: dict) -> list[dict]:
    """A reply with almost no short questions: every discovery is medium except one per language."""
    w = Words(3)
    out = []
    for lang in ("en", "fr"):
        out += [q(w.many(5 if i == 0 else 14), language=lang, is_target=i < 2) for i in range(10)]
        out += [q(w.many(14) + "?", language=lang, intent="informational", control=True) for _ in range(2)]
        out += [q(t.format(n=b["name"], d=b["domain"]) + " " + w.many(8), kind="brand", language=lang)
                for t in ("What does {n} do?", "Tell me about {d}", "Where is {n} based?", "Who are the customers of {n}?")]
    return out


def test_a_bucket_far_under_target_gets_one_top_up_asking_only_for_it(biz, tmp_path):
    diagnosed(biz, tmp_path)
    first = lopsided(biz)
    counts = plan.length_counts(plan.check_questions(first, name=biz["name"], domain=biz["domain"], alternates=[],
                                                     languages=["en", "fr"], confirmed_fact_ids=set(), already=[])[0])
    assert plan.make_targets(32, ["en", "fr"]).length_aim["short"] - counts["short"] > plan.TOPUP_GAP
    w = Words(77)
    request = plan.topup_request(plan.check_questions(first, name=biz["name"], domain=biz["domain"], alternates=[],
                                                      languages=["en", "fr"], confirmed_fact_ids=set(), already=[])[0],
                                 plan.make_targets(32, ["en", "fr"]))
    assert request["length"] == "short"
    new = []
    for place in request["places"]:
        new += [q(w.many(4), kind=place["group"], language=place["language"]) if place["group"] == "discovery"
                else q(f"{biz['name']} {w.many(2)}", kind="brand", language=place["language"]) for _ in range(place["count"])]
    client = fake(first, new)
    r = run(biz, tmp_path, client)
    assert r.calls == 2 and len(client.messages.calls) == 2 and r.topped_up == "short"
    topup = sent_to_model(client, 1)
    assert "retry" not in topup and topup["topup"]["length"] == "short" and topup["topup"]["count"] == request["count"]
    after = {k: sum(1 for i in file_items(r) if i["length"] == k) for k in plan.LENGTHS}
    assert after["short"] > counts["short"] and len(file_items(r)) == 32
    assert len(r.surplus) == request["count"]                                   # what was replaced is reported, not hidden


def test_a_mix_that_still_misses_keeps_the_warning_and_is_not_padded(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(lopsided(biz), []))                              # the top-up gives nothing
    assert r.topped_up == "short" and len(file_items(r)) == 32
    assert any(w.startswith("Length mix:") and "short" in w for w in r.warnings)


def test_a_clean_mix_makes_no_top_up(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    assert r.calls == 1 and r.topped_up is None


def run_count(biz) -> int:
    conn = get_connection()
    try:
        return conn.execute("SELECT count(*) FROM runs WHERE business_id = %s AND agent = 'questions_draft'", (biz["id"],)).fetchone()[0]
    finally:
        conn.close()


def test_the_whole_command_never_makes_more_than_three_model_calls(biz, tmp_path):
    """The first reply is not valid JSON, so run_agent's own retry is call 2. The dropped question then needs the command's
    retry, call 3. The lopsided mix would want a top-up, but the three calls are spent."""
    diagnosed(biz, tmp_path)
    bad = lopsided(biz)
    bad[0] = {**bad[0], "text": ""}
    client = fake("this is not json", bad, [q(Words(5).many(4))])        # a fourth reply is not queued: asking would fail the test
    r = run(biz, tmp_path, client)
    assert len(client.messages.calls) == 3 and run_count(biz) == 3 and r.topped_up is None and r.cost["calls"] == 3


def test_a_top_up_that_fails_is_a_warning_and_the_calls_stay_at_three(biz, tmp_path):
    diagnosed(biz, tmp_path)
    bad = lopsided(biz)
    bad[0] = {**bad[0], "text": ""}
    client = fake(bad, [q(Words(8).many(14))], "not json at all")
    r = run(biz, tmp_path, client)
    assert len(client.messages.calls) == 3 and run_count(biz) == 3
    assert any("top-up request did not return a usable reply" in w for w in r.warnings)


# ---- 3. the cost card -------------------------------------------------------------------------------------------------------------------

class MaxedOut:
    """Wraps a fake client so every call reports the biggest honest usage: a request-sized input (3 characters a token)
    and the whole output cap."""
    def __init__(self, inner):
        self.inner, self.messages = inner, self
        self.cost = 0.0

    @property
    def calls(self):
        return self.inner.messages.calls

    def create(self, **kwargs):
        got = self.inner.messages.create(**kwargs)
        tokens_in = (len(kwargs.get("system") or "") + len(json.dumps(kwargs["messages"]))) // 3
        return FakeResponse(content=got.content, stop_reason=got.stop_reason,
                            usage=FakeUsage(input_tokens=tokens_in, output_tokens=qrun.DRAFT_AGENT.max_tokens))


def figure(said, label):
    line = next(s for s in said if label in s)
    return float(line.split("$")[1].split()[0].rstrip(".;)"))


@pytest.fixture
def own_history(monkeypatch):
    """The typical figure averages every earlier run of the command in the shared test database; give this test its own."""
    monkeypatch.setattr(qrun, "ACTOR", f"aeo questions draft test {uuid.uuid4().hex}")


def test_the_card_shows_calls_input_size_output_cap_and_an_up_to_figure(biz, tmp_path, own_history):
    diagnosed(biz, tmp_path)
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said)
    card = "\n".join(said)
    assert "Up to 3 calls" in card and "Input about" in card and "output capped at 8,000 tokens per call" in card
    assert "Up to $" in card and "Typical: not known yet" in card


def test_the_real_cost_of_a_three_call_run_never_exceeds_the_up_to_figure(biz, tmp_path):
    diagnosed(biz, tmp_path)
    bad = lopsided(biz)
    bad[0] = {**bad[0], "text": ""}
    request_reply = [q(Words(9).many(4), language="en")]
    said: list = []
    client = MaxedOut(fake(bad, [q(Words(8).many(14))], request_reply))
    r = run(biz, tmp_path, client, said=said)
    assert r.cost["calls"] == 3
    assert r.cost["cost_usd"] <= figure(said, "Up to $") + 1e-9
    assert r.cost["cost_usd"] > 0.1                                              # three maxed-out calls really are expensive


def test_the_up_to_figure_also_covers_a_reply_that_needs_run_agents_own_retry(biz, tmp_path):
    diagnosed(biz, tmp_path)
    said: list = []
    client = MaxedOut(fake("garbage", lopsided(biz), [q(Words(4).many(4))]))
    r = run(biz, tmp_path, client, said=said)
    assert r.cost["calls"] <= 3 and r.cost["cost_usd"] <= figure(said, "Up to $") + 1e-9


def test_the_typical_figure_comes_from_recent_logged_runs(biz, tmp_path, own_history):
    diagnosed(biz, tmp_path)
    first: list = []
    r1 = run(biz, tmp_path, fake(clean_set(biz)), said=first)
    assert any("Typical: not known yet" in s for s in first)
    second: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=second)
    card = "\n".join(second)
    assert f"Typical ${r1.cost['cost_usd']:.3f}" in card and "average of the last 1 run(s)" in card


# ---- 4. linked diagnoses -----------------------------------------------------------------------------------------------------------------

def bare_diagnosis(url: str) -> str:
    conn = get_connection()
    try:
        return str(conn.execute("INSERT INTO diagnoses (business_id, url, verdict, checked_at) VALUES (NULL, %s, 'serve', %s) RETURNING id",
                                (url, datetime.now(timezone.utc))).fetchone()[0])
    finally:
        conn.close()


def owner_of(diagnosis_id: str):
    conn = get_connection()
    try:
        return conn.execute("SELECT business_id FROM diagnoses WHERE id = %s", (diagnosis_id,)).fetchone()[0]
    finally:
        conn.close()


def test_aeo_add_links_earlier_diagnoses_of_the_same_domain_and_nothing_else():
    host = f"link-{uuid.uuid4().hex[:10]}.example"
    same = [bare_diagnosis(f"https://{host}/"), bare_diagnosis(f"https://www.{host}/about")]
    other = bare_diagnosis(f"https://shop.{host}/")                             # a subdomain: a different address
    elsewhere = bare_diagnosis(f"https://other-{uuid.uuid4().hex[:10]}.example/")
    result = businesses.add_business(f"https://{host}", name=f"Link {uuid.uuid4().hex[:8]}")
    assert result["linked_diagnoses"] == 2
    for d in same:
        assert str(owner_of(d)) == result["id"]
    assert owner_of(other) is None and owner_of(elsewhere) is None
    conn = get_connection()
    try:
        notes = conn.execute("SELECT count(*) FROM client_journal WHERE business_id = %s AND details->>'linked_earlier_diagnosis' = 'true'",
                             (result["id"],)).fetchone()[0]
        total = conn.execute("SELECT count(*) FROM diagnoses WHERE id = ANY(%s::uuid[])", (same + [other, elsewhere],)).fetchone()[0]
    finally:
        conn.close()
    assert notes == 2 and total == 4                                            # nothing deleted


def test_a_diagnosis_already_owned_by_another_business_is_never_taken(biz):
    conn = get_connection()
    try:
        owned = str(conn.execute("INSERT INTO diagnoses (business_id, url, verdict) VALUES (%s, %s, 'serve') RETURNING id",
                                 (biz["id"], f"https://{biz['domain']}/")).fetchone()[0])
        host = f"mine-{uuid.uuid4().hex[:10]}.example"
        conn.execute("INSERT INTO diagnoses (business_id, url, verdict) VALUES (%s, %s, 'serve')", (biz["id"], f"https://{host}/"))
    finally:
        conn.close()
    result = businesses.add_business(f"https://{host}", name=f"Mine {uuid.uuid4().hex[:8]}")
    assert result["linked_diagnoses"] == 0 and str(owner_of(owned)) == biz["id"]


def test_aeo_add_says_how_many_were_linked():
    from typer.testing import CliRunner
    from aeo import cli
    host = f"say-{uuid.uuid4().hex[:10]}.example"
    bare_diagnosis(f"https://{host}/")
    out = CliRunner().invoke(cli.app, ["add", f"https://{host}", "--name", f"Say {uuid.uuid4().hex[:8]}"])
    assert out.exit_code == 0 and "Linked 1 earlier diagnosis" in out.output


def test_the_draft_then_uses_the_linked_diagnosis_and_never_fetches_the_homepage(tmp_path):
    host = f"draft-{uuid.uuid4().hex[:10]}.example"
    conn = get_connection()
    try:
        did = conn.execute("INSERT INTO diagnoses (business_id, url, verdict, checked_at, checkpoint) VALUES (NULL, %s, 'serve', %s, 'before') RETURNING id",
                           (f"https://{host}/", datetime.now(timezone.utc))).fetchone()[0]
    finally:
        conn.close()
    b = businesses.add_business(f"https://{host}", name=f"Drafty {uuid.uuid4().hex[:8]}")
    assert b["linked_diagnoses"] == 1
    from core import client_files
    from tests.helpers_diagnose import page
    folder = client_files.evidence_dir(host, tmp_path / "clients")           # where `aeo diagnose` leaves the page for a bare address
    folder.mkdir(parents=True)
    (folder / f"{datetime.now(timezone.utc).date().isoformat()}_raw_page_run1.html").write_text(page(300, h1="Water stations for events"))

    class NoFetch:
        def get(self, *a, **k):
            raise AssertionError("the homepage must not be fetched when a linked diagnosis has a saved page")

    client = fake(clean_set({"name": b["name"], "domain": host}))
    r = run({"id": b["id"], "domain": host, "website_url": b["website_url"], "name": b["name"]}, tmp_path, client, fetcher=NoFetch())
    assert any("latest diagnosis" in s for s in r.source_lines) and str(did) in r.path.read_text()
