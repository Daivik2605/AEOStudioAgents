"""Step 25g: three problems from a real `aeo questions draft` run (maximsdentistry.ca, v4 prompt, thinking off).
The two replies in tests/fixtures/real_run_25g/ are the model's real replies from that run, word for word (only the
business name and web address are swapped for the test business). The model is the scripted fake; no AI, no network."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from questions_draft import plan, run as qrun
from questions_draft.run import DraftOutput
from questions_draft.salvage import reply_json, salvage
from tests.conftest import FakeAnthropicClient, text_response
from tests.test_audit import biz  # noqa: F401  (fixture)
from tests.test_question_drafts_keep_everything import lopsided
from tests.test_questions_draft import (Words, clean_set, diagnosed, disc, fake, file_items,  # noqa: F401
                                        person_at_keyboard, q, run, sent_to_model)

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "tests" / "fixtures" / "real_run_25g"
AREA = "North York"


def real(name: str, b: dict) -> str:
    return (REAL / name).read_text().replace("Maxim's Dentistry", b["name"]).replace("maximsdentistry.ca", b["domain"])


def lengths(r) -> dict:
    out = {"short": 0, "medium": 0, "long": 0}
    for i in file_items(r):
        out[i["length"]] += 1
    return out


# ---- 1. the first call returned nothing usable ----------------------------------------------------------------------------------------------

def test_the_real_first_reply_held_no_questions_and_the_command_says_so(biz, tmp_path):
    """Call 1 of the real run was 36 tokens: only {"local": true, "local_reason": ...}, although "North York" was given."""
    diagnosed(biz, tmp_path)
    first = real("call1_local_only.txt", biz)
    assert set(json.loads(first)) == {"local", "local_reason"}
    client = FakeAnthropicClient([text_response(first), text_response(real("call2_prose_then_json.txt", biz)), text_response(json.dumps({"discovery": []}))])
    r = run(biz, tmp_path, client, languages=("en",), location=AREA)
    assert any("first reply held no questions, although a place was known" in w for w in r.warnings)
    retry = sent_to_model(client, 1)["retry"]
    assert retry["accepted"] == [] and sum(n["count"] for n in retry["needed"]) == 32          # the next call asks for all of them


def test_the_real_second_reply_has_reasoning_before_the_json_and_is_read_without_a_third_call_for_it(biz, tmp_path):
    """Call 2 of the real run began "Looking at the inputs, note ..." and then gave a whole, good set. It was thrown out as
    invalid, and the repeat used up the last call. Now the JSON is found inside it."""
    diagnosed(biz, tmp_path)
    second = real("call2_prose_then_json.txt", biz)
    assert not second.lstrip().startswith("{")
    with pytest.raises(ValueError):
        json.loads(second)
    client = FakeAnthropicClient([text_response(real("call1_local_only.txt", biz)), text_response(second), text_response(json.dumps({"discovery": []}))])
    r = run(biz, tmp_path, client, languages=("en",), location=AREA)
    assert not any("invalid" in str(c).lower() for c in client.messages.calls[1:2])
    sent = [json.loads(c["messages"][0]["content"]) for c in client.messages.calls]
    assert "retry" in sent[1] and all(len(c["messages"]) == 1 for c in client.messages.calls)      # no "that response was invalid" repeat
    assert len(file_items(r)) == 32 and r.rejected == []


def test_the_real_set_leaves_a_length_gap_and_the_top_up_now_runs_and_fills_it(biz, tmp_path):
    """The real set was 19 short, 11 medium, 2 long against 13/13/6. The top-up never ran, because the earlier calls had used all
    three. With them fixed, the top-up is the third call, asks for long questions, and the long bucket is filled."""
    diagnosed(biz, tmp_path)
    w = Words(21)
    longs = [disc(f"{w.many(36)} {AREA}", is_target=True, need="urgent") for _ in range(6)]
    for x in longs:
        x["text"] = x["text"].replace(" Toronto", "")
        x["place"] = AREA
    client = FakeAnthropicClient([text_response(real("call1_local_only.txt", biz)), text_response(real("call2_prose_then_json.txt", biz)),
                                  text_response(json.dumps({"discovery": [{k: v for k, v in x.items() if k in ("text", "intent", "need", "is_target")}
                                                                           for x in longs]}))])
    r = run(biz, tmp_path, client, languages=("en",), location=AREA)
    before = {"short": 19, "medium": 11, "long": 2}
    assert len(client.messages.calls) == 3 and r.topped_up == "long"
    topup = sent_to_model(client, 2)["topup"]
    assert topup["length"] == "long" and topup["count"] == 4 and topup["places"][0]["group"] == "discovery"
    now = lengths(r)
    assert now["long"] == 6 and now["long"] > before["long"] and sum(now.values()) == 32
    assert not any("Length mix" in w and "long" in w for w in r.warnings)


def test_a_top_up_that_is_wanted_but_has_no_call_left_is_a_warning_not_padding(biz, tmp_path):
    diagnosed(biz, tmp_path)
    bad = lopsided(biz)
    bad[0] = {**bad[0], "text": ""}
    client = fake(bad, "not json", "still not json")                     # call 2 and its repeat (call 3) are lost
    r = run(biz, tmp_path, client)
    assert len(client.messages.calls) == 3 and r.topped_up is None
    assert any("model calls were already used. Not padded." in w and "short" in w for w in r.warnings)
    assert len(file_items(r)) == 31


# ---- reading a reply with words around the JSON ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    'Looking at the inputs, I will use the given name.\n\n{"local": true, "discovery": [{"text": "a"}]}',
    '{"local": true, "discovery": [{"text": "a"}]}\nI hope that helps.',
    'Note {with braces} first.\n{"local": true, "discovery": [{"text": "a"}]}',
    '```json\n{"local": true, "discovery": [{"text": "a"}]}\n```',
])
def test_the_json_is_found_inside_a_reply_with_words_around_it(text):
    got = json.loads(reply_json(text))
    assert got["discovery"] == [{"text": "a"}]
    assert DraftOutput.model_validate(got).all_questions(["en"])[0]["text"] == "a"


def test_a_reply_with_no_json_comes_back_unchanged_and_still_fails_the_normal_check():
    assert reply_json("I cannot do that.") == "I cannot do that."
    assert reply_json('{"unrelated": 1}') == '{"unrelated": 1}'


def test_a_cut_off_reply_with_words_before_it_is_still_salvaged():
    text = 'Thinking first.\n\n{"local": true, "local_reason": "a clinic",\n"discovery": [{"text": "x"}, {"text": "y"}, {"text": "wh'
    kept = salvage(text)
    assert [d["text"] for d in kept["discovery"]] == ["x", "y"] and kept["local"] is True


# ---- 2. the length mix -----------------------------------------------------------------------------------------------------------------------

def test_the_length_counts_are_given_per_list_and_add_up():
    t = plan.make_targets(32, ["en", "fr"])
    by_list = plan.length_by_list(t)
    assert by_list["discovery"] == {"short": 8, "medium": 4, "long": 6}
    assert by_list["control"] == {"short": 2, "medium": 2, "long": 0} and by_list["brand"] == {"short": 3, "medium": 7, "long": 0}
    for length in plan.LENGTHS:
        assert sum(row[length] for row in by_list.values()) == t.length_aim[length]
    for group, size in (("discovery", t.discovery), ("control", t.control), ("brand", t.brand)):
        assert sum(by_list[group].values()) == size


@pytest.mark.parametrize("size", range(plan.MIN_SIZE, plan.MAX_SIZE + 1))
def test_every_allowed_size_has_a_per_list_split_that_adds_up(size):
    t = plan.make_targets(size, ["en"])
    by_list = plan.length_by_list(t)
    assert by_list is not None and all(n >= 0 for row in by_list.values() for n in row.values())
    assert [sum(by_list[g].values()) for g in ("discovery", "control", "brand")] == [t.discovery, t.control, t.brand]


def test_the_payload_carries_the_per_list_counts_and_the_prompt_asks_for_long_ones_first(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    run(biz, tmp_path, client)
    assert sent_to_model(client)["length_by_list"]["discovery"]["long"] == 6
    v4 = (ROOT / "prompts/questions_draft/v4.md").read_text()
    assert "write the LONG questions first" in v4 and '"length_by_list"' in v4 and "Your first character must be {" in v4
    assert "write ALL the questions in this same reply" in v4


# ---- 3. yes/no brand questions ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Is {n} accepting new patients?", "Does {n} offer financing?", "Does {n} offer sedation?",
                                  "Can I book online with {n}?", "Est-ce que {n} accepte de nouveaux patients?", "Has {n} got parking?"])
def test_plain_yes_no_brand_questions_are_not_flagged(text):
    assert not plan.assumes_a_fact(text.format(n="Acme"))


@pytest.mark.parametrize("text", ["Why is {n} the cheapest?", "When did {n} start offering implants?", "How long has {n} been open late?",
                                  "Since when does {n} take walk-ins?", "Pourquoi {n} est-il le moins cher?", "Quand {n} a-t-il commencé à offrir des facettes?"])
def test_a_question_that_takes_a_fact_for_granted_is_flagged(text):
    assert plan.assumes_a_fact(text.format(n="Acme"))


def test_a_yes_no_brand_question_must_still_carry_a_fact_topic(biz):
    items = [q(f"Is {biz['name']} accepting new patients?", kind="brand", fact_topic=None),
             q(f"Does {biz['name']} offer financing?", kind="brand", fact_topic="insurance")]
    kept, rejected = plan.check_questions(items, name=biz["name"], domain=biz["domain"], alternates=[], languages=["en"],
                                          confirmed_fact_ids=set(), already=[])
    assert rejected == []
    assert kept[0].needs_review and "fact_topic" in kept[0].review_reason
    assert not kept[1].needs_review


def test_a_question_about_an_unconfirmed_fact_is_kept_at_the_draft_stage(biz):
    item = q(f"Does {biz['name']} offer sedation?", kind="brand", relies=[7], fact_topic="services")
    kept, rejected = plan.check_questions([item], name=biz["name"], domain=biz["domain"], alternates=[], languages=["en"],
                                          confirmed_fact_ids=set(), already=[])
    assert rejected == [] and len(kept) == 1 and not kept[0].needs_review


# ---- 4. earlier prompts and PLAN.md -------------------------------------------------------------------------------------------------------------

def test_v1_v2_v3_are_still_as_they_were():
    sums = {"v1": "f0360064f94eafd6aa73ddbf09da77a91f41748b6c21c953004a5c6dc6c14502",
            "v2": "95219f0afcc7a625b48e456a66222a4f77ea86a9be15f4d9430a42e38b19efad",
            "v3": "79129459c36473607cc2898f75a179553c5368725ac8ddca1f148306cbaa241b"}
    for v, want in sums.items():
        assert hashlib.sha256((ROOT / f"prompts/questions_draft/{v}.md").read_bytes()).hexdigest() == want


def test_plan_has_row_25g_after_25f_and_nothing_else_in_section_8_changed():
    lines = (ROOT / "PLAN.md").read_text().splitlines()
    row = next(l for l in lines if l.startswith("| 25g |"))
    assert row.rstrip().endswith("✅ Done |") and lines.index(row) == next(i for i, l in enumerate(lines) if l.startswith("| 25f |")) + 1
    assert "~10 brand (one asks with the URL)." in "\n".join(lines)
