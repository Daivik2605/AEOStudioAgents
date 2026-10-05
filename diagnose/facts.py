"""Everything `aeo diagnose` measured, in one place, so the decision rules and
the findings builder read from the same object."""

from __future__ import annotations

from dataclasses import dataclass, field

from diagnose.crawler import CrawlerTest
from diagnose.fetch import FetchResult
from diagnose.platform_detect import Detection
from diagnose.render import RenderCheck
from diagnose.robots import RobotsCheck
from diagnose.structured_data import SchemaAnalysis


@dataclass
class Facts:
    url: str
    page: FetchResult                       # the page as fetched by the browser control
    detection: Detection
    business: dict | None = None            # id, name, domain, status, alternate_names, phone, address
    crawler: CrawlerTest | None = None
    robots: RobotsCheck | None = None
    llms: FetchResult | None = None         # /llms.txt as fetched by the browser control
    render: RenderCheck | None = None
    schema: SchemaAnalysis | None = None
    jsonld_raw: list[str] = field(default_factory=list)
    site_reachable: bool = True             # False if the browser control got no usable page at all

    @property
    def platform(self) -> str | None:
        return self.detection.platform

    @property
    def business_names(self) -> list[str]:
        if not self.business:
            return []
        return [n for n in [self.business.get("name"), *(self.business.get("alternate_names") or [])] if n]
