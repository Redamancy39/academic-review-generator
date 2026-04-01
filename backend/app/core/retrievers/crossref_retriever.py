# Crossref retriever implementation
import re
from typing import Any, Dict, List, Sequence

import requests

from ..models import CURRENT_YEAR, PaperRecord, RunConfig
from .base import BaseRetriever
from .wos_retriever import normalize_title, coerce_int


class CrossrefRetriever(BaseRetriever):
    """Retriever for Crossref API."""

    @property
    def source_name(self) -> str:
        return "Crossref"

    def __init__(self, config: RunConfig, session: requests.Session) -> None:
        super().__init__(config, session)
        self.api_base = "https://api.crossref.org/works"
        self.headers = {
            "User-Agent": "academic-review-system/1.0",
            "Accept": "application/json",
        }

    def fetch(self, queries: Sequence[Dict[str, Any]]) -> List[PaperRecord]:
        """Fetch papers from Crossref API."""
        records: List[PaperRecord] = []
        since_year = CURRENT_YEAR - self.config.year_window + 1
        # 根据检索池大小调整每个查询返回的数量
        per_query = min(30, max(10, self.config.retrieval_pool_size // len(queries))) if queries else 20
        for query_def in queries[:6]:  # 增加到6个查询
            try:
                response = self.session.get(
                    self.api_base,
                    headers=self.headers,
                    params={
                        "query": query_def.get("query", query_def.get("intent", "")),
                        "rows": per_query,
                        "filter": f"from-pub-date:{since_year}-01-01,type:journal-article",
                        "sort": "relevance",
                        "order": "desc",
                    },
                    timeout=self.config.request_timeout,
                )
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("message", {}).get("items", []):
                    records.append(self._parse_item(item))
            except Exception:
                continue
        return records

    def _parse_item(self, item: Dict[str, Any]) -> PaperRecord:
        """Parse a single Crossref record."""
        title = " ".join(item.get("title") or [])
        authors = []
        for author in item.get("author", []):
            given = author.get("given", "")
            family = author.get("family", "")
            full_name = " ".join(part for part in [given, family] if part).strip()
            if full_name:
                authors.append(full_name)
        issued = item.get("issued", {}).get("date-parts", [[None]])
        year = coerce_int(issued[0][0] if issued and issued[0] else None)
        abstract = re.sub(r"<[^>]+>", "", item.get("abstract", "") or "")
        return PaperRecord(
            ref_id=f"CR-{normalize_title(title)[:12]}",
            title=title,
            authors=authors[:10],
            year=year or None,
            journal=" ".join(item.get("container-title") or []),
            doi=item.get("DOI", "") or "",
            url=item.get("URL", "") or "",
            abstract=abstract,
            keywords=item.get("subject") or [],
            source_db="Crossref",
            jcr_quartile="Unknown",
            document_type=str(item.get("type") or "article"),
            times_cited=0,
            language=str(item.get("language") or "English"),
            is_recent=bool(year and year >= CURRENT_YEAR - self.config.year_window + 1),
            is_high_tier=False,
        )
