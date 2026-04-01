# OpenAlex retriever implementation
import re
from typing import Any, Dict, List, Sequence, Tuple

import requests

from ..models import CURRENT_YEAR, PaperRecord, RunConfig
from .base import BaseRetriever
from .wos_retriever import normalize_title, coerce_int, normalize_quartile


class OpenAlexRetriever(BaseRetriever):
    """Retriever for OpenAlex API with enhanced precision."""

    @property
    def source_name(self) -> str:
        return "OpenAlex"

    def __init__(self, config: RunConfig, session: requests.Session) -> None:
        super().__init__(config, session)
        self.api_base = "https://api.openalex.org/works"
        self.headers = {
            "User-Agent": "academic-review-system/1.0",
            "Accept": "application/json",
        }

    def fetch(self, queries: Sequence[Dict[str, Any]]) -> List[PaperRecord]:
        """Fetch papers from OpenAlex API with precision search."""
        records: List[PaperRecord] = []
        since_year = CURRENT_YEAR - self.config.year_window + 1
        # 根据检索池大小调整每个查询返回的数量
        per_query = min(40, max(15, self.config.retrieval_pool_size // len(queries))) if queries else 25

        for query_def in queries[:6]:
            try:
                search_term = query_def.get("query", query_def.get("intent", ""))
                priority = query_def.get("priority", 1)

                # 构建精确检索过滤器
                filters = self._build_filters(search_term, since_year)

                # 使用 title 和 abstract 搜索，更精确
                response = self.session.get(
                    self.api_base,
                    headers=self.headers,
                    params={
                        "search": None,  # 不使用默认搜索
                        "filter": filters,
                        "sort": "cited_by_count:desc,publication_year:desc",
                        "per-page": per_query,
                    },
                    timeout=self.config.request_timeout,
                )
                response.raise_for_status()
                payload = response.json()

                for item in payload.get("results", []):
                    record = self._parse_item(item)
                    # 计算精确相关性分数
                    record.relevance_score = self._compute_relevance(record, search_term, priority)
                    records.append(record)

            except Exception as e:
                print(f"OpenAlex query failed: {e}")
                continue

        return records

    def _build_filters(self, search_term: str, since_year: int) -> str:
        """Build OpenAlex filter string with precision search.

        OpenAlex filter syntax:
        - title.search: search in title only
        - abstract.search: search in abstract
        - default.search: search in title, abstract, and fulltext (less precise)
        """
        # 清理检索词
        clean_term = self._clean_search_term(search_term)

        # 构建过滤器：限定搜索字段为 title 或 abstract
        # 使用 title.search 和 abstract.search 提高精确度
        filters = [
            f"publication_year:>{since_year - 1}",
            "type:article",
            f"title_and_abstract.search:{clean_term}",
        ]

        return ",".join(filters)

    def _clean_search_term(self, term: str) -> str:
        """Clean and optimize search term for OpenAlex.

        OpenAlex search tips:
        - Use quotes for exact phrase matching
        - Remove overly complex boolean operators
        - Keep key domain terms
        """
        # 移除复杂的布尔运算符，简化为关键词
        # 保留引号内的精确匹配
        term = term.strip()

        # 如果已经有引号，保持原样
        if '"' in term:
            return term

        # 提取关键词
        # 移除常见的布尔运算符
        term = re.sub(r'\b(OR|AND|NOT)\b', ' ', term, flags=re.IGNORECASE)
        term = re.sub(r'[()]+', ' ', term)

        # 清理多余空格
        terms = [t.strip() for t in term.split() if t.strip() and len(t.strip()) > 2]

        # 返回清理后的搜索词
        return " ".join(terms[:10])  # 限制关键词数量

    def _compute_relevance(self, record: PaperRecord, search_term: str, priority: int) -> float:
        """Compute relevance score based on title/abstract matching.

        Args:
            record: The paper record.
            search_term: Original search term.
            priority: Query priority (1 is highest).

        Returns:
            Relevance score (0-10).
        """
        score = 0.0

        # 提取搜索关键词
        keywords = set(kw.lower() for kw in re.findall(r'\b[a-zA-Z]{3,}\b', search_term)
                       if kw.lower() not in {'and', 'or', 'not', 'the', 'for', 'with'})

        title_lower = (record.title or "").lower()
        abstract_lower = (record.abstract or "").lower()

        # 标题匹配权重高
        title_matches = sum(1 for kw in keywords if kw in title_lower)
        abstract_matches = sum(1 for kw in keywords if kw in abstract_lower)

        # 计算基础分数
        if keywords:
            title_ratio = title_matches / len(keywords)
            abstract_ratio = abstract_matches / len(keywords)

            # 标题匹配权重 0.6，摘要匹配权重 0.4
            score = (title_ratio * 6 + abstract_ratio * 4)

        # 优先级加成
        if priority == 1:
            score *= 1.2
        elif priority == 2:
            score *= 1.1

        # 新近度加成
        if record.year and record.year >= CURRENT_YEAR - 2:
            score *= 1.1

        # 引用数加成
        if record.times_cited >= 100:
            score *= 1.05
        elif record.times_cited >= 50:
            score *= 1.02

        return min(10.0, round(score, 2))

    def _parse_item(self, item: Dict[str, Any]) -> PaperRecord:
        """Parse a single OpenAlex record."""
        authors = []
        for authorship in item.get("authorships", []):
            author = authorship.get("author", {})
            if author.get("display_name"):
                authors.append(author["display_name"])
        year = coerce_int(item.get("publication_year"))
        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        abstract = item.get("abstract_inverted_index")
        abstract_text = ""
        if isinstance(abstract, dict):
            slots: List[Tuple[int, str]] = []
            for token, positions in abstract.items():
                for position in positions:
                    slots.append((position, token))
            abstract_text = " ".join(token for _, token in sorted(slots))

        # 获取期刊分区信息
        quartile = self._extract_quartile(source, item)

        # 获取影响因子
        impact_factor = self._extract_impact_factor(source, item)

        return PaperRecord(
            ref_id=f"OA-{item.get('id', '').split('/')[-1] or normalize_title(item.get('title', ''))[:12]}",
            title=item.get("title", "") or "",
            authors=authors[:10],
            year=year or None,
            journal=source.get("display_name", ""),
            doi=(item.get("doi") or "").replace("https://doi.org/", ""),
            url=location.get("landing_page_url", "") or item.get("id", ""),
            abstract=abstract_text,
            keywords=[concept.get("display_name", "") for concept in item.get("concepts", [])[:8]],
            source_db="OpenAlex",
            jcr_quartile=quartile,
            document_type=str(item.get("type") or "article"),
            times_cited=coerce_int(item.get("cited_by_count")),
            language="English",
            is_recent=bool(year and year >= CURRENT_YEAR - self.config.year_window + 1),
            is_high_tier=quartile in {"Q1", "Q2"},
            impact_factor=impact_factor,
        )

    def _extract_quartile(self, source: Dict[str, Any], item: Dict[str, Any]) -> str:
        """Extract JCR quartile from OpenAlex data."""
        # 尝试从多个来源获取分区信息
        quartile_sources = [
            source.get("x_indexed_in"),
            source.get("quartile"),
            item.get("jcr_quartile"),
        ]

        for q in quartile_sources:
            normalized = normalize_quartile(q)
            if normalized != "Unknown":
                return normalized

        # 根据 is_oa 和 is_in_doaj 推断
        if source.get("is_in_doaj"):
            return "Q2"  # 开放获取期刊通常质量不错

        return "Unknown"

    def _extract_impact_factor(self, source: Dict[str, Any], item: Dict[str, Any]) -> float:
        """Extract impact factor from OpenAlex data."""
        # OpenAlex 提供一些指标
        metrics = source.get("metrics", {}) or {}
        if "2yr_mean_citedness" in metrics:
            return round(metrics["2yr_mean_citedness"], 2)

        # 从 concepts 获取领域排名作为替代
        concepts = item.get("concepts", [])
        if concepts:
            # 取最高排名概念的得分作为参考
            top_score = max((c.get("score", 0) for c in concepts), default=0)
            return round(top_score * 10, 2)  # 归一化

        return None
