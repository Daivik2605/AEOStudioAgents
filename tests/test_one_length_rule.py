"""PLAN.md step 25c, item 5 (and its small companions): one rule for question length, the v2 draft prompt, controls kept out
of the dossier's spot-check list, and `aeo status --all` hiding test businesses. No AI, no network."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo import cli
from audit import report_data
from audit.question_set import load_question_set, resolve_tags
from core import question_length, status as st
from core.db import get_connection
from core.question_length import LENGTHS, MEDIUM_MAX_WORDS, SHORT_MAX_WORDS, length_for, length_of
from dossier import questions as dossier_questions
from questions_draft import plan
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_compare import CLIENT_REC, RIVAL, add_run, make_qs
from tests.test_dossier import dossier, section, write_questions

ROOT = Path(__file__).parent.parent
V1_PROMPT_SHA256 = "f0360064f94eafd6aa73ddbf09da77a91f41748b6c21c953004a5c6dc6c14502"       # v1 as it was when v2 was made


def words(n: int, sentences: int = 1, tag: str = "") -> str:
    """n words in `sentences` sentences. `tag` makes two texts of the same size different."""
    per, extra = divmod(n, sentences)
    parts = []
    for i in range(sentences):
        k = per + (1 if i < extra else 0)
        parts.append(" ".join(f"w{tag}{i}x{j}" for j in range(k)) + ".")
    return " ".join(parts)


# ---- the rule ----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("", "short"), ("hello", "short"), ("water stations", "short"),                       # under 3 words: short
    (words(3), "short"), (words(6), "short"),                                             # 6 or fewer: short
    (words(7), "medium"), (words(18), "medium"), (words(30), "medium"),                  # one sentence of 7 to 30 words: medium
    (words(31), "long"), (words(80), "long"),                                             # more than 30 words: long
    (words(14, sentences=2), "long"), (words(8, sentences=3), "long"),                    # two or more sentences: long
])
def test_the_boundaries(text, expected):
    assert length_of(text) == expected


def test_the_constants_state_the_rule():
    assert (SHORT_MAX_WORDS, MEDIUM_MAX_WORDS) == (6, 30) and LENGTHS == ("short", "medium", "long")


def test_a_very_short_text_is_short_whatever_its_punctuation():
    """The checks run short first, as the dossier's own code always did: 'Help. Now.' is short, not long."""
    assert length_of("Help. Now.") == "short" and length_of(words(6, sentences=2)) == "short"


def test_questions_that_really_look_like_questions():
    assert length_of("refill station rental") == "short"
    assert length_of("Who rents water stations for a summer festival in Toronto?") == "medium"
    assert length_of("We host 300 people. We need six stations.") == "long"
    assert length_of("Quelles entreprises louent des stations de remplissage d'eau pour les festivals au Québec?") == "medium"


def test_an_explicit_tag_always_wins():
    for tag in LENGTHS:
        assert length_for(words(40), tag) == (tag, "tag") and length_for("two words", tag) == (tag, "tag")
    assert length_for(words(40)) == ("long", "word count")
    assert length_for(words(40), "huge") == ("long", "word count")                          # a tag that is not one of the three is ignored
    assert length_for(words(40), "") == ("long", "word count")


# ---- one function, one module ---------------------------------------------------------------------------------------------------------

def python_files():
    for folder in ("aeo", "audit", "client_report", "compare", "core", "diagnose", "dossier", "questions_draft", "recommend", "scoring", "truth"):
        yield from (ROOT / folder).rglob("*.py")


def test_no_other_module_defines_a_length_rule():
    defines = [str(p.relative_to(ROOT)) for p in python_files() if re.search(r"^def (length_of|word_length|question_length)\b", p.read_text(), re.M)]
    assert defines == ["core/question_length.py"]
    for p in python_files():                      # nor does anything keep its own boundary numbers
        if p.name in ("question_length.py",):
            continue
        assert not re.search(r"\bSHORT_UNDER\b|\bMEDIUM_UP_TO\b|words\s*<=\s*6\b|words\s*>\s*30\b", p.read_text()), p


def test_every_user_of_the_rule_gets_the_same_function():
    assert plan.length_of is length_of and dossier_questions.length_of is length_of
    assert plan.LENGTHS is LENGTHS and dossier_questions.LENGTHS is LENGTHS
    assert report_data.length_for is length_for and question_length.length_for is length_for


def test_the_four_users_agree_on_the_same_text():
    text = words(20)
    assert plan.length_of(text) == dossier_questions.length_of(text) == "medium"
    types = report_data.question_types(None, [text], {"name": "Acme", "domain": "acme.example"})
    assert types[text]["length"] == "medium"                                      # the audit report, and so compare


def write_yaml(path: Path, items: list[dict]) -> Path:
    import json
    path.write_text("questions:\n" + "".join(f"  - {json.dumps(i)}\n" for i in items))
    return path


def test_the_dossier_reader_and_the_loader_use_the_rule_and_honour_tags(tmp_path):
    f = write_yaml(tmp_path / "set_v1.yaml", [
        {"text": words(20)}, {"text": words(40)}, {"text": words(3)},
        {"text": words(40, tag="b"), "length": "short"}, {"text": "two words", "length": "long"}])
    read = dossier_questions.read_questions(f)["items"]
    assert [(i["length"], i["length_from"]) for i in read] == [("medium", "word count"), ("long", "word count"), ("short", "word count"),
                                                              ("short", "tag"), ("long", "tag")]
    qs = load_question_set(f)
    tags, _ = resolve_tags(qs, "Acme", "acme.example")
    assert [(t["length"], t["length_source"]) for t in tags] == [(i["length"], i["length_from"]) for i in read]


def test_the_audit_report_keeps_tags_and_counts_the_rest(biz):
    tagged, untagged = words(40, tag="a"), words(40, tag="b")
    types = report_data.question_types([{"text": tagged, "length": "short"}, {"text": untagged}], [tagged, untagged], biz)
    assert types[tagged]["length"] == "short" and types[untagged]["length"] == "long"          # the tag wins; the rest is counted


def test_compare_gets_its_lengths_from_the_same_place(biz):
    """compare reads types through report_data.question_types, which uses the one rule (checked above); a run's recorded
    tags are what it started with, so an old run keeps the lengths it was frozen with."""
    from compare import audit as compare_audit
    assert compare_audit.question_types is report_data.question_types


def test_existing_drafts_and_their_tags_are_left_alone(tmp_path):
    body = ('# DRAFT\nnote: "hand made"\nquestions:\n'
            f'  - {{"text": "{words(40)}", "type": "discovery", "length": "short"}}\n'
            '  - {"text": "two words", "type": "discovery", "length": "long"}\n')
    draft = tmp_path / "question_set_draft_v0.yaml"
    draft.write_text(body)
    before = hashlib.sha256(draft.read_bytes()).hexdigest()
    qs = load_question_set(draft)
    resolve_tags(qs, "Acme", "acme.example")
    dossier_questions.read_questions(draft)
    assert hashlib.sha256(draft.read_bytes()).hexdigest() == before
    assert [q.length for q in qs.items] == ["short", "long"]                                   # the tags are what the file says


def test_the_real_oland_draft_is_untouched_and_keeps_its_tags():
    draft = ROOT / "clients" / "olandstations.com" / "question_set_draft_v0.yaml"
    if not draft.exists():
        pytest.skip("the O'land draft is not on this machine (clients/ is gitignored)")
    before = hashlib.sha256(draft.read_bytes()).hexdigest()
    qs = load_question_set(draft)
    tags, _ = resolve_tags(qs, "O'land Stations", "olandstations.com")
    assert all(t["length_source"] == "tag" for t in tags) and [t["length"] for t in tags] == [q.length for q in qs.items]
    assert hashlib.sha256(draft.read_bytes()).hexdigest() == before


# ---- PLAN §8 and the v2 prompt ---------------------------------------------------------------------------------------------------------

def test_plan_section_8_states_the_boundaries():
    plan_md = (ROOT / "PLAN.md").read_text()
    line = next(l for l in plan_md.splitlines() if l.startswith("- Three lengths, tagged:"))
    assert f"**short** ({SHORT_MAX_WORDS} words or fewer" in line and "anything under 3 words counts as short" in line
    assert f"one sentence of {SHORT_MAX_WORDS + 1}–{MEDIUM_MAX_WORDS} words" in line
    assert f"two or more sentences, or more than {MEDIUM_MAX_WORDS} words" in line
    assert "explicit length tag" in line and "core/question_length.py" in line
    row = next(l for l in plan_md.splitlines() if l.startswith("| 25c |"))
    assert row.rstrip().endswith("| ✅ Done |")


def test_the_v2_prompt_has_the_same_rule_and_the_same_aim():
    v2 = (ROOT / "prompts" / "questions_draft" / "v2.md").read_text()
    assert f"short: {SHORT_MAX_WORDS} words or fewer" in v2 and "under 3 words also counts as short" in v2
    assert f"one sentence of {SHORT_MAX_WORDS + 1} to {MEDIUM_MAX_WORDS} words" in v2 and f"more than {MEDIUM_MAX_WORDS} words" in v2
    assert "about 40% short, 40% medium and 20% long" in v2 and plan.LENGTH_AIM == {"short": 0.4, "medium": 0.4, "long": 0.2}
    assert "under 12" not in v2 and "12 to 25" not in v2 and "over 25" not in v2
    assert "do not add facts" in v2.lower() and "ONLY a JSON object" in v2                        # everything else is as in v1


def test_v1_is_untouched_and_differs_from_v2_only_in_the_length_paragraph():
    v1_path = ROOT / "prompts" / "questions_draft" / "v1.md"
    assert hashlib.sha256(v1_path.read_bytes()).hexdigest() == V1_PROMPT_SHA256
    v1, v2 = v1_path.read_text().split("\n"), (ROOT / "prompts" / "questions_draft" / "v2.md").read_text().split("\n")
    changed = [l for l in v2 if l not in v1]
    assert changed and all(any(w in l for w in ("short", "medium", "long", "Length", "length", "Brand questions", "topup")) for l in changed)
    assert len([l for l in v1 if l not in v2]) == 2                                              # the length paragraph and the length_aim line


# ---- the command uses v2 ----------------------------------------------------------------------------------------------------------------

def test_the_command_uses_the_current_prompt_and_records_it(biz, tmp_path):
    from questions_draft import run as qrun
    from tests.test_questions_draft import clean_set, diagnosed, fake, run
    assert qrun.PROMPT_VERSION == "v3" and qrun.DRAFT_AGENT.prompt_path.name == "v3.md"
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    result = run(biz, tmp_path, client)
    assert "prompt questions_draft/v3" in result.path.read_text()
    assert client.messages.calls[0]["system"] == (ROOT / "prompts" / "questions_draft" / "v3.md").read_text()
    from tests.test_questions_draft import file_items
    assert all(i["length"] == length_of(i["text"]) for i in file_items(result))
    assert "short 6 words or fewer" in result.path.read_text() and "PLAN.md section 8" in result.path.read_text()


def test_a_drafted_set_has_all_three_lengths_by_the_new_rule(biz, tmp_path):
    from tests.test_questions_draft import clean_set, diagnosed, fake, file_items, run
    diagnosed(biz, tmp_path)
    items = file_items(run(biz, tmp_path, fake(clean_set(biz))))
    assert {i["length"] for i in items} == set(LENGTHS)
    assert all(i["length"] == "long" for i in items if len(i["text"].split()) > MEDIUM_MAX_WORDS)


# ---- controls and the dossier's spot-check list ---------------------------------------------------------------------------------------------

def test_before_the_run_a_control_is_not_listed_for_the_spot_check(biz, tmp_path):
    marked = write_questions(tmp_path / "set_v1.yaml", [
        {"text": "q one", "spot_check": True}, {"text": "q control", "spot_check": True, "control": True}, {"text": "q three", "spot_check": True}])
    data = section(dossier(biz, tmp_path, questions=marked), "ai_says")["data"]
    assert data["questions_to_ask"] == ["q one", "q three"]


def test_after_the_run_a_control_is_not_counted_or_listed_even_if_marked(biz, tmp_path):
    qs = make_qs(6)
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q % 2 == 0 else RIVAL), checkpoint="before")
    marked = write_questions(tmp_path / "set_v1.yaml", [
        {"text": qs.items[0].text, "type": "discovery", "spot_check": True},
        {"text": qs.items[1].text, "type": "discovery", "spot_check": True, "control": True},         # marked, but a control
        {"text": qs.items[2].text, "type": "discovery", "spot_check": True}])
    res = dossier(biz, tmp_path, questions=marked)
    data = section(res, "ai_says")["data"]
    assert {r["question"] for r in data["rows"]} == {qs.items[0].text, qs.items[2].text}
    assert len(data["rows"]) == 4 and data["discovery"]["answers"] == 4                  # 2 questions x 2 engines; the control is in neither
    assert qs.items[1].text not in {r["question"] for r in data["rows"]}


def test_a_spot_check_file_made_only_of_controls_lists_nothing(biz, tmp_path):
    qs = make_qs(4)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    only_controls = write_questions(tmp_path / "spot_v1.yaml", [{"text": qs.items[0].text, "control": True}])
    assert section(dossier(biz, tmp_path, spot_check=only_controls), "ai_says")["data"]["rows"] == []


# ---- aeo status --all hides the test businesses ---------------------------------------------------------------------------------------------

def run_status(*args):
    return CliRunner().invoke(cli.app, ["status", *args])


@pytest.fixture
def test_businesses():
    """Two businesses that look like the leaked ones, and near misses that must NOT be hidden."""
    conn = get_connection()
    made = {}
    try:
        for key, domain in {
            "leaked": f"core-test-{uuid.uuid4().hex}.example",
            "short_hex": f"core-test-{uuid.uuid4().hex[:31]}.example",           # 31 digits: not the pattern
            "other_tld": f"core-test-{uuid.uuid4().hex}.com",
            "capital": f"core-test-x{uuid.uuid4().hex}.example",                   # an extra letter: not the pattern
            "real": f"realclient-{uuid.uuid4().hex[:8]}.example",
        }.items():
            conn.execute("INSERT INTO businesses (domain, website_url, status) VALUES (%s, %s, %s)", (domain, f"https://{domain}", "declined" if key == "leaked" else "prospect"))
            made[key] = domain
    finally:
        conn.close()
    return made


def test_status_all_hides_test_businesses_by_default(test_businesses):
    out = run_status("--all").output
    assert test_businesses["leaked"] not in out
    assert all(test_businesses[k] in out for k in ("short_hex", "other_tld", "capital", "real"))          # only the exact pattern is hidden
    assert "test business(es) are hidden" in out and "Nothing is deleted" in out and "--include-test" in out


def test_include_test_shows_them(test_businesses):
    out = run_status("--all", "--include-test").output
    assert test_businesses["leaked"] in out and "are hidden" not in out


def test_the_filter_works_with_the_hiding(test_businesses):
    default = run_status("--all", "--filter", "declined").output
    assert test_businesses["leaked"] not in default and "hidden" in default
    shown = run_status("--all", "--filter", "declined", "--include-test").output
    assert test_businesses["leaked"] in shown


def test_hiding_never_deletes_and_a_hidden_business_can_still_be_looked_up(test_businesses):
    conn = get_connection()
    try:
        before = conn.execute("SELECT count(*) FROM businesses WHERE domain ~ %s", (st.TEST_DOMAIN_PATTERN,)).fetchone()[0]
        run_status("--all")
        run_status("--all", "--include-test")
        assert conn.execute("SELECT count(*) FROM businesses WHERE domain ~ %s", (st.TEST_DOMAIN_PATTERN,)).fetchone()[0] == before >= 1
        assert st.hidden_test_businesses(conn) == before
        assert st.hidden_test_businesses(conn, "declined") >= 1
    finally:
        conn.close()
    shown = run_status(test_businesses["leaked"])
    assert shown.exit_code == 0 and "declined" in shown.output


def test_the_pattern_is_exactly_core_test_and_32_hex_digits():
    import re as _re
    ok = f"core-test-{'0123456789abcdef' * 2}.example"
    assert _re.match(st.TEST_DOMAIN_PATTERN, ok)
    for bad in (ok.replace(".example", ".com"), ok.upper(), f"x{ok}", f"core-test-{'a' * 31}.example", f"core-test-{'a' * 33}.example", f"core-test-{'g' * 32}.example"):
        assert not _re.match(st.TEST_DOMAIN_PATTERN, bad), bad


def test_include_test_needs_all(test_businesses):
    result = run_status(test_businesses["real"], "--include-test")
    assert result.exit_code != 0 and "--all" in result.output
