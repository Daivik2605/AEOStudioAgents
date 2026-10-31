"""Step 25h: a business can have several names. One canonical name, any number of aliases, and the domain (always an alias).

The matching tests need no database and no AI. The record tests use aeo_test. The model is the scripted fake; nothing reaches
the network. The names are the real ones for maximsdentistry.ca, which is where the need came from."""

from __future__ import annotations

import json

import psycopg
import pytest
from typer.testing import CliRunner

from aeo.cli import app
from audit import store
from audit.derive import derive_run
from core import names
from core.db import get_connection
from core.names import Identity, name_key, names_in
from tests.conftest import FakeAnthropicClient, text_response
from tests.test_audit import biz, query  # noqa: F401  (fixture and helper)
from tests.test_questions_draft import clean_set, diagnosed, fake, file_items, run, sent_to_model

CANONICAL = "Maxim’s Family Dentist"
ALIASES = ["Maxim's Family Dentistry", "Maxim's Dentistry"]
DOMAIN = "maximsdentistry.ca"


def maxims() -> Identity:
    return Identity(canonical=CANONICAL, aliases=list(ALIASES), domain=DOMAIN)


# ---- matching: the same business -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("written", [
    "Maxim's Family Dentist",                    # the canonical name
    "Maxim's Family Dentistry",                  # each alias
    "Maxim's Dentistry",
    "maximsdentistry.ca",                        # the domain, as a name
    "MaximsDentistry",                           # the domain without its ending
    "MAXIM’S FAMILY DENTIST",               # capitals and a curly apostrophe
    "Maxims Family Dentist",                     # no apostrophe at all
    "The Maxim's Dentistry, Inc.",               # "the" in front, a legal ending behind
    "Maxim’s Family Dentistry Ltd",
    "Maxim's  Family\tDentist",                  # stray spaces
])
def test_the_canonical_name_each_alias_and_the_domain_are_the_same_business(written):
    assert maxims().match(written) is not None


@pytest.mark.parametrize("name, domain", [
    ("Some Dental Clinic", "maximsdentistry.ca"),              # the answer gives our domain: it is us whatever the name says
    ("Some Dental Clinic", "https://www.maximsdentistry.ca/book?x=1"),
    (None, "WWW.MaximsDentistry.ca"),
])
def test_the_domain_alone_is_enough(name, domain):
    assert maxims().match(name, domain) == DOMAIN


def test_the_match_says_which_name_it_was():
    ident = maxims()
    assert ident.match("Maxim's Family Dentistry") == "Maxim's Family Dentistry"
    assert ident.match("maxims dentistry") == "Maxim's Dentistry"
    assert ident.match("maxim’s family dentist") == CANONICAL


def test_an_earlier_canonical_name_is_still_recognised():
    ident = Identity(canonical="Maxim's Family Dentist", aliases=[], domain=DOMAIN, former=["Maxim's Dentistry"])
    assert ident.match("Maxim's Dentistry") == "Maxim's Dentistry"


# ---- matching: another business (the strict part) ----------------------------------------------------------------------------------

@pytest.mark.parametrize("name, domain", [
    ("Maxim's Family Dental", None),                    # one word different: another clinic
    ("Maxim's Family Dental Care", None),
    ("Maxim's Dentistry Plus", None),                   # ours with something added
    ("Maxim's Family Dentist of Oakville", None),       # same words, then a place: a different practice
    ("Maximus Dentistry", None),                        # a similar name
    ("Maxim Dentistry", None),
    ("Maxim's Family", None),                           # part of our name
    ("Family Dentist", None),
    ("Dentistry", None),
    ("Royal Oak Smiles", None),                         # the name on the site's markup that is not an alias
    ("Some Dental Clinic", "maximsdentistry.com"),      # a look-alike domain
    ("Some Dental Clinic", "notmaximsdentistry.ca"),
    ("Some Dental Clinic", "book.maximsdentistry.ca"),  # a subdomain is not the same address
    ("", None),
    (None, None),
])
def test_a_similar_name_or_look_alike_domain_is_a_different_business(name, domain):
    assert maxims().match(name, domain) is None


def test_a_short_domain_word_is_not_used_to_match_by_accident():
    # "ace" is the whole first part of ace.ca, too short to count as a name on its own.
    assert Identity(canonical="Ace Hardware", aliases=[], domain="ace.ca").match("Ace") is None


def test_a_business_with_no_name_is_found_by_its_domain_only():
    ident = Identity(canonical=None, aliases=[], domain=DOMAIN)
    assert ident.match("Maxim's Dentistry") == DOMAIN          # the bare domain word is the same words
    assert ident.match("Maxim's Family Dentistry") is None
    assert ident.match(None, "https://maximsdentistry.ca") == DOMAIN


def test_name_key_tidies_the_writing_and_nothing_else():
    assert name_key("The Maxim’s Dentistry, Inc.") == name_key("maxims dentistry") == "maxims dentistry"
    assert name_key("Café Rouge") == "cafe rouge"
    assert name_key("A & B Plumbing") == "a and b plumbing"
    assert name_key("Maxim's Dentistry Plus") != name_key("Maxim's Dentistry")
    assert name_key(None) == "" and name_key("   ") == ""


def test_names_in_counts_whole_words_and_the_longest_name():
    both = [CANONICAL, *ALIASES]
    assert names_in("Is Maxim's Family Dentistry open on Sundays?", both) == ["Maxim's Family Dentistry"]
    assert names_in("What is Maxim's Family Dentist?", both) == [CANONICAL]       # "Dentist" is not inside "Dentistry"
    assert set(names_in("Compare Maxim's Dentistry and Maxim's Family Dentist", both)) == {"Maxim's Dentistry", CANONICAL}
    assert names_in("Is Maxim's Dentistry Plus open?", ["Maxim's Dentistry", "Maxim's Dentistry Plus"]) == ["Maxim's Dentistry Plus"]
    assert names_in("Is Maximus Dentistry open?", both) == []


# ---- the record: names with history -------------------------------------------------------------------------------------------------

def test_set_the_canonical_name_and_aliases_and_nothing_is_overwritten(biz):
    original = biz["name"]
    conn = get_connection()
    try:
        done = names.set_names(conn, biz["id"], canonical=CANONICAL, aliases=ALIASES, operator="Sam")
        ident = names.get_identity(conn, biz["id"])
        trail = names.history(conn, biz["id"])
    finally:
        conn.close()
    assert done["canonical"] == {"changed": True, "from": original, "to": CANONICAL}
    assert done["aliases_added"] == ALIASES
    assert (ident.canonical, ident.aliases, ident.former) == (CANONICAL, ALIASES, [original])
    assert query("SELECT name FROM businesses WHERE id = %s", (biz["id"],))[0][0] == CANONICAL     # what every report reads
    # History: the name `aeo add` gave, then the new canonical name, then the aliases, each with who and when.
    assert [(r["name"], r["role"]) for r in trail] == [(original, "canonical"), (CANONICAL, "canonical"),
                                                       (ALIASES[0], "alias"), (ALIASES[1], "alias")]
    assert [r["set_by"] for r in trail][1:] == ["Sam", "Sam", "Sam"] and all(r["when"] for r in trail)


def test_the_old_name_can_be_made_an_alias_on_purpose_and_is_then_listed_as_one(biz):
    """The real case: the record said "Maxim's Dentistry"; the canonical name becomes "Maxim's Family Dentist" and the old name
    is added as an alias in the same command."""
    old = biz["name"]
    conn = get_connection()
    try:
        done = names.set_names(conn, biz["id"], canonical=CANONICAL, aliases=[ALIASES[0], old], operator="Sam")
        ident = names.get_identity(conn, biz["id"])
    finally:
        conn.close()
    assert done["aliases_added"] == [ALIASES[0], old] and ident.aliases == [ALIASES[0], old] and ident.former == []


def test_the_name_a_business_is_added_with_is_the_first_canonical_row(biz):
    rows = query("SELECT name, role, set_by FROM business_names WHERE business_id = %s ORDER BY seq", (biz["id"],))
    assert rows == [(biz["name"], "canonical", "system")]


def test_a_changed_name_made_by_raw_sql_is_still_recorded(biz):
    conn = get_connection()
    try:
        conn.execute("UPDATE businesses SET name = 'Acme Water Co' WHERE id = %s", (biz["id"],))
    finally:
        conn.close()
    assert [r[0] for r in query("SELECT name FROM business_names WHERE business_id = %s ORDER BY seq", (biz["id"],))] \
        == [biz["name"], "Acme Water Co"]


def test_a_saved_name_can_never_be_edited_or_deleted(biz):
    for sql in ("UPDATE business_names SET name = 'x' WHERE business_id = %s", "DELETE FROM business_names WHERE business_id = %s"):
        conn = get_connection()
        try:
            with pytest.raises(psycopg.errors.RaiseException, match="never edited or deleted"):
                conn.execute(sql, (biz["id"],))
        finally:
            conn.close()
    assert query("SELECT count(*) FROM business_names WHERE business_id = %s", (biz["id"],))[0][0] == 1


def test_the_same_name_twice_changes_nothing(biz):
    conn = get_connection()
    try:
        names.set_names(conn, biz["id"], canonical=CANONICAL, aliases=ALIASES, operator="Sam")
        before = names.history(conn, biz["id"])
        again = names.set_names(conn, biz["id"], canonical=CANONICAL, aliases=[*ALIASES, "maxims family dentistry"], operator="Sam")
        after = names.history(conn, biz["id"])
    finally:
        conn.close()
    assert again["canonical"]["changed"] is False and again["aliases_added"] == []
    assert len(again["aliases_already_known"]) == 3
    assert before == after


def test_the_domain_and_the_canonical_name_are_never_stored_as_aliases(biz):
    conn = get_connection()
    try:
        done = names.set_names(conn, biz["id"], aliases=[biz["name"], biz["domain"], f"www.{biz['domain']}"], operator="Sam")
        ident = names.get_identity(conn, biz["id"])
    finally:
        conn.close()
    assert done["aliases_added"] == [] and ident.aliases == []
    assert ident.domain == biz["domain"] and ident.match(biz["domain"]) == biz["domain"]       # the domain is always an alias


def test_an_empty_name_or_nothing_to_set_is_refused(biz):
    conn = get_connection()
    try:
        for kwargs in ({"canonical": "  "}, {"aliases": [""]}, {}):
            with pytest.raises(names.BusinessError):
                names.set_names(conn, biz["id"], operator="Sam", **kwargs)
    finally:
        conn.close()


def test_changing_the_canonical_name_keeps_the_old_one_recognised_and_is_noted_in_the_journal(biz):
    old = biz["name"]
    conn = get_connection()
    try:
        names.set_names(conn, biz["id"], canonical=CANONICAL, operator="Sam")
        ident = names.get_identity(conn, biz["id"])
    finally:
        conn.close()
    assert ident.match(old) == old and ident.match(CANONICAL) == CANONICAL
    notes = [r[0] for r in query("SELECT description FROM client_journal WHERE business_id = %s AND actor = 'aeo names'", (biz["id"],))]
    assert notes == [f"Canonical name set to '{CANONICAL}' (was '{old}')"]


def test_the_command_sets_shows_and_lists_the_history(biz):
    runner = CliRunner()
    out = runner.invoke(app, ["names", "--business", biz["id"], "--canonical", CANONICAL,
                              "--alias", ALIASES[0], "--alias", ALIASES[1]])
    assert out.exit_code == 0, out.output
    assert f"Canonical name: {CANONICAL}" in out.output and f"Alias added: {ALIASES[0]}" in out.output
    assert f"was: {biz['name']}; kept in the history" in out.output
    shown = runner.invoke(app, ["names", "--business", biz["domain"]])                       # no options: just show
    assert shown.exit_code == 0
    assert f"Aliases: {ALIASES[0]}, {ALIASES[1]}; and the domain, {biz['domain']}" in shown.output
    assert f"Earlier canonical names (still recognised): {biz['name']}" in shown.output
    full = runner.invoke(app, ["names", "--business", biz["id"], "--history"])
    assert full.output.count("canonical ") >= 2 and f"alias     {ALIASES[0]}" in full.output
    nothing = runner.invoke(app, ["names", "--business", biz["id"], "--alias", "  "])
    assert nothing.exit_code == 1 and "cannot be empty" in nothing.output


# ---- reports, the dossier and compare show the canonical name ------------------------------------------------------------------------

def test_every_loader_that_reports_use_gives_the_canonical_name_and_the_aliases(biz):
    from diagnose.store import load_business
    conn = get_connection()
    try:
        names.set_names(conn, biz["id"], canonical=CANONICAL, aliases=ALIASES, operator="Sam")
        loaded = store.load_business_and_profile(conn, biz["id"])
        diag = load_business(conn, biz["id"])
    finally:
        conn.close()
    assert loaded["name"] == diag["name"] == CANONICAL
    assert loaded["aliases"] == diag["aliases"] == ALIASES
    assert set(ALIASES) <= set(diag["alternate_names"])                    # the diagnosis and the draft checks see them too


def test_the_dossier_data_carries_the_canonical_name_and_the_aliases(biz):
    from dossier import data as dossier_data
    conn = get_connection()
    try:
        names.set_names(conn, biz["id"], canonical=CANONICAL, aliases=ALIASES, operator="Sam")
        ctx = dossier_data.load(conn, {"id": biz["id"]})
    finally:
        conn.close()
    assert ctx["name"] == CANONICAL and set(ALIASES) <= set(ctx["alternates"])


# ---- the answer extraction: the code decides which business is ours ----------------------------------------------------------------------

def a_run_with_answers(business_id: str, n: int) -> tuple[dict, object, list]:
    conn = get_connection()
    business = store.load_business_and_profile(conn, business_id)
    run_id = store.create_run(conn, business=business, checkpoint=None)
    ids = [store.add_result(conn, probe_run_id=run_id, question=f"question {i}", engine="chatgpt", raw_answer=f"answer {i}",
                            retrieval_activated=None, engine_version=None, logged_in_state="logged_out",
                            location_context=None, question_set_version="t_v1")[0] for i in range(n)]
    return business, run_id, ids, conn


def extract_reply(*named):
    """One scripted extraction reply. Each item: (name written in the answer, domain or None, what the MODEL says is_client)."""
    return text_response(json.dumps({"businesses": [
        {"name": n, "domain": d, "is_client": c, "recommended": False, "position": i, "reason": None, "descriptors": []}
        for i, (n, d, c) in enumerate(named, start=1)]}))


def stored(conn, result_id):
    return conn.execute("SELECT businesses_named, recognized, named_any_business FROM probe_results WHERE id = %s",
                        (result_id,)).fetchone()


def test_the_extraction_matches_the_canonical_name_each_alias_and_the_domain(biz):
    c = get_connection()
    try:
        names.set_names(c, biz["id"], canonical=CANONICAL, aliases=ALIASES, operator="Sam")
    finally:
        c.close()
    # Four answers: the model said "not the client" every time, even though each one names us under one of our names.
    business, run_id, ids, conn = a_run_with_answers(biz["id"], 4)
    try:
        client = FakeAnthropicClient([extract_reply(("Rival Dental", None, False), ("Maxim's Family Dentist", None, False)),
                                      extract_reply(("Maxim's Family Dentistry", None, False)),
                                      extract_reply(("Maxim's Dentistry", None, False)),
                                      extract_reply(("The dentist at the clinic", biz["domain"], False))])
        report = derive_run(conn, business, run_id, client=client)
        rows = [stored(conn, i) for i in ids]
    finally:
        conn.close()
    assert report.extracted == 4
    for named, recognized, any_named in rows:
        assert recognized is True and any_named is True
        assert [b["is_client"] for b in named].count(True) == 1
    assert [next(b["matched_as"] for b in r[0] if b["is_client"]) for r in rows] == [CANONICAL, ALIASES[0], ALIASES[1], biz["domain"]]
    assert all(b["client_by_model"] is False for r in rows for b in r[0])              # what the model said is kept beside it


@pytest.mark.parametrize("similar", ["Maxim's Family Dental", "Maxim's Dentistry Plus", "Maximus Dentistry",
                                     "Maxim's Family Dentist of Oakville", "Royal Oak Smiles"])
def test_the_extraction_does_not_match_a_similar_name_even_when_the_model_says_it_is_the_client(biz, similar):
    c = get_connection()
    try:
        names.set_names(c, biz["id"], canonical=CANONICAL, aliases=ALIASES, operator="Sam")
    finally:
        c.close()
    business, run_id, ids, conn = a_run_with_answers(biz["id"], 1)
    try:
        derive_run(conn, business, run_id, client=FakeAnthropicClient([extract_reply((similar, None, True))]))
        named, recognized, any_named = stored(conn, ids[0])
    finally:
        conn.close()
    assert recognized is False and any_named is True                                  # a business was named, but not ours
    assert named[0]["is_client"] is False and named[0]["client_by_model"] is True and named[0]["matched_as"] is None


def test_the_recognition_and_accuracy_figures_follow_the_rule_not_the_model(biz):
    """Recognition counts answers that name us; the claim check (accuracy) runs only for those. An alias counts; a near-miss does not."""
    from tests.test_audit import confirm_facts
    confirm_facts(biz, services="Dental implants")
    c = get_connection()
    try:
        names.set_names(c, biz["id"], aliases=["Acme Refill Pro"], operator="Sam")
    finally:
        c.close()
    business, run_id, ids, conn = a_run_with_answers(biz["id"], 2)
    claims = text_response(json.dumps({"claims": [{"claim": "offers implants", "field": "Services", "verdict": "correct", "note": "n"}]}))
    try:
        client = FakeAnthropicClient([extract_reply(("Acme Refill Pro", None, False)), claims,
                                      extract_reply(("Acme Refill Plus", None, True))])
        report = derive_run(conn, business, run_id, client=client)
        first, second = stored(conn, ids[0]), stored(conn, ids[1])
        claim_rows = [conn.execute("SELECT claims_checked FROM probe_results WHERE id = %s", (i,)).fetchone()[0] for i in ids]
    finally:
        conn.close()
    assert (first[1], second[1]) == (True, False)
    assert report.claims_checked == 1 and report.claims_skipped == 1
    assert claim_rows[0] is not None and claim_rows[1] is None
    sent = json.loads(client.messages.calls[1]["messages"][0]["content"])               # the claim check is told every name
    assert sent["client_name"] == business["name"] and sent["client_aliases"] == ["Acme Refill Pro"]


def test_the_extraction_is_told_the_canonical_name_the_aliases_and_the_domain(biz):
    c = get_connection()
    try:
        names.set_names(c, biz["id"], canonical=CANONICAL, aliases=ALIASES, operator="Sam")
    finally:
        c.close()
    business, run_id, ids, conn = a_run_with_answers(biz["id"], 1)
    try:
        client = FakeAnthropicClient([extract_reply()])
        derive_run(conn, business, run_id, client=client)
    finally:
        conn.close()
    sent = json.loads(client.messages.calls[0]["messages"][0]["content"])
    assert sent["client"] == {"name": CANONICAL, "aliases": ALIASES, "domain": biz["domain"]}


# ---- the question draft: the aliases are used, and only the aliases ---------------------------------------------------------------------

ALIAS_A, ALIAS_B = "Acme Refill Pro", "Acme Refills"


def set_aliases(biz, *aliases):
    conn = get_connection()
    try:
        names.set_names(conn, biz["id"], aliases=list(aliases), operator="Sam")
    finally:
        conn.close()


def brand_with(items, name, replacements):
    """Swaps the business name for an alias in the English brand questions that start with the given words (the fake model
    'using' the alias)."""
    out = []
    for it in items:
        for start, alias in replacements.items():
            if it["kind"] == "brand" and it["language"] == "en" and it["text"].startswith(start):
                it = {**it, "text": it["text"].replace(name, alias)}
        out.append(it)
    return out


def test_the_model_is_sent_the_canonical_name_and_every_alias(biz, tmp_path):
    set_aliases(biz, ALIAS_A, ALIAS_B)
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    run(biz, tmp_path, client)
    assert sent_to_model(client)["business"]["name"] == biz["name"]
    assert sent_to_model(client)["business"]["aliases"] == [ALIAS_A, ALIAS_B]


def test_a_draft_with_every_alias_in_some_brand_question_needs_no_extra_call(biz, tmp_path):
    set_aliases(biz, ALIAS_A, ALIAS_B)
    diagnosed(biz, tmp_path)
    items = brand_with(clean_set(biz), biz["name"], {"What are the opening hours of": ALIAS_A, "Who works at": ALIAS_B})
    r = run(biz, tmp_path, fake(items))
    brand = [i["text"] for i in file_items(r) if i["type"] == "brand"]
    assert r.calls == 1 and not any("is in no brand question" in w for w in r.warnings)
    for alias in (ALIAS_A, ALIAS_B):
        assert any(names_in(t, [biz["name"], ALIAS_A, ALIAS_B]) == [alias] for t in brand)
    assert sum(biz["name"] in t for t in brand) >= 3                                  # most still use the canonical name


def test_an_alias_no_brand_question_uses_is_asked_for_once_and_swapped_in(biz, tmp_path):
    set_aliases(biz, ALIAS_A, ALIAS_B)
    diagnosed(biz, tmp_path)
    first = clean_set(biz)                                                         # every brand question uses the canonical name
    from tests.test_questions_draft import q
    second = [q(f"Does {ALIAS_A} offer same day visits?", kind="brand", fact_topic="availability"),
              q(f"Is {ALIAS_B} accepting new customers?", kind="brand", fact_topic="services")]
    client = fake(first, second)
    r = run(biz, tmp_path, client)
    retry = sent_to_model(client, 1)["retry"]
    assert retry["names_needed"] == [ALIAS_A, ALIAS_B] and retry["needed"] == []
    items = file_items(r)
    brand = [i["text"] for i in items if i["type"] == "brand" and i["language"] == "en"]
    assert r.calls == 2 and len(items) == 32 and len(brand) == 5                          # the size did not grow
    for alias in (ALIAS_A, ALIAS_B):
        assert any(alias in t for t in brand)


def test_an_alias_still_unused_after_the_one_retry_is_a_warning_and_nothing_is_padded(biz, tmp_path):
    set_aliases(biz, ALIAS_A)
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz), [])                                              # the retry brings nothing
    r = run(biz, tmp_path, client)
    items = file_items(r)
    assert len(items) == 32 and not any(ALIAS_A in i["text"] for i in items)
    assert any(f'"{ALIAS_A}" is in no brand question' in w and "Not padded" in w for w in r.warnings)
    assert r.calls == 2                                                            # one retry, not more


def test_a_business_with_no_aliases_behaves_exactly_as_before(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    r = run(biz, tmp_path, client)
    assert r.calls == 1 and sent_to_model(client)["business"]["aliases"] == []
    assert not any("is in no brand question" in w for w in r.warnings)


def test_a_discovery_question_that_names_an_alias_is_retagged_brand_not_left_as_discovery(biz, tmp_path):
    set_aliases(biz, ALIAS_A)
    diagnosed(biz, tmp_path)
    from tests.test_questions_draft import disc
    items = clean_set(biz)
    items[0] = disc(f"Is {ALIAS_A} a good choice in", need="price")
    r = run(biz, tmp_path, fake(items, []))
    hit = next(i for i in file_items(r) if ALIAS_A in i["text"])
    assert hit["type"] == "brand" and hit.get("needs_review") is True


def test_the_top_up_never_replaces_the_question_that_covers_an_alias():
    from questions_draft.plan import Candidate, Targets, merge_topup
    mk = lambda text: Candidate(text=text, kind="brand", language="en", intent=None, is_target=False, control=False, fact_topic="other")
    keep, other = mk("Is Acme Refill Pro open today and tomorrow and also on the weekend?"), mk("What is Acme?")
    targets = Targets(size=2, discovery=0, control=0, brand=2, by_language={"en": {"discovery": 0, "control": 0, "brand": 2}},
                      length_aim={"short": 2, "medium": 0, "long": 0})
    new = mk("Where is Acme?")
    unprotected, _ = merge_topup([keep, other], [new], targets, "short")
    assert keep not in unprotected                                                  # without the protection it is the one replaced
    items, left_out = merge_topup([keep, other], [new], targets, "short", protect=lambda c: "Refill Pro" in c.text)
    assert keep in items and new not in items and left_out == [new]
