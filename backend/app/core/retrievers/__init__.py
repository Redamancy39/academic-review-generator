# Retriever module - unified interface
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from ..models import PaperRecord, RunConfig
from .base import BaseRetriever
from .crossref_retriever import CrossrefRetriever
from .openalex_retriever import OpenAlexRetriever
from .pubmed_retriever import PubMedRetriever
from .wos_retriever import WOSRetriever, normalize_title


def create_session() -> requests.Session:
    """Create a configured requests session."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "academic-review-system/1.0",
        "Accept": "application/json",
    })
    return session


def score_paper(record: PaperRecord, config: RunConfig, terms: Sequence[str]) -> float:
    """Calculate relevance score for a paper with enhanced domain checking."""
    text = f"{record.title} {' '.join(record.keywords)} {record.abstract}".lower()

    # Base relevance from term matching
    relevance_hits = sum(1 for term in terms if term.lower() in text)

    # Title match bonus (more important)
    title_hits = sum(1 for term in terms if term.lower() in record.title.lower())
    title_bonus = title_hits * 1.5

    # Abstract match
    abstract_hits = 0
    if record.abstract:
        abstract_hits = sum(1 for term in terms if term.lower() in record.abstract.lower())

    # Time and quality bonuses
    recent_bonus = 2.0 if record.is_recent else -1.0
    tier_bonus = 2.5 if record.is_high_tier else 0.5 if record.jcr_quartile == "Unknown" else -0.5
    citation_bonus = min(math.log1p(max(record.times_cited, 0)), 3.0)
    source_bonus = 2.0 if record.source_db == "WOS" else 1.0
    abstract_bonus = 1.0 if record.abstract else 0.0
    type_bonus = 0.6 if "review" in record.document_type.lower() or "article" in record.document_type.lower() else -0.2

    # Domain exclusion penalty
    exclusion_keywords = [
        "clinical trial", "patient", "diagnosis", "surgery", "treatment",
        "stock market", "financial", "investment", "cryptocurrency",
        "climate change", "air pollution", "wireless communication",
        "5g network", "power grid", "solar panel", "battery management",
    ]
    exclusion_penalty = 0
    for kw in exclusion_keywords:
        if kw in text:
            exclusion_penalty += 2.0

    # Domain relevance bonus
    domain_keywords = [
        "food", "meat", "fruit", "vegetable", "freshness", "quality",
        "safety", "spoilage", "detection", "agricultural", "crop",
    ]
    domain_bonus = 0
    for kw in domain_keywords:
        if kw in text:
            domain_bonus += 0.5
        if kw in record.title.lower():
            domain_bonus += 1.0

    score = (
        relevance_hits * 0.8 +
        title_bonus +
        abstract_hits * 0.3 +
        recent_bonus +
        tier_bonus +
        citation_bonus +
        source_bonus +
        abstract_bonus +
        type_bonus +
        domain_bonus -
        exclusion_penalty
    )

    return round(max(0, score), 3)


def choose_better_record(left: PaperRecord, right: PaperRecord) -> PaperRecord:
    """Choose the better record when duplicates are found."""
    left_score = (
        (1 if left.source_db == "WOS" else 0)
        + (1 if left.abstract else 0)
        + (1 if left.is_high_tier else 0)
        + left.times_cited / 100.0
    )
    right_score = (
        (1 if right.source_db == "WOS" else 0)
        + (1 if right.abstract else 0)
        + (1 if right.is_high_tier else 0)
        + right.times_cited / 100.0
    )
    return right if right_score > left_score else left


def deduplicate_records(records: Sequence[PaperRecord]) -> List[PaperRecord]:
    """Deduplicate records by DOI or title."""
    deduped: Dict[str, PaperRecord] = {}
    for record in records:
        key = record.doi.lower().strip() if record.doi else normalize_title(record.title)
        existing = deduped.get(key)
        deduped[key] = choose_better_record(existing, record) if existing else record
    result = list(deduped.values())
    for index, record in enumerate(result, start=1):
        record.ref_id = f"REF{index:03d}"
    return result


class RetrieverManager:
    """Manager for multiple retrievers."""

    def __init__(
        self,
        config: RunConfig,
        wos_api_key: Optional[str] = None,
        source_progress_callback: Optional[callable] = None,
    ) -> None:
        self.config = config
        self.session = create_session()
        self.retrievers: Dict[str, BaseRetriever] = {}
        self.source_progress_callback = source_progress_callback

        # Initialize available retrievers
        if wos_api_key:
            self.retrievers["WOS"] = WOSRetriever(config, self.session, wos_api_key)
        self.retrievers["OpenAlex"] = OpenAlexRetriever(config, self.session)
        self.retrievers["Crossref"] = CrossrefRetriever(config, self.session)
        self.retrievers["PubMed"] = PubMedRetriever(config, self.session)

    def _report_source_progress(self, source: str, status: str, count: int = 0, error: Optional[str] = None) -> None:
        """Report progress for a data source."""
        if self.source_progress_callback:
            self.source_progress_callback(source, status, count, error)

    def fetch_all(
        self,
        queries: Sequence[Dict[str, Any]],
        sources: Optional[List[str]] = None,
    ) -> Tuple[List[PaperRecord], List[str]]:
        """Fetch papers from all configured sources.

        Args:
            queries: List of query definitions.
            sources: Optional list of source names to use. If None, uses all available.

        Returns:
            Tuple of (deduplicated records, notices).
        """
        all_records: List[PaperRecord] = []
        notices: List[str] = []

        sources = sources or list(self.retrievers.keys())

        for source_name in sources:
            retriever = self.retrievers.get(source_name)
            if not retriever:
                notices.append(f"Unknown source: {source_name}")
                continue

            # Report source started
            self._report_source_progress(source_name, "running", 0)

            try:
                records = retriever.fetch(queries)
                all_records.extend(records)
                # Report source completed
                self._report_source_progress(source_name, "completed", len(records))
            except Exception as exc:
                notices.append(f"{source_name} fetch failed: {exc}")
                # Report source failed
                self._report_source_progress(source_name, "failed", 0, str(exc))

        # Deduplicate and score
        deduped = deduplicate_records(all_records)
        return deduped, notices

    def close(self) -> None:
        """Close the session."""
        self.session.close()
