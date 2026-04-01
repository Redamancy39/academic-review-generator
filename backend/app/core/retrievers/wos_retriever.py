# WOS (Web of Science) retriever implementation
import re
from typing import Any, Dict, List, Optional, Sequence

import requests

from ..models import CURRENT_YEAR, PaperRecord, RunConfig
from .base import BaseRetriever


def normalize_title(title: str) -> str:
    """Normalize title for comparison."""
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def extract_authors(raw: Any) -> List[str]:
    """Extract author names from various formats."""
    authors: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("display_name") or item.get("full_name") or item.get("name")
                if name:
                    authors.append(str(name))
            elif isinstance(item, str):
                authors.append(item)
    elif isinstance(raw, dict):
        for key in ("authors", "author", "names"):
            if key in raw:
                authors.extend(extract_authors(raw[key]))
    return authors[:10]


def coerce_int(value: Any, default: int = 0) -> int:
    """Safely convert value to integer."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_quartile(value: Any) -> str:
    """Normalize JCR quartile string."""
    text = str(value or "").upper().strip()
    for candidate in ("Q1", "Q2", "Q3", "Q4"):
        if candidate in text:
            return candidate
    return "Unknown"


class WOSRetriever(BaseRetriever):
    """Retriever for Web of Science API."""

    @property
    def source_name(self) -> str:
        return "WOS"

    def __init__(
        self,
        config: RunConfig,
        session: requests.Session,
        api_key: str,
        api_base: str = None,
    ) -> None:
        super().__init__(config, session)
        self.api_key = api_key
        self.api_base = (api_base or config.wos_api_base).rstrip("/")
        self.headers = {
            "User-Agent": "academic-review-system/1.0",
            "Accept": "application/json",
            "X-ApiKey": api_key,
        }

    def fetch(self, queries: Sequence[Dict[str, Any]]) -> List[PaperRecord]:
        """Fetch papers from WOS API."""
        records: List[PaperRecord] = []
        for query_def in queries:
            try:
                response = self.session.get(
                    self.api_base,
                    headers=self.headers,
                    params={"db": "WOS", "q": query_def["query"], "limit": 20, "page": 1},
                    timeout=self.config.request_timeout,
                )
                response.raise_for_status()
                payload = response.json()
                raw_hits = payload.get("hits") or payload.get("data") or payload.get("records") or []
                for item in raw_hits:
                    try:
                        records.append(self._parse_item(item))
                    except Exception:
                        continue
            except Exception:
                continue
        return records

    def _parse_item(self, item: Dict[str, Any]) -> PaperRecord:
        """Parse a single WOS record."""
        identifiers = item.get("identifiers", {}) if isinstance(item.get("identifiers"), dict) else {}
        source = item.get("source", {}) if isinstance(item.get("source"), dict) else {}
        title = (
            item.get("title")
            or item.get("titles", {}).get("title")
            or item.get("title_summary", {}).get("title")
            or ""
        )
        authors = extract_authors(item.get("names") or item.get("authors") or item.get("author"))
        year = coerce_int(
            item.get("published")
            or item.get("publishYear")
            or item.get("source", {}).get("publishYear")
            or item.get("year")
        )
        doi = identifiers.get("doi") or item.get("doi") or ""
        url = (
            item.get("links", {}).get("record")
            if isinstance(item.get("links"), dict)
            else item.get("url") or ""
        )
        journal = source.get("sourceTitle") or source.get("title") or item.get("journal") or ""
        abstract = item.get("abstract") or item.get("abstract_text") or ""
        keywords = item.get("keywords") or source.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [kw.strip() for kw in keywords.split(";") if kw.strip()]
        quartile = normalize_quartile(
            item.get("jcr_quartile")
            or item.get("jcrQuartile")
            or source.get("jcr_quartile")
            or source.get("quartile")
        )
        return PaperRecord(
            ref_id=str(item.get("uid") or f"WOS-{normalize_title(title)[:12]}"),
            title=title,
            authors=authors,
            year=year or None,
            journal=journal,
            doi=doi,
            url=url,
            abstract=abstract,
            keywords=list(keywords),
            source_db="WOS",
            jcr_quartile=quartile,
            document_type=str(item.get("document_type") or item.get("doctype") or "article"),
            times_cited=coerce_int(item.get("timesCited") or item.get("citations")),
            language=str(item.get("language") or "English"),
            is_recent=bool(year and year >= CURRENT_YEAR - self.config.year_window + 1),
            is_high_tier=quartile in {"Q1", "Q2"},
        )
