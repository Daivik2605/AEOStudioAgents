"""Step 25e: questions a real customer would type, and brand questions whose answer we can check.
The model is always the scripted fake; nothing here calls an AI or the network."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo import cli
from audit.question_set import FACT_TOPICS, QuestionSetError, load_question_set, resolve_tags
from questions_draft import location as loc
from questions_draft import plan, run as qrun
from questions_draft.run import DraftError, summary_lines
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_questions_draft import (PLACE, Words, clean_set, diagnosed, disc, fake, file_items,  # noqa: F401
                                        person_at_keyboard, q, reply, run, sent_to_model)

ROOT = Path(__file__).resolve().parent.parent
V2_PROMPT_SHA256 = "95219f0afcc7a625b48e456a66222a4f77ea86a9be15f4d9430a42e38b19efad"      # v2 as it was when v3 was made


def ld(city="Leeds", areas=(), **extra) -> str:
    return json.dumps({"@type": "LocalBusiness", "address": {"@type": "PostalAddress", "addressLocality": city},
                       **({"areaServed": list(areas)} if areas else {}), **extra})


def rule(location="Toronto", known=(), by_language=None, corpus="", required=True) -> loc.PlaceRule:
    return loc.PlaceRule(required=required, location=location, known=list(known), by_language=by_language or {}, corpus=corpus)


def check(items, b, place_rule=None, languages=("en",)):
    return plan.check_questions(items, name=b["name"], domain=b["domain"], alternates=[], languages=list(languages),
                                confirmed_fact_ids=set(), already=[], place_rule=place_rule)


# ---- 1. where the business is ---------------------------------------------------------------------------------------------------

def test_the_location_is_read_from_the_structured_data_and_printed(biz, tmp_path):
    diagnosed(biz, tmp_path, jsonld=ld("Leeds", areas=["Headingley"]))
    client, said = fake(clean_set(biz, ["en"])), []
    r = run(biz, tmp_path, client, languages=("en",), size=16, location=None, said=said)
    assert r.place.location == "Leeds" and "structured data" in r.place.source
    assert any(s.startswith("Location: Leeds (the address in the page's structured data)") for s in said)
    assert any("Other places the site names: Headingley" in s for s in said)
    sent = sent_to_model(client)
    assert sent["location"] == "Leeds" and sent["other_places_on_the_site"] == ["Headingley"]
    assert "Location used: Leeds" in r.path.read_text() and "prompt questions_draft/v3" in r.path.read_text()


def test_the_location_is_read_from_an_address_written_in_the_page_text(biz, tmp_path):
    html = ("<!doctype html><html><head><title>t</title></head><body><main><h1>Water</h1><p>" + "word " * 100 + "</p></main>"
            "<footer>Visit us at 120 King Street, Toronto, ON M5H 1A1. Open daily.</footer></body></html>")
    diagnosed(biz, tmp_path, html=html)
    r = run(biz, tmp_path, fake(clean_set(biz, ["en"])), languages=("en",), size=16, location=None)
    assert r.place.location == "Toronto" and "page's text" in r.place.source


@pytest.mark.parametrize("text, expected", [
    ("Find us: 55 Elm Road, Austin, TX 78701", "Austin"),
    ("Find us: 12 Rue Saint-Denis, Montréal, QC H2X 3K8", "Montréal"),
    ("We are open daily. Call 555-1234.", None),
])
def test_an_address_in_text_gives_the_city_or_nothing(text, expected):
    assert loc.find_place([], text, None).location == expected


def test_the_operator_location_wins_over_the_site(biz, tmp_path):
    diagnosed(biz, tmp_path, jsonld=ld("Leeds"))
    client = fake(clean_set(biz, ["en"]))
    r = run(biz, tmp_path, client, languages=("en",), size=16, location="Toronto")
    assert r.place.location == "Toronto" and r.place.source == "given with --location"
    assert sent_to_model(client)["location"] == "Toronto"


def test_a_local_business_with_no_place_stops_and_asks_for_location(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(json.dumps({"local": True, "local_reason": "a clinic with a street address", "questions": []}))
    with pytest.raises(DraftError) as err:
        run(biz, tmp_path, client, location=None)
    assert "--location" in str(err.value) and "never guessed" in str(err.value) and "Nothing was saved" in str(err.value)
    assert len(client.messages.calls) == 1
    assert not list((tmp_path / "clients").glob("**/question_set_draft_v*.yaml"))


def test_when_the_model_cannot_say_and_there_is_no_place_it_stops_too(biz, tmp_path):
    diagnosed(biz, tmp_path)
    with pytest.raises(DraftError, match="--location"):
        run(biz, tmp_path, fake(json.dumps({"questions": []})), location=None)


def test_a_business_that_is_not_local_needs_no_place_and_the_choice_is_shown(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz, ["en"])
    for it in items:
        if it["kind"] == "discovery" and not it["control"]:
            it["text"] = it["text"].removesuffix(f" {PLACE}") + " delivered"
            it["place"] = None
    said: list = []
    r = run(biz, tmp_path, fake(reply(items, local=False)), languages=("en",), size=16, location=None, said=said)
    assert r.local is False and r.rejected == [] and len(file_items(r)) == 16
    assert any("not local" in s and "No place is required" in s for s in said)
    assert "Local business (the model's choice, check it): no" in r.path.read_text()
    assert "not local: no place required" in "\n".join(summary_lines(r))


def test_the_models_choice_is_shown_when_it_says_local(biz, tmp_path):
    diagnosed(biz, tmp_path)
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz, ["en"])), languages=("en",), size=16, said=said)
    assert any("business is local" in s and "must carry a place" in s for s in said)


# ---- 2. discovery questions carry a place -----------------------------------------------------------------------------------------

def test_a_discovery_question_with_no_place_is_rejected_and_replaced(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[0] = q("How much do water stations typically cost?", need="price")        # generic, no place
    items[1] = q("what is a refill station", need="service")                         # educational, no place
    w = Words(61)
    client = fake(items, [disc(w.many(4), need="price"), disc(w.many(13), need="service")])
    said: list = []
    r = run(biz, tmp_path, client, said=said)
    assert r.calls == 2 and r.missing == {}
    assert {x.text for x in r.rejected} == {items[0]["text"], items[1]["text"]}
    assert all("names no place" in x.reasons[0] for x in r.rejected)
    retry = sent_to_model(client, 1)["retry"]
    assert len(retry["rejected"]) == 2 and "place" in retry["rejected"][0]["reasons"][0]
    assert retry["needed"] == [{"group": "discovery", "language": "en", "count": 2}]
    assert not any(i["text"] in (items[0]["text"], items[1]["text"]) for i in file_items(r))
    text = "\n".join(summary_lines(r))
    assert "Dropped, not in the file: 2" in text and "How much do water stations typically cost?" in text


def test_a_set_that_still_misses_keeps_the_good_ones_and_is_not_padded(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    items[0] = q("How much do water stations typically cost?")
    r = run(biz, tmp_path, fake(items, [q("What is a water station?")]))              # the retry has no place either
    assert len(file_items(r)) == 31 and r.missing == {("discovery", "en"): 1}
    assert "STILL MISSING" in "\n".join(summary_lines(r))


def test_controls_are_the_only_questions_allowed_without_a_place(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    controls = [i for i in file_items(r) if i["control"]]
    assert len(controls) == 4 and r.rejected == []
    assert not any(PLACE in i["text"] for i in controls)


@pytest.mark.parametrize("text", ["emergency dentist Toronto", "emergency dentist TORONTO", "dentiste d'urgence toronto",
                                  "dentist open sunday near me", "a dentist nearby for my kid", "où trouver un dentiste près de moi"])
def test_a_place_counts_in_any_case_and_with_near_me(text):
    assert loc.anchor(rule("Toronto"), text) is not None


def test_a_place_matches_without_regard_to_accents():
    assert loc.anchor(rule("Montréal"), "plombier montreal urgence") == "the location"
    assert loc.anchor(rule("Montreal"), "plombier Montréal urgence") == "the location"


@pytest.mark.parametrize("text", ["emergency dentist", "How much do dental implants typically cost?", "best dentist for kids",
                                  "dentist in Torontonian style", "dentist in another city"])
def test_a_question_without_the_place_is_not_anchored(text):
    assert loc.anchor(rule("Toronto"), text) is None


def test_a_neighbourhood_the_site_names_counts_and_one_it_does_not_name_does_not():
    r = rule("Leeds", known=["Headingley"], corpus="We serve Headingley and Chapel Allerton. Our dentist team is friendly.")
    assert loc.anchor(r, "cheap bike service Headingley") == "a place the site names (Headingley)"
    assert loc.anchor(r, "bike repair Chapel Allerton", claimed="Chapel Allerton") is not None     # named in the site's text
    assert loc.anchor(r, "bike repair Roundhay", claimed="Roundhay") is None                         # the site never says it
    assert loc.anchor(r, "best dentist team", claimed="dentist") is None                              # a common word is not a place
    assert loc.anchor(r, "bike repair Chapel Allerton") is None                                      # no claim, not in the known list


def test_the_nearest_dentist_is_not_a_place():
    assert loc.anchor(rule("Toronto"), "the nearest dentist") is None


def test_no_place_is_required_of_a_business_that_is_not_local():
    kept, rejected = check([q("best online shop for cast iron pans")], {"name": "Pan Co", "domain": "panco.example"},
                           rule(required=False))
    assert len(kept) == 1 and rejected == []


def test_a_question_in_another_language_must_use_the_sites_own_form_of_the_place():
    r = rule("Montreal", by_language={"fr": "Montréal"})
    assert loc.language_issue(r, "plombier Montreal urgence", "fr") is None                  # same name once accents are ignored
    r2 = rule("Brussels", by_language={"fr": "Bruxelles"})
    assert "writes it \"Bruxelles\" in fr" in loc.language_issue(r2, "plombier Brussels urgence", "fr")
    assert loc.language_issue(r2, "plombier Bruxelles urgence", "fr") is None
    assert loc.language_issue(r2, "plumber Brussels", "en") is None                           # the site gives no English form to check


def test_the_site_can_give_the_place_in_each_language():
    blocks = [json.dumps({"address": {"addressLocality": [{"@language": "en", "@value": "Brussels"}, {"@language": "fr", "@value": "Bruxelles"}]}})]
    p = loc.find_place(blocks, "", None)
    assert p.by_language == {"en": "Brussels", "fr": "Bruxelles"}


def test_the_wrong_form_is_kept_but_flagged_for_review(biz):
    r = rule("Brussels", by_language={"fr": "Bruxelles"})
    kept, rejected = check([q("plombier Brussels urgence", language="fr", place="Brussels")], biz, r, ("fr",))
    assert rejected == [] and kept[0].needs_review and "Bruxelles" in kept[0].review_reason


def test_two_asks_in_one_question_are_flagged(biz):
    kept, _ = check([disc("best dentist? and how much do they charge?")], biz, rule("Toronto"))
    assert kept[0].needs_review and "two things" in kept[0].review_reason


# ---- 3. brand questions and fact_topic ----------------------------------------------------------------------------------------------

def test_the_loader_validates_fact_topic(tmp_path):
    def write(entry):
        path = tmp_path / "set_v1.yaml"
        path.write_text("questions:\n  - " + json.dumps(entry) + "\n")
        return path
    assert load_question_set(write({"text": "What is X?", "type": "brand", "fact_topic": "other"})).items[0].fact_topic == "other"
    with pytest.raises(QuestionSetError, match="fact_topic must be one of"):
        load_question_set(write({"text": "What is X?", "type": "brand", "fact_topic": "weather"}))
    with pytest.raises(QuestionSetError, match="brand questions only"):
        load_question_set(write({"text": "dentist Toronto", "type": "discovery", "fact_topic": "hours"}))
    assert set(FACT_TOPICS) == {"rating", "people", "services", "hours", "address", "contact", "insurance", "prices", "languages",
                                "availability", "other"}


def test_fact_topic_is_in_the_fingerprint_and_old_sets_keep_theirs(tmp_path):
    def fingerprint(entry):
        path = tmp_path / "set_v1.yaml"
        path.write_text("questions:\n  - " + json.dumps(entry) + "\n")
        return load_question_set(path).fingerprint
    base = {"text": "What is X?", "type": "brand"}
    assert fingerprint({**base, "fact_topic": "hours"}) != fingerprint({**base, "fact_topic": "address"}) != fingerprint(base)
    assert fingerprint(base) == fingerprint({**base})
    old = [{"text": "plain"}, {"text": "tagged", "intent": "commercial", "type": "discovery", "length": "short", "language": "en"}]
    path = tmp_path / "old_v1.yaml"
    path.write_text("questions:\n" + "\n".join("  - " + json.dumps(e) for e in old) + "\n")
    expected = hashlib.sha256(json.dumps(["plain", {"text": "tagged", "intent": "commercial", "is_target": False, "type": "discovery",
                                                    "length": "short", "language": "en"}], ensure_ascii=False).encode()).hexdigest()
    assert load_question_set(path).fingerprint == expected


def test_resolve_tags_carries_the_fact_topic(tmp_path, biz):
    path = tmp_path / "set_v1.yaml"
    path.write_text("questions:\n  - " + json.dumps({"text": f"What is {biz['name']}?", "type": "brand", "fact_topic": "other"}) + "\n")
    tags, _ = resolve_tags(load_question_set(path), biz["name"], biz["domain"])
    assert tags[0]["fact_topic"] == "other"


def test_a_brand_question_without_a_fact_topic_is_flagged_not_dropped(biz):
    kept, rejected = check([q(f"What is {biz['name']}?", kind="brand")], biz)
    assert rejected == [] and kept[0].needs_review and "no fact_topic" in kept[0].review_reason


def test_a_fact_topic_is_cleaned_up_or_flagged(biz):
    kept, _ = check([q(f"Hours of {biz['name']}?", kind="brand", fact_topic=" Hours "),
                     q(f"Weather at {biz['name']}?", kind="brand", fact_topic="weather")], biz)
    assert kept[0].fact_topic == "hours" and not kept[0].needs_review
    assert kept[1].fact_topic is None and kept[1].needs_review and "not one of the topics" in kept[1].review_reason
    assert kept[1].review_reason.count("fact_topic") == 1


def test_who_owns_questions_are_dropped(biz):
    texts = [f"Who owns {biz['domain']}?", f"who owns {biz['name']}", f"Who is the owner of {biz['name']}?", f"Qui possède {biz['name']}?"]
    kept, rejected = check([q(t, kind="brand", fact_topic="other") for t in texts], biz)
    assert kept == [] and len(rejected) == 4 and all("who owns" in x.reasons[0] for x in rejected)
    ok, _ = check([q(f"Who are the dentists at {biz['name']}?", kind="brand", fact_topic="people")], biz)
    assert len(ok) == 1


def test_brand_questions_are_saved_with_their_fact_topic_and_spread_over_six_topics(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    brand = [i for i in file_items(r) if i["type"] == "brand"]
    assert all(i["fact_topic"] in FACT_TOPICS for i in brand) and len({i["fact_topic"] for i in brand}) >= plan.MIN_TOPICS
    assert not any("fact topic" in w for w in r.warnings)
    assert not any("fact_topic" in i for i in file_items(r) if i["type"] == "discovery")
    tags, _ = resolve_tags(load_question_set(r.path), biz["name"], biz["domain"])
    assert sum(1 for t in tags if t["fact_topic"]) == 10


def test_a_narrow_spread_of_topics_is_a_warning_not_a_rejection(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    for it in items:
        if it["kind"] == "brand":
            it["fact_topic"] = "services"
    r = run(biz, tmp_path, fake(items))
    assert r.rejected == [] and any("Brand questions cover 1 fact topic(s)" in w for w in r.warnings)


def test_the_set_is_checked_for_recognition_url_and_city_variants(biz):
    items = [q(f"Hours of {biz['name']}?", kind="brand", fact_topic="hours")]
    kept, _ = check(items, biz)
    w = plan.coverage_warnings(kept, name=biz["name"], domain=biz["domain"], location="Toronto", needs_place=True)
    assert any("web address" in x for x in w) and any("city after the name" in x for x in w) and any("recognition" in x for x in w)
    full = [q(f"What is {biz['name']}?", kind="brand", fact_topic="other"), q(f"Tell me about {biz['domain']}", kind="brand", fact_topic="services"),
            q(f"Is {biz['name']} Toronto any good?", kind="brand", fact_topic="rating")]
    kept, _ = check(full, biz)
    w = plan.coverage_warnings(kept, name=biz["name"], domain=biz["domain"], location="Toronto", needs_place=True)
    assert not any(x for x in w if "web address" in x or "city after" in x or "recognition" in x)


def test_a_rating_question_stores_the_tag_only_and_the_file_says_where_the_answer_comes_from(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    rated = next(i for i in items if i.get("fact_topic") == "rating")
    r = run(biz, tmp_path, fake(items))
    saved = next(i for i in file_items(r) if i["text"] == rated["text"])
    assert saved["fact_topic"] == "rating" and "rating" not in {k for k in saved if k != "fact_topic"}
    text = r.path.read_text()
    assert "live source" in text and "Google Business Profile" in text and "evidence file for the same date" in text
    qs = load_question_set(r.path)
    assert "Google Business Profile" in (qs.items[0].text and text.split("note:")[1])


def test_with_no_rating_question_the_file_carries_no_rating_note(biz, tmp_path):
    diagnosed(biz, tmp_path)
    items = clean_set(biz)
    for it in items:
        if it.get("fact_topic") == "rating":
            it["fact_topic"] = "contact"
    assert "Google Business Profile" not in run(biz, tmp_path, fake(items)).path.read_text()


# ---- 4. counts and the spot check -----------------------------------------------------------------------------------------------------

def test_thirty_two_is_eighteen_discovery_four_control_ten_brand():
    t = plan.make_targets(32, ["en"])
    assert (t.discovery, t.control, t.brand) == (18, 4, 10)
    for size in (12, 16, 24, 48, 64, 100):
        t = plan.make_targets(size, ["en", "fr"])
        assert t.discovery + t.control + t.brand == size and t.brand == int(size * 10 / 32 + 0.5) and t.control >= 1


def test_plan_section_8_states_the_new_counts():
    text = (ROOT / "PLAN.md").read_text()
    assert "~18 discovery plus 4 control (40% short, 40% medium, 20% long), ~10 brand" in text and "~24 discovery" not in text
    row = next(l for l in text.splitlines() if l.startswith("| 25e |"))
    assert row.rstrip().endswith("✅ Done |")


def kept_set(b, languages=("en", "fr")):
    kept, rejected = check(clean_set(b, languages), b, rule("Toronto"), languages)
    assert rejected == []
    return plan.order_for_file(kept, list(languages))


def test_the_spot_check_is_five_discovery_with_a_place_and_different_needs_and_three_brand_with_different_topics(biz):
    ordered = kept_set(biz)
    chosen = [ordered[i] for i in plan.choose_spot_check(ordered, ["en", "fr"], biz["domain"])]
    disc_ = [c for c in chosen if c.kind == "discovery"]
    brand = [c for c in chosen if c.kind == "brand"]
    assert len(disc_) == 5 and len(brand) == 3 and not any(c.control or c.needs_review for c in chosen)
    assert all(c.place_how for c in disc_) and len({c.need for c in disc_}) == 5
    assert len({c.fact_topic for c in brand}) == 3 and None not in {c.fact_topic for c in brand}
    assert {c.language for c in chosen} == {"en", "fr"}


def test_the_spot_check_never_takes_a_flagged_question_for_its_need_or_topic(biz):
    ordered = kept_set(biz)
    for c in ordered:
        if c.need == "service" or c.fact_topic == "services":
            c.needs_review = True
    chosen = [ordered[i] for i in plan.choose_spot_check(ordered, ["en", "fr"], biz["domain"])]
    assert not any(c.needs_review for c in chosen) and not any(c.need == "service" for c in chosen)
    assert len({c.need for c in chosen if c.kind == "discovery"}) == 5


def test_the_spot_check_never_takes_a_discovery_question_that_lacks_its_place(biz):
    ordered = kept_set(biz)
    for c in ordered:
        if c.kind == "discovery" and not c.control and c.need == "urgent":
            c.place_how = None                                   # as if the check had been skipped
    chosen = [ordered[i] for i in plan.choose_spot_check(ordered, ["en", "fr"], biz["domain"])]
    assert not any(c.need == "urgent" for c in chosen)


def test_the_recognition_question_is_the_last_choice_for_a_spot_check_topic(biz):
    ordered = kept_set(biz, ("en",))
    chosen = [ordered[i] for i in plan.choose_spot_check(ordered, ["en"], biz["domain"])]
    assert "other" not in {c.fact_topic for c in chosen if c.kind == "brand"}


def test_the_summary_says_why_each_spot_check_question_was_chosen(biz, tmp_path):
    diagnosed(biz, tmp_path)
    text = "\n".join(summary_lines(run(biz, tmp_path, fake(clean_set(biz)))))
    assert "[need: " in text and "[fact topic: " in text and "Location used: Toronto (given with --location)" in text


# ---- 5. a scripted example for a business that is not a dentist ----------------------------------------------------------------------

def bike_shop_set(name: str, domain: str) -> list[dict]:
    """32 questions for a bike repair shop in Leeds (one language): 18 discovery, 4 control, 10 brand."""
    d = lambda text, need, **kw: q(text, place=kw.pop("place", "Leeds"), need=need, **kw)       # noqa: E731
    b = lambda text, topic: q(text.format(n=name, d=domain), kind="brand", fact_topic=topic)    # noqa: E731
    discovery = [
        d("bike repair Leeds", "service", is_target=True),
        d("My bike's gears keep slipping and the brakes squeal. Where can I get a full service in Leeds this week?", "service", is_target=True),
        d("puncture repair while you wait Leeds", "urgent", is_target=True),
        d("I've got a puncture and a commute tomorrow morning. Which Leeds bike shops can fix it today or first thing?", "urgent"),
        d("cheap bike service Leeds", "price"),
        d("I just need a basic tune-up and don't want to be upsold. What's the cheapest honest bike shop in Headingley?", "price", place="Headingley"),
        d("Leeds bike shop cycle to work", "payment"),
        d("Which bike shops in Leeds let you pay for repairs in instalments?", "payment"),
        d("kids bike repair Leeds", "audience"),
        d("My dad is 72 and has just bought an electric bike. Is there a bike shop near me in Chapel Allerton that works on e-bikes and is patient with beginners?", "audience", place="Chapel Allerton"),
        d("best rated bike shop Leeds", "trust"),
        d("Which Leeds bike repair shops have the best reviews?", "trust"),
        d("bike repair open Sunday Leeds", "convenience"),
        d("Is there a bike repair shop near me in Leeds that's open late on weekdays?", "convenience", place="near me"),
        d("Polish speaking bike mechanic Leeds", "language"),
        d("My neighbour only speaks Polish and needs her bike fixed before Friday. Do any bike shops in Leeds have Polish-speaking staff?", "language"),
        d("Independent bike shop or chain store for repairs in Leeds, which is better?", "compare"),
        d("I'm choosing between a small repair shop and a big chain for my road bike in Leeds. Which shops near Headingley should I compare?", "compare", place="Headingley"),
    ]
    controls = [q(t, intent="informational", control=True) for t in (
        "how long do tortoises live?", "What is the capital of Australia?", "convert Celsius to Fahrenheit?",
        "Can you explain how photosynthesis works in plants?")]
    brand = [b("What is {n}?", "other"), b("Who are the mechanics at {d}?", "people"),
             b("What do customers say about {n} in their Google reviews?", "rating"),
             b("What services does {n} in Leeds offer?", "services"),
             b("What are the opening hours of {n} on weekends?", "hours"),
             b("What is the street address of {n}?", "address"),
             b("What phone number do I call to book a repair at {n}?", "contact"),
             b("How much does {n} charge for a basic bike service?", "prices"),
             b("Which languages do the staff at {n} speak?", "languages"),
             b("What is the booking process at {n} for repairs?", "availability")]
    return discovery + controls + brand


@pytest.fixture
def bike_run(biz, tmp_path):
    diagnosed(biz, tmp_path, jsonld=ld("Leeds", areas=["Headingley", "Chapel Allerton"]))
    said: list = []
    result = run(biz, tmp_path, fake(bike_shop_set(biz["name"], biz["domain"])), languages=("en",), location=None, said=said)
    return result, said


def test_a_non_dental_example_passes_every_check_with_nothing_flagged_dropped_or_warned(bike_run):
    r, said = bike_run
    items = file_items(r)
    assert len(items) == 32 and r.calls == 1 and r.rejected == [] and r.missing == {} and r.warnings == []
    assert (sum(i["type"] == "discovery" and not i["control"] for i in items), sum(i["control"] for i in items),
            sum(i["type"] == "brand" for i in items)) == (18, 4, 10)
    assert not any(i.get("needs_review") for i in items)
    assert {k: sum(1 for i in items if i["length"] == k) for k in plan.LENGTHS} == {"short": 13, "medium": 13, "long": 6}
    assert r.place.location == "Leeds" and len(r.needs) == len(plan.NEEDS)
    assert len({i["fact_topic"] for i in items if i["type"] == "brand"}) == 10
    spot = [i for i in items if i["spot_check"]]
    assert len(spot) == 8 and sum(i["type"] == "brand" for i in spot) == 3


def test_every_discovery_question_in_the_example_really_carries_a_place(bike_run):
    r, _ = bike_run
    rule_ = loc.PlaceRule(required=True, location="Leeds", known=r.place.known, corpus="")
    for i in file_items(r):
        if i["type"] == "discovery" and not i["control"]:
            assert loc.anchor(rule_, i["text"]) is not None, i["text"]
    assert not any("dentist" in i["text"].lower() for i in file_items(r))


# ---- 6. v1 and v2 are left alone ----------------------------------------------------------------------------------------------------------

def test_the_v1_and_v2_prompts_are_untouched_and_v3_is_the_one_in_use():
    assert hashlib.sha256((ROOT / "prompts/questions_draft/v1.md").read_bytes()).hexdigest() == \
        "f0360064f94eafd6aa73ddbf09da77a91f41748b6c21c953004a5c6dc6c14502"
    assert hashlib.sha256((ROOT / "prompts/questions_draft/v2.md").read_bytes()).hexdigest() == V2_PROMPT_SHA256
    assert qrun.PROMPT_VERSION == "v3" and qrun.DRAFT_AGENT.prompt_path.name == "v3.md"


def test_the_v3_prompt_has_the_whole_brief_and_examples_that_are_not_only_dental():
    v3 = (ROOT / "prompts/questions_draft/v3.md").read_text()
    for needle in ("LIST OF BUSINESSES", "near me", "One ask per question", "fact_topic", "Never write \"Who owns", "Google reviews",
                   "at least 6 different topics", "recognition", "web address", "\"local\"", "--location" if False else "reply with ONLY {\"local\": true",
                   "short: 6 words or fewer", "more than 30 words", "do not add facts".lower()):
        assert needle.lower() in v3.lower(), needle
    for need in plan.NEEDS:
        assert f'"{need}"' in v3
    assert "dental clinic" in v3 and "online-only kitchenware shop" in v3 and "bike repair shop" in v3
    for topic in FACT_TOPICS:
        assert topic in v3


def test_earlier_draft_files_are_never_touched_by_a_new_draft(biz, tmp_path):
    diagnosed(biz, tmp_path)
    folder = tmp_path / "clients" / biz["domain"]
    folder.mkdir(parents=True, exist_ok=True)
    old = {n: f"questions:\n  - \"old draft {n}\"\n" for n in (1, 2)}
    for n, body in old.items():
        (folder / f"question_set_draft_v{n}.yaml").write_text(body)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    assert r.path.name == "question_set_draft_v3.yaml"
    for n, body in old.items():
        assert (folder / f"question_set_draft_v{n}.yaml").read_text() == body


# ---- 7. the command ---------------------------------------------------------------------------------------------------------------------

def test_the_command_passes_location_through(biz, tmp_path, monkeypatch):
    seen = {}
    def fake_draft(conn, b, **kw):
        seen.update(kw)
        raise DraftError("stop here")
    monkeypatch.setattr(cli.qdraft, "draft_questions", fake_draft)
    res = CliRunner().invoke(cli.app, ["questions", "draft", biz["domain"], "--location", "Toronto"])
    assert seen["location"] == "Toronto" and "stop here" in res.output
    CliRunner().invoke(cli.app, ["questions", "draft", biz["domain"]])
    assert seen["location"] is None
    assert "--location" in CliRunner().invoke(cli.app, ["questions", "draft", "--help"]).output


def test_a_stop_for_a_missing_place_is_a_plain_error_with_no_traceback(biz, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.qdraft, "draft_questions", lambda *a, **k: (_ for _ in ()).throw(DraftError("Run it again with --location \"<city or area>\".")))
    res = CliRunner().invoke(cli.app, ["questions", "draft", biz["domain"]])
    assert res.exit_code == 1 and "--location" in res.output and "Traceback" not in res.output
