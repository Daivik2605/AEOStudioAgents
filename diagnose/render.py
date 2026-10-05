"""The render check (PLAN.md section 3.1 step 4): is the content in the HTML a
crawler gets WITHOUT running JavaScript?

We only ever look at the raw HTML. The finding is "this content is not in the
server HTML", never "no AI can read this": Google renders JavaScript, so its
surfaces may see things ChatGPT and Claude cannot (PLATFORM_DELIVERY.md §7).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from diagnose.html_utils import ParsedPage


@dataclass
class RenderCheck:
    html_bytes: int
    word_count: int                 # main content
    word_count_total: int
    text_ratio: float               # readable text bytes / html bytes
    h1_texts: list[str]
    jsonld_block_count: int
    empty_mount_div: bool
    noscript_warning: bool
    details: dict = field(default_factory=dict)   # name/phone/address: expected + where found
    verdict: str = "ok"             # content_missing | likely_missing | fine | ok
    reasons: list[str] = field(default_factory=list)

    @property
    def h1_nonempty(self) -> int:
        return sum(1 for t in self.h1_texts if t)

    def to_json(self) -> dict:
        return {
            "html_bytes": self.html_bytes, "word_count": self.word_count,
            "word_count_total": self.word_count_total, "text_ratio": round(self.text_ratio, 4),
            "h1_texts": self.h1_texts, "jsonld_block_count": self.jsonld_block_count,
            "empty_mount_div": self.empty_mount_div, "noscript_warning": self.noscript_warning,
            "details": self.details, "verdict": self.verdict, "reasons": self.reasons,
        }


def normalise(text: str) -> str:
    """Lowercase, accents removed, punctuation turned into single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _phone_digits_in(text: str) -> list[str]:
    """All phone-looking runs in the text, reduced to digits."""
    runs = re.findall(r"\+?\d[\d\s().\-]{6,}\d", text)
    return [re.sub(r"\D", "", r) for r in runs]


def _found(kind: str, expected: str, page: ParsedPage, raw_html: str) -> tuple[bool, bool]:
    """(found in the readable text, found anywhere in the raw HTML)."""
    if kind == "phone":
        want = re.sub(r"\D", "", expected)[-10:]
        if len(want) < 7:
            return False, False
        in_text = any(want in d for d in _phone_digits_in(page.visible_text))
        in_html = any(want in d for d in _phone_digits_in(raw_html))
        return in_text, in_html
    if kind == "address":
        # The street line is what an AI would quote. Use the part before the first comma.
        want = normalise(expected.split(",")[0])
        if len(want) < 4:
            return False, False
    else:  # name: any of the known spellings
        want = normalise(expected)
        if not want:
            return False, False
    return want in normalise(page.visible_text), want in normalise(raw_html)


def check_render(
    page: ParsedPage,
    raw_html: str,
    *,
    names: list[str] | None = None,
    phone: str | None = None,
    address: str | None = None,
) -> RenderCheck:
    html_bytes = len(raw_html.encode("utf-8", errors="replace"))
    text_bytes = len(page.visible_text.encode("utf-8", errors="replace"))
    noscript = bool(re.search(r"javascript (must be|needs to be|is required)|enable javascript|requires javascript",
                              page.noscript_text, re.I))
    check = RenderCheck(
        html_bytes=html_bytes,
        word_count=page.word_count_main,
        word_count_total=page.word_count_total,
        text_ratio=(text_bytes / html_bytes) if html_bytes else 0.0,
        h1_texts=page.h1_texts,
        jsonld_block_count=len(page.jsonld_raw),
        empty_mount_div=page.empty_mount_div,
        noscript_warning=noscript,
    )

    # Which business facts are in the HTML? (Only the ones we know to look for.)
    for kind, expected in (("name", names), ("phone", phone), ("address", address)):
        if not expected:
            check.details[kind] = {"checked": False}
            continue
        if kind == "name":
            hits = [_found("name", n, page, raw_html) for n in expected]
            in_text = any(h[0] for h in hits)
            in_html = any(h[1] for h in hits)
            shown = expected
        else:
            in_text, in_html = _found(kind, expected, page, raw_html)
            shown = expected
        check.details[kind] = {"checked": True, "expected": shown,
                               "in_readable_text": in_text, "in_raw_html": in_html}

    # Is the page client-rendered? PLATFORM_DELIVERY.md §7 draft rules. A page is
    # either basically fine or basically empty, so this is a label, not a score.
    words = page.word_count_main
    if (page.empty_mount_div and words < 150) or (words < 100 and html_bytes > 20_000) or (noscript and words < 150):
        check.verdict = "content_missing"
        check.reasons.append(
            f"only {words} readable words in {html_bytes:,} bytes of HTML"
            + (", an empty app mount point" if page.empty_mount_div else "")
            + (", and a 'JavaScript required' notice" if noscript else "")
        )
    elif check.text_ratio < 0.05 and html_bytes > 5_000:
        check.verdict = "likely_missing"
        check.reasons.append(f"readable text is only {check.text_ratio:.1%} of the HTML")
    elif words > 400 and check.h1_nonempty >= 1:
        check.verdict = "fine"
    return check
