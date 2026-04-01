# PubMed retriever implementation
import re
import time
from typing import Any, Dict, List, Sequence
from xml.etree import ElementTree

import requests

from ..models import CURRENT_YEAR, PaperRecord, RunConfig
from .base import BaseRetriever
from .wos_retriever import normalize_title


class PubMedRetriever(BaseRetriever):
    """Retriever for PubMed/MEDLINE API (E-utilities).

    PubMed is excellent for food science and nutrition research,
    as it indexes journals in medicine, biology, and food science.
    """

    @property
    def source_name(self) -> str:
        return "PubMed"

    def __init__(self, config: RunConfig, session: requests.Session) -> None:
        super().__init__(config, session)
        self.search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        self.summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        self.fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        self.headers = {
            "User-Agent": "academic-review-system/1.0",
            "Accept": "application/json",
        }
        self._last_request_time = 0

    def fetch(self, queries: Sequence[Dict[str, Any]]) -> List[PaperRecord]:
        """Fetch papers from PubMed API with precision search."""
        records: List[PaperRecord] = []
        since_year = CURRENT_YEAR - self.config.year_window + 1

        for query_def in queries[:4]:  # 限制查询数量，避免触发限流
            try:
                # 构建精确的 PubMed 查询
                search_term = self._build_pubmed_query(query_def, since_year)

                self._rate_limit()

                # Step 1: Search for PMIDs
                search_response = self.session.get(
                    self.search_url,
                    headers=self.headers,
                    params={
                        "db": "pubmed",
                        "term": search_term,
                        "retmode": "json",
                        "retmax": 25,
                        "sort": "relevance",
                    },
                    timeout=self.config.request_timeout,
                )
                search_response.raise_for_status()
                search_data = search_response.json()

                pmids = search_data.get("esearchresult", {}).get("idlist", [])
                if not pmids:
                    continue

                # Step 2: Fetch summaries
                self._rate_limit()
                summary_response = self.session.get(
                    self.summary_url,
                    headers=self.headers,
                    params={
                        "db": "pubmed",
                        "id": ",".join(pmids),
                        "retmode": "json",
                    },
                    timeout=self.config.request_timeout,
                )
                summary_response.raise_for_status()
                summary_data = summary_response.json()

                result = summary_data.get("result", {})
                for uid in pmids:
                    item = result.get(uid)
                    if item and isinstance(item, dict):
                        record = self._parse_item(item)
                        # 计算相关性
                        record.relevance_score = self._compute_relevance(
                            record, query_def.get("query", "")
                        )
                        records.append(record)

            except Exception as e:
                print(f"PubMed query failed: {e}")
                continue

        return records

    def _rate_limit(self):
        """Ensure we don't exceed NCBI rate limits (3 requests/second)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.34:
            time.sleep(0.34 - elapsed)
        self._last_request_time = time.time()

    def _build_pubmed_query(self, query_def: Dict[str, Any], since_year: int) -> str:
        """Build PubMed search query with field tags for precision.

        PubMed supports field-specific searching:
        - [Title/Abstract]: search in title and abstract
        - [MeSH]: search in MeSH terms
        - [Filter]: apply filters
        """
        original_query = query_def.get("query", "")

        # 提取关键词
        keywords = self._extract_keywords(original_query)

        if not keywords:
            return original_query

        # 使用 [Title/Abstract] 限定搜索字段，提高精确度
        terms = []
        for kw in keywords[:10]:
            if len(kw) > 2:
                terms.append(f'"{kw}"[Title/Abstract]')

        # 组合查询词
        query_parts = " OR ".join(terms)

        # 添加日期过滤
        date_filter = f"{since_year}:{CURRENT_YEAR}[pdat]"

        # 最终查询
        final_query = f"({query_parts}) AND {date_filter}"

        return final_query

    def _extract_keywords(self, query: str) -> List[str]:
        """Extract meaningful keywords from query string."""
        # 移除布尔运算符
        query = re.sub(r'\b(OR|AND|NOT)\b', ' ', query, flags=re.IGNORECASE)
        query = re.sub(r'[()"]+', ' ', query)

        # 提取单词
        words = query.split()

        # 过滤停用词和短词
        stopwords = {
            'the', 'a', 'an', 'for', 'with', 'and', 'or', 'not',
            'in', 'on', 'at', 'to', 'of', 'by', 'from', 'as',
            'using', 'based', 'study', 'research', 'analysis',
            'review', 'method', 'methods', 'approach', 'novel',
        }

        keywords = []
        for w in words:
            w = w.strip().lower()
            if w and len(w) > 2 and w not in stopwords:
                keywords.append(w)

        return keywords[:12]

    def _compute_relevance(self, record: PaperRecord, search_term: str) -> float:
        """Compute relevance score based on title/abstract matching."""
        score = 0.0

        keywords = set(self._extract_keywords(search_term))
        title_lower = (record.title or "").lower()

        # 标题匹配
        title_matches = sum(1 for kw in keywords if kw in title_lower)

        if keywords:
            title_ratio = title_matches / len(keywords)
            score = title_ratio * 8  # 最高8分来自标题匹配

        # 新近度加成
        if record.year and record.year >= CURRENT_YEAR - 2:
            score *= 1.1

        return min(10.0, round(score, 2))

    def _parse_item(self, item: Dict[str, Any]) -> PaperRecord:
        """Parse a single PubMed record."""
        title = item.get("title", "")
        authors = [
            author.get("name", "")
            for author in item.get("authors", [])
            if author.get("name")
        ]

        # 提取年份
        year = None
        year_match = re.search(r"(19|20)\d{2}", str(item.get("pubdate", "")))
        if year_match:
            year = int(year_match.group(0))

        # 提取 DOI
        doi = ""
        url = ""
        for article_id in item.get("articleids", []):
            if article_id.get("idtype") == "doi":
                doi = article_id.get("value", "")
                url = f"https://doi.org/{doi}"
                break

        # 提取关键词
        keywords = []
        for kw in item.get("keywords", [])[:8]:
            if isinstance(kw, str):
                keywords.append(kw)

        return PaperRecord(
            ref_id=f"PM-{item.get('uid', normalize_title(title)[:12])}",
            title=title,
            authors=authors[:10],
            year=year,
            journal=item.get("fulljournalname", ""),
            doi=doi,
            url=url or f"https://pubmed.ncbi.nlm.nih.gov/{item.get('uid', '')}/",
            abstract="",  # Summary API doesn't return abstract
            keywords=keywords,
            source_db="PubMed",
            jcr_quartile="Unknown",
            document_type=str(
                item.get("pubtype", ["article"])[0]
                if item.get("pubtype") else "article"
            ),
            times_cited=0,  # PubMed doesn't provide citation count
            language="English",
            is_recent=bool(year and year >= CURRENT_YEAR - self.config.year_window + 1),
            is_high_tier=False,
        )

    def fetch_abstracts(self, pmids: List[str]) -> Dict[str, str]:
        """Fetch abstracts for a list of PMIDs using efetch.

        Args:
            pmids: List of PubMed IDs.

        Returns:
            Dictionary mapping PMID to abstract text.
        """
        if not pmids:
            return {}

        self._rate_limit()

        try:
            response = self.session.get(
                self.fetch_url,
                headers={"User-Agent": "academic-review-system/1.0", "Accept": "application/xml"},
                params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()

            abstracts = {}
            root = ElementTree.fromstring(response.content)

            for article in root.findall(".//PubmedArticle"):
                pmid_elem = article.find(".//PMID")
                if pmid_elem is None:
                    continue
                pmid = pmid_elem.text

                abstract_elem = article.find(".//Abstract")
                if abstract_elem is not None:
                    texts = []
                    for text_elem in abstract_elem.findall("AbstractText"):
                        label = text_elem.get("Label", "")
                        text = "".join(text_elem.itertext())
                        if label:
                            texts.append(f"{label}: {text}")
                        else:
                            texts.append(text)
                    abstracts[pmid] = " ".join(texts)

            return abstracts

        except Exception as e:
            print(f"Failed to fetch abstracts: {e}")
            return {}
