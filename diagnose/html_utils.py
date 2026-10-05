"""Reads one page of raw HTML (no JavaScript is ever run) and pulls out the
few things `aeo diagnose` needs: readable text, <h1> texts, JSON-LD blocks,
<meta> tags, and a few signs of a client-rendered app.

Built on Python's standard-library HTMLParser so there is no extra
dependency. It is forgiving about broken HTML, which real sites are full of.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Text inside these tags is never "readable page content".
_SKIP_TAGS = {"script", "style", "template", "svg", "title"}
# Page furniture. Words inside these don't count as the page's main content.
_CHROME_TAGS = {"nav", "header", "footer", "aside"}
# The ids that single-page-app frameworks mount into (PLATFORM_DELIVERY.md §7).
_MOUNT_IDS = {"root", "app", "__next", "__nuxt", "___gatsby"}


@dataclass
class ParsedPage:
    visible_text: str = ""          # everything a reader sees, in order
    main_text: str = ""             # <main> if present, else body minus nav/header/footer/aside
    word_count_total: int = 0
    word_count_main: int = 0
    h1_texts: list[str] = field(default_factory=list)   # one entry per <h1>, "" if empty
    jsonld_raw: list[str] = field(default_factory=list)  # each block exactly as found
    meta: dict[str, str] = field(default_factory=dict)   # lowercase name -> content
    html_attrs: dict[str, str] = field(default_factory=dict)  # attributes on <html>
    noscript_text: str = ""
    empty_mount_div: bool = False   # a framework mount point with no text inside


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = ParsedPage()
        self._skip_depth = 0
        self._noscript_depth = 0
        self._chrome_depth = 0
        self._main_depth = 0
        self._h1_depth = 0
        self._h1_buf: list[str] = []
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self._mount_depth = 0  # >0 while inside a framework mount <div>
        self._mount_words = 0
        self._mount_seen = False
        self._all: list[str] = []
        self._main: list[str] = []
        self._body_no_chrome: list[str] = []
        self._noscript: list[str] = []

    # -- tags ------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}

        if tag == "html":
            self.page.html_attrs = a
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name and "content" in a:
                self.page.meta.setdefault(name, a["content"])

        if tag == "script" and a.get("type", "").lower().strip() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = []
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "noscript":
            self._noscript_depth += 1
        if tag in _CHROME_TAGS:
            self._chrome_depth += 1
        if tag == "main" or a.get("role") == "main":
            self._main_depth += 1
        if tag == "h1":
            self._h1_depth += 1
            self._h1_buf = []

        # Track a framework mount <div>, counting nested divs so we know when it closes.
        if tag == "div":
            if self._mount_depth:
                self._mount_depth += 1
            elif a.get("id") in _MOUNT_IDS:
                self._mount_depth = 1
                self._mount_words = 0
                self._mount_seen = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <meta ... /> style tags: no matching end tag will follow.
        if tag in ("meta", "html"):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.page.jsonld_raw.append("".join(self._jsonld_buf))
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "noscript" and self._noscript_depth:
            self._noscript_depth -= 1
        if tag in _CHROME_TAGS and self._chrome_depth:
            self._chrome_depth -= 1
        if tag == "main" and self._main_depth:
            self._main_depth -= 1
        if tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
            self.page.h1_texts.append(" ".join("".join(self._h1_buf).split()))
        if tag == "div" and self._mount_depth:
            self._mount_depth -= 1
            if self._mount_depth == 0 and self._mount_words == 0:
                self.page.empty_mount_div = True

    # -- text ------------------------------------------------------------

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._noscript_depth:
            self._noscript.append(data)
            return
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        self._all.append(text)
        if self._main_depth:
            self._main.append(text)
        if not self._chrome_depth:
            self._body_no_chrome.append(text)
        if self._h1_depth:
            self._h1_buf.append(data)
        if self._mount_depth:
            self._mount_words += len(text.split())


def parse_page(html: str) -> ParsedPage:
    parser = _Parser()
    parser.feed(html)
    parser.close()
    page = parser.page
    page.visible_text = " ".join(parser._all)
    main_text = " ".join(parser._main)
    # A page with a <main> that has words in it: use that. Otherwise fall back
    # to the body with the obvious furniture taken out.
    page.main_text = main_text if main_text.split() else " ".join(parser._body_no_chrome)
    page.word_count_total = len(page.visible_text.split())
    page.word_count_main = len(page.main_text.split())
    page.noscript_text = " ".join(" ".join(parser._noscript).split())
    return page
