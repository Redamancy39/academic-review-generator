# Citation Tracker Service - forward and backward citation tracking
import asyncio
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from ..core.models import CURRENT_YEAR, PaperRecord, RunConfig


class CitationTracker:
    """Service for tracking citations (forward and backward) using OpenAlex API."""

    def __init__(self, config: RunConfig, session: Optional[requests.Session] = None) -> None:
        """Initialize the citation tracker.

        Args:
            config: Run configuration.
            session: Optional requests session for connection pooling.
        """
        self.config = config
        self.session = session or requests.Session()
        self.api_base = "https://api.openalex.org"
        self.headers = {
            "User-Agent": "academic-review-system/1.0",
            "Accept": "application/json",
        }

    def forward_tracking(
        self,
        paper_ids: List[str],
        max_results: int = 50,
        year_from: Optional[int] = None,
    ) -> List[PaperRecord]:
        """Find papers that cite the given papers (forward citation tracking).

        Args:
            paper_ids: List of OpenAlex paper IDs (e.g., ["W1234567890"]).
            max_results: Maximum number of results to return.
            year_from: Minimum publication year for citing papers.

        Returns:
            List of PaperRecords for papers that cite the input papers.
        """
        if not paper_ids:
            return []

        citing_papers: List[PaperRecord] = []
        seen_ids: Set[str] = set()

        # Build query - papers that cite any of the given papers
        # OpenAlex uses the "cited_by" filter for forward tracking
        for paper_id in paper_ids[:5]:  # Limit to 5 seed papers
            try:
                # Normalize paper ID to OpenAlex format
                if not paper_id.startswith("W") and not paper_id.startswith("https://"):
                    # Assume it's an OpenAlex ID without the W prefix
                    paper_id = f"W{paper_id}"

                if paper_id.startswith("W"):
                    openalex_id = f"https://openalex.org/{paper_id}"
                else:
                    openalex_id = paper_id

                params = {
                    "filter": f"cites:{openalex_id}",
                    "sort": "cited_by_count:desc,publication_year:desc",
                    "per-page": min(25, max_results // len(paper_ids) + 1),
                }

                if year_from:
                    params["filter"] += f",publication_year:>{year_from - 1}"

                response = self.session.get(
                    f"{self.api_base}/works",
                    headers=self.headers,
                    params=params,
                    timeout=self.config.request_timeout,
                )
                response.raise_for_status()
                data = response.json()

                for item in data.get("results", []):
                    work_id = item.get("id", "").split("/")[-1]
                    if work_id not in seen_ids:
                        seen_ids.add(work_id)
                        citing_papers.append(self._parse_work(item))

            except Exception as e:
                print(f"Warning: Forward tracking failed for {paper_id}: {e}")
                continue

        # Sort by citation count and return top results
        citing_papers.sort(key=lambda r: r.times_cited, reverse=True)
        return citing_papers[:max_results]

    def backward_tracking(
        self,
        paper_ids: List[str],
        max_results: int = 50,
    ) -> List[PaperRecord]:
        """Find papers cited by the given papers (backward citation tracking).

        Args:
            paper_ids: List of OpenAlex paper IDs.
            max_results: Maximum number of results to return.

        Returns:
            List of PaperRecords for papers that are cited by the input papers.
        """
        if not paper_ids:
            return []

        cited_papers: List[PaperRecord] = []
        seen_ids: Set[str] = set()

        for paper_id in paper_ids[:5]:  # Limit to 5 seed papers
            try:
                # Normalize paper ID
                if not paper_id.startswith("W") and not paper_id.startswith("https://"):
                    paper_id = f"W{paper_id}"

                if paper_id.startswith("W"):
                    openalex_id = f"https://openalex.org/{paper_id}"
                else:
                    openalex_id = paper_id

                # Get the paper details to extract referenced_works
                response = self.session.get(
                    f"{self.api_base}/works/{openalex_id}",
                    headers=self.headers,
                    timeout=self.config.request_timeout,
                )
                response.raise_for_status()
                data = response.json()

                referenced_works = data.get("referenced_works", [])

                # Fetch details for referenced works
                for ref_id in referenced_works[:max_results]:
                    if ref_id in seen_ids:
                        continue
                    seen_ids.add(ref_id)

                    try:
                        ref_response = self.session.get(
                            ref_id,
                            headers=self.headers,
                            timeout=self.config.request_timeout,
                        )
                        ref_response.raise_for_status()
                        ref_data = ref_response.json()
                        cited_papers.append(self._parse_work(ref_data))
                    except Exception:
                        continue

            except Exception as e:
                print(f"Warning: Backward tracking failed for {paper_id}: {e}")
                continue

        return cited_papers[:max_results]

    def get_citation_network(
        self,
        seed_paper_ids: List[str],
        depth: int = 1,
        max_papers: int = 100,
    ) -> Tuple[List[PaperRecord], Dict[str, List[str]]]:
        """Build a citation network around seed papers.

        Args:
            seed_paper_ids: List of seed paper IDs.
            depth: How many levels to expand (1 = immediate neighbors only).
            max_papers: Maximum total papers to return.

        Returns:
            Tuple of (list of papers, adjacency dict of citation relationships).
        """
        all_papers: Dict[str, PaperRecord] = {}
        citation_edges: Dict[str, List[str]] = {}  # paper_id -> list of cited paper_ids

        current_level = seed_paper_ids
        for level in range(depth):
            next_level: List[str] = []

            for paper_id in current_level:
                if paper_id in all_papers:
                    continue

                # Get paper details
                try:
                    if not paper_id.startswith("W") and not paper_id.startswith("https://"):
                        paper_id = f"W{paper_id}"

                    if paper_id.startswith("W"):
                        openalex_id = f"https://openalex.org/{paper_id}"
                    else:
                        openalex_id = paper_id

                    response = self.session.get(
                        f"{self.api_base}/works/{openalex_id}",
                        headers=self.headers,
                        timeout=self.config.request_timeout,
                    )
                    response.raise_for_status()
                    data = response.json()

                    work_id = data.get("id", "").split("/")[-1]
                    if work_id not in all_papers:
                        all_papers[work_id] = self._parse_work(data)

                    # Get referenced works (backward citations)
                    referenced = data.get("referenced_works", [])
                    citation_edges[work_id] = [r.split("/")[-1] for r in referenced]
                    next_level.extend([r.split("/")[-1] for r in referenced[:10]])

                except Exception as e:
                    print(f"Warning: Failed to get paper {paper_id}: {e}")
                    continue

            current_level = next_level

            if len(all_papers) >= max_papers:
                break

        return list(all_papers.values())[:max_papers], citation_edges

    def find_highly_cited_in_network(
        self,
        papers: List[PaperRecord],
        citation_edges: Dict[str, List[str]],
        top_n: int = 10,
    ) -> List[Tuple[PaperRecord, int]]:
        """Find papers that are most cited within the network.

        Args:
            papers: List of papers in the network.
            citation_edges: Citation adjacency dict.
            top_n: Number of top papers to return.

        Returns:
            List of (paper, citation_count_within_network) tuples.
        """
        # Count in-network citations
        in_network_citations: Dict[str, int] = {}
        paper_map = {p.ref_id.replace("OA-", "").replace("W", ""): p for p in papers}

        for citing_id, cited_ids in citation_edges.items():
            for cited_id in cited_ids:
                cited_short = cited_id.split("/")[-1].replace("W", "")
                in_network_citations[cited_short] = in_network_citations.get(cited_short, 0) + 1

        # Sort by in-network citation count
        results = []
        for paper_id, count in sorted(in_network_citations.items(), key=lambda x: -x[1]):
            if paper_id in paper_map:
                results.append((paper_map[paper_id], count))
            if len(results) >= top_n:
                break

        return results

    def _parse_work(self, item: Dict[str, Any]) -> PaperRecord:
        """Parse OpenAlex work item to PaperRecord.

        Args:
            item: OpenAlex work API response item.

        Returns:
            PaperRecord instance.
        """
        authors = []
        for authorship in item.get("authorships", []):
            author = authorship.get("author", {})
            if author.get("display_name"):
                authors.append(author["display_name"])

        year = item.get("publication_year")
        location = item.get("primary_location") or {}
        source = location.get("source") or {}

        # Parse abstract
        abstract = item.get("abstract_inverted_index")
        abstract_text = ""
        if isinstance(abstract, dict):
            slots: List[Tuple[int, str]] = []
            for token, positions in abstract.items():
                for position in positions:
                    slots.append((position, token))
            abstract_text = " ".join(token for _, token in sorted(slots))

        # Determine quartile
        quartile = "Unknown"
        x_indexed = source.get("x_indexed_in") or []
        if "JCR_Q1" in x_indexed:
            quartile = "Q1"
        elif "JCR_Q2" in x_indexed:
            quartile = "Q2"
        elif "JCR_Q3" in x_indexed:
            quartile = "Q3"
        elif "JCR_Q4" in x_indexed:
            quartile = "Q4"

        work_id = item.get("id", "").split("/")[-1] or "unknown"

        return PaperRecord(
            ref_id=f"OA-{work_id}",
            title=item.get("title", ""),
            authors=authors[:10],
            year=year,
            journal=source.get("display_name", ""),
            doi=(item.get("doi") or "").replace("https://doi.org/", ""),
            url=location.get("landing_page_url", "") or item.get("id", ""),
            abstract=abstract_text,
            keywords=[c.get("display_name", "") for c in item.get("concepts", [])[:8]],
            source_db="OpenAlex",
            jcr_quartile=quartile,
            document_type=str(item.get("type") or "article"),
            times_cited=item.get("cited_by_count", 0),
            language="English",
            is_recent=bool(year and year >= CURRENT_YEAR - self.config.year_window + 1),
            is_high_tier=quartile in {"Q1", "Q2"},
        )


def expand_with_citations(
    records: List[PaperRecord],
    config: RunConfig,
    session: Optional[requests.Session] = None,
    forward: bool = True,
    backward: bool = True,
    max_papers: int = 50,
) -> List[PaperRecord]:
    """Expand a list of papers using citation tracking.

    Args:
        records: Initial list of paper records.
        config: Run configuration.
        session: Optional requests session.
        forward: Whether to do forward tracking.
        backward: Whether to do backward tracking.
        max_papers: Maximum additional papers to return.

    Returns:
        Extended list of paper records.
    """
    if not records:
        return records

    tracker = CitationTracker(config, session)

    # Extract OpenAlex IDs from records
    paper_ids = []
    for r in records:
        if r.source_db == "OpenAlex" and r.ref_id.startswith("OA-"):
            paper_ids.append(r.ref_id.replace("OA-", ""))

    if not paper_ids:
        return records

    expanded: List[PaperRecord] = []
    seen_ids = {r.ref_id for r in records}

    if forward:
        citing_papers = tracker.forward_tracking(paper_ids[:5], max_results=max_papers // 2)
        for paper in citing_papers:
            if paper.ref_id not in seen_ids:
                expanded.append(paper)
                seen_ids.add(paper.ref_id)

    if backward:
        cited_papers = tracker.backward_tracking(paper_ids[:5], max_results=max_papers // 2)
        for paper in cited_papers:
            if paper.ref_id not in seen_ids:
                expanded.append(paper)
                seen_ids.add(paper.ref_id)

    # Combine and return
    return records + expanded[:max_papers]
