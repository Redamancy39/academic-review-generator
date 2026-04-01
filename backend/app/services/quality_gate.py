# Quality Gate - Checkpoints and validation for workflow stages
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.models import CURRENT_YEAR, PaperRecord, RunConfig


class GateDecision(Enum):
    """Decision from quality gate check."""
    PASS = "pass"  # Continue to next stage
    RETRY = "retry"  # Retry current stage with adjustments
    ROLLBACK = "rollback"  # Rollback to previous stage
    ABORT = "abort"  # Cannot proceed, abort


@dataclass
class GateResult:
    """Result from a quality gate check."""
    decision: GateDecision
    passed: bool
    score: float  # 0-100
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    rollback_to: Optional[str] = None  # Stage to rollback to
    retry_count: int = 0
    max_retries: int = 2


@dataclass
class RetrievalQualityMetrics:
    """Metrics for retrieval quality assessment."""
    total_records: int
    relevant_records: int
    domain_coverage: float  # Percentage of domain keywords covered
    time_distribution: Dict[int, int]  # Year -> count
    source_distribution: Dict[str, int]  # Source -> count
    relevance_score_avg: float
    relevance_score_median: float


@dataclass
class ScreeningQualityMetrics:
    """Metrics for screening quality assessment."""
    selected_count: int
    target_count: int
    topic_coverage: Dict[str, int]  # Topic -> count
    high_tier_ratio: float  # Q1/Q2 papers ratio
    recent_ratio: float  # Recent papers ratio
    relevance_score_avg: float


@dataclass
class DraftQualityMetrics:
    """Metrics for draft quality assessment."""
    word_count: int
    word_count_target_min: int
    word_count_target_max: int
    citation_count: int
    citation_target: int
    section_count: int
    table_count: int
    abstract_completeness: float  # Has intro, methods, results, discussion


class QualityGate:
    """Quality gate checker for workflow stages.

    This class provides quality checks at each stage of the workflow,
    enabling iterative refinement and rollback capabilities.
    """

    def __init__(self, config: RunConfig, topic_keywords: List[str]):
        """Initialize quality gate.

        Args:
            config: Run configuration.
            topic_keywords: Keywords for the research topic.
        """
        self.config = config
        self.topic_keywords = topic_keywords
        self.retry_counts: Dict[str, int] = {}
        self.max_retries_per_stage = 2

    def check_retrieval_quality(
        self,
        records: Sequence[PaperRecord],
        previous_records: Optional[Sequence[PaperRecord]] = None,
    ) -> GateResult:
        """Check quality of retrieved papers.

        Args:
            records: Retrieved paper records.
            previous_records: Records from previous attempt (if retry).

        Returns:
            GateResult with decision and metrics.
        """
        issues = []
        suggestions = []
        score = 100.0

        if not records:
            return GateResult(
                decision=GateDecision.RETRY,
                passed=False,
                score=0.0,
                issues=["检索结果为空，未找到任何文献"],
                suggestions=["尝试简化检索词", "扩大时间范围", "更换数据源"],
                rollback_to="planning",
            )

        # Calculate metrics
        metrics = self._calculate_retrieval_metrics(records)

        # Check 1: Quantity check
        min_required = self.config.minimum_acceptable_refs * 2  # Need buffer for screening
        if metrics.total_records < min_required:
            issues.append(f"检索结果不足：{metrics.total_records} 篇，建议至少 {min_required} 篇")
            score -= 20
            suggestions.append("扩大检索范围或增加检索词")

        # Check 2: Relevance check
        relevance_threshold = 3.0
        relevant_count = sum(1 for r in records if r.relevance_score >= relevance_threshold)
        relevance_ratio = relevant_count / len(records) if records else 0

        if relevance_ratio < 0.3:
            issues.append(f"相关性过低：仅 {relevant_count}/{len(records)} 篇文献相关性得分 >= {relevance_threshold}")
            score -= 25
            suggestions.append("优化检索词，增加领域限定关键词")
            suggestions.append("使用更精确的检索式")

        # Check 3: Domain keyword coverage
        domain_keywords = self._extract_domain_keywords()
        coverage_score = self._check_keyword_coverage(records, domain_keywords)

        if coverage_score < 0.5:
            issues.append(f"领域关键词覆盖率不足：{coverage_score:.1%}")
            score -= 15
            suggestions.append("检查检索词是否准确表达研究主题")

        # Check 4: Time distribution
        recent_count = sum(1 for r in records if r.is_recent)
        recent_ratio = recent_count / len(records) if records else 0

        if recent_ratio < 0.5:
            issues.append(f"近年文献比例偏低：{recent_ratio:.1%}")
            score -= 10
            suggestions.append("考虑放宽时间限制或补充经典文献")

        # Check 5: Source diversity
        if len(metrics.source_distribution) == 1:
            issues.append("数据源单一，建议使用多个数据源")
            score -= 5
            suggestions.append("启用 PubMed 等其他数据源")

        # Determine decision
        if score >= 70:
            decision = GateDecision.PASS
        elif score >= 40:
            decision = GateDecision.RETRY
            suggestions.append("尝试不同的检索策略")
        else:
            decision = GateDecision.ROLLBACK
            suggestions.append("重新规划检索策略")

        return GateResult(
            decision=decision,
            passed=score >= 60,
            score=max(0, score),
            issues=issues,
            suggestions=suggestions,
            rollback_to="planning" if decision == GateDecision.ROLLBACK else None,
        )

    def check_screening_quality(
        self,
        selected: Sequence[PaperRecord],
        candidates: Sequence[PaperRecord],
    ) -> GateResult:
        """Check quality of screening results.

        Args:
            selected: Selected paper records.
            candidates: Candidate pool before screening.

        Returns:
            GateResult with decision and metrics.
        """
        issues = []
        suggestions = []
        score = 100.0

        if not selected:
            return GateResult(
                decision=GateDecision.ROLLBACK,
                passed=False,
                score=0.0,
                issues=["筛选结果为空，未选中任何文献"],
                suggestions=["放宽筛选标准", "重新检索"],
                rollback_to="retrieval",
            )

        # Calculate metrics
        metrics = self._calculate_screening_metrics(selected)

        # Check 1: Quantity check
        if metrics.selected_count < self.config.minimum_acceptable_refs:
            issues.append(f"选中文献不足：{metrics.selected_count} 篇，目标 {self.config.target_refs} 篇")
            score -= 30
            suggestions.append("放宽筛选标准")
            suggestions.append("补充检索更多文献")

        # Check 2: Topic coverage
        uncovered_topics = []
        for topic, count in metrics.topic_coverage.items():
            if count < 2:
                uncovered_topics.append(topic)

        if uncovered_topics:
            issues.append(f"部分主题覆盖不足：{', '.join(uncovered_topics[:3])}")
            score -= 15
            suggestions.append(f"补充检索：{uncovered_topics[0]}")

        # Check 3: Quality distribution
        if metrics.high_tier_ratio < 0.3:
            issues.append(f"高质量期刊文献比例偏低：{metrics.high_tier_ratio:.1%}")
            score -= 10
            suggestions.append("优先选择 Q1/Q2 期刊文献")

        # Check 4: Relevance score
        if metrics.relevance_score_avg < 5.0:
            issues.append(f"平均相关性得分偏低：{metrics.relevance_score_avg:.1f}")
            score -= 15
            suggestions.append("检查文献与主题的相关性")

        # Determine decision
        if score >= 70:
            decision = GateDecision.PASS
        elif score >= 40:
            decision = GateDecision.RETRY
        else:
            decision = GateDecision.ROLLBACK

        return GateResult(
            decision=decision,
            passed=score >= 60,
            score=max(0, score),
            issues=issues,
            suggestions=suggestions,
            rollback_to="retrieval" if decision == GateDecision.ROLLBACK else None,
        )

    def check_draft_quality(
        self,
        draft: str,
        selected_records: Sequence[PaperRecord],
    ) -> GateResult:
        """Check quality of generated draft.

        Args:
            draft: Draft content.
            selected_records: Selected paper records.

        Returns:
            GateResult with decision and metrics.
        """
        issues = []
        suggestions = []
        score = 100.0

        if not draft or len(draft.strip()) < 100:
            return GateResult(
                decision=GateDecision.RETRY,
                passed=False,
                score=0.0,
                issues=["初稿内容为空或过短"],
                suggestions=["重新撰写"],
            )

        # Calculate metrics
        metrics = self._calculate_draft_metrics(draft, selected_records)

        # Check 1: Word count (正文，不含参考文献)
        if metrics.word_count < self.config.word_count_min:
            issues.append(f"字数不足：{metrics.word_count} 字，目标 {self.config.word_count_min}-{self.config.word_count_max} 字")
            score -= 25
            suggestions.append("扩展各章节内容深度")
            suggestions.append("增加案例分析或方法对比")
        elif metrics.word_count > self.config.word_count_max * 1.2:
            issues.append(f"字数过多：{metrics.word_count} 字，目标 {self.config.word_count_min}-{self.config.word_count_max} 字")
            score -= 10
            suggestions.append("精简冗余内容")
            suggestions.append("合并相似段落")

        # Check 2: Citation count
        if metrics.citation_count < self.config.minimum_acceptable_refs:
            issues.append(f"引用文献不足：{metrics.citation_count} 篇，目标 {self.config.target_refs} 篇")
            score -= 20
            suggestions.append("增加文献支撑")
            suggestions.append("每个论点至少引用 1-2 篇文献")
        elif metrics.citation_count < len(selected_records) * 0.7:
            issues.append(f"引用覆盖率低：仅引用 {metrics.citation_count}/{len(selected_records)} 篇选中文献")
            score -= 15
            suggestions.append("确保所有选中文献都有引用")

        # Check 3: Section structure
        if metrics.section_count < 4:
            issues.append(f"章节结构不完整：仅 {metrics.section_count} 个章节")
            score -= 15
            suggestions.append("补充引言、方法、结果、讨论等章节")

        # Check 4: Tables
        if metrics.table_count < 2:
            issues.append(f"表格数量不足：{metrics.table_count} 个，建议至少 3 个")
            score -= 10
            suggestions.append("添加方法对比表、性能汇总表等")

        # Determine decision
        if score >= 70:
            decision = GateDecision.PASS
        elif score >= 40:
            decision = GateDecision.RETRY
            if metrics.word_count < self.config.word_count_min:
                suggestions.insert(0, "扩展内容充实各个章节")
            elif metrics.word_count > self.config.word_count_max:
                suggestions.insert(0, "精简内容突出重点")
        else:
            decision = GateDecision.ROLLBACK

        return GateResult(
            decision=decision,
            passed=score >= 60,
            score=max(0, score),
            issues=issues,
            suggestions=suggestions,
            rollback_to="analysis" if decision == GateDecision.ROLLBACK else None,
        )

    def get_retry_count(self, stage: str) -> int:
        """Get retry count for a stage."""
        return self.retry_counts.get(stage, 0)

    def increment_retry(self, stage: str) -> int:
        """Increment retry count and return new value."""
        self.retry_counts[stage] = self.retry_counts.get(stage, 0) + 1
        return self.retry_counts[stage]

    def can_retry(self, stage: str) -> bool:
        """Check if stage can be retried."""
        return self.retry_counts.get(stage, 0) < self.max_retries_per_stage

    def _calculate_retrieval_metrics(self, records: Sequence[PaperRecord]) -> RetrievalQualityMetrics:
        """Calculate retrieval quality metrics."""
        total = len(records)
        relevant = sum(1 for r in records if r.relevance_score >= 3.0)

        # Time distribution
        time_dist: Dict[int, int] = {}
        for r in records:
            if r.year:
                time_dist[r.year] = time_dist.get(r.year, 0) + 1

        # Source distribution
        source_dist: Dict[str, int] = {}
        for r in records:
            source_dist[r.source_db] = source_dist.get(r.source_db, 0) + 1

        # Relevance scores
        scores = [r.relevance_score for r in records]
        avg_score = sum(scores) / len(scores) if scores else 0
        sorted_scores = sorted(scores)
        median_score = sorted_scores[len(sorted_scores) // 2] if sorted_scores else 0

        # Domain coverage
        domain_kw = self._extract_domain_keywords()
        domain_coverage = self._check_keyword_coverage(records, domain_kw)

        return RetrievalQualityMetrics(
            total_records=total,
            relevant_records=relevant,
            domain_coverage=domain_coverage,
            time_distribution=time_dist,
            source_distribution=source_dist,
            relevance_score_avg=avg_score,
            relevance_score_median=median_score,
        )

    def _calculate_screening_metrics(self, selected: Sequence[PaperRecord]) -> ScreeningQualityMetrics:
        """Calculate screening quality metrics."""
        total = len(selected)

        # Topic coverage
        topic_coverage = {}
        for topic in self.topic_keywords[:10]:
            count = sum(1 for r in selected if topic.lower() in (r.title + " " + (r.abstract or "")).lower())
            topic_coverage[topic] = count

        # High tier ratio
        high_tier = sum(1 for r in selected if r.is_high_tier)
        high_tier_ratio = high_tier / total if total else 0

        # Recent ratio
        recent = sum(1 for r in selected if r.is_recent)
        recent_ratio = recent / total if total else 0

        # Relevance score
        scores = [r.relevance_score for r in selected]
        avg_score = sum(scores) / len(scores) if scores else 0

        return ScreeningQualityMetrics(
            selected_count=total,
            target_count=self.config.target_refs,
            topic_coverage=topic_coverage,
            high_tier_ratio=high_tier_ratio,
            recent_ratio=recent_ratio,
            relevance_score_avg=avg_score,
        )

    def _calculate_draft_metrics(
        self,
        draft: str,
        records: Sequence[PaperRecord],
    ) -> DraftQualityMetrics:
        """Calculate draft quality metrics."""
        # Word count (Chinese characters + English words)
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', draft))
        english_words = len(re.findall(r'[A-Za-z]+', draft))
        word_count = chinese_chars + english_words // 2  # Adjust for Chinese counting

        # Citation count (look for [@REFxxx] or [数字] patterns)
        citations = set()
        # Pattern: [@REF001] or [REF001]
        ref_pattern = re.findall(r'\[@?REF(\d+)\]', draft)
        citations.update(ref_pattern)
        # Pattern: [1], [2-5], [1,3,5]
        num_pattern = re.findall(r'\[(\d+(?:-\d+)?(?:,\s*\d+)*)\]', draft)
        for p in num_pattern:
            for part in p.split(','):
                if '-' in part:
                    start, end = part.split('-')
                    try:
                        citations.update(str(i) for i in range(int(start), int(end) + 1))
                    except ValueError:
                        pass
                else:
                    citations.add(part.strip())

        citation_count = len(citations)

        # Section count (## headings)
        sections = re.findall(r'^##\s+\d+\.?\s*', draft, re.MULTILINE)
        section_count = len(sections)

        # Table count
        tables = re.findall(r'\|[^|]+\|', draft)
        table_count = len(set(re.findall(r'^\|[^|]+\|$', draft, re.MULTILINE)))

        return DraftQualityMetrics(
            word_count=word_count,
            word_count_target_min=self.config.word_count_min,
            word_count_target_max=self.config.word_count_max,
            citation_count=citation_count,
            citation_target=self.config.target_refs,
            section_count=section_count,
            table_count=table_count,
            abstract_completeness=1.0,  # Placeholder
        )

    def _extract_domain_keywords(self) -> List[str]:
        """Extract domain-specific keywords from topic."""
        keywords = list(self.topic_keywords)

        # Add common domain keywords based on topic
        topic_lower = self.config.topic.lower()

        if any(kw in topic_lower for kw in ['食品', 'food', '肉', 'meat', '果', 'fruit']):
            keywords.extend(['food', 'quality', 'freshness', 'safety', 'detection'])
        if any(kw in topic_lower for kw in ['深度学习', 'deep learning', 'cnn', '神经网络']):
            keywords.extend(['deep learning', 'neural network', 'cnn', 'model'])

        return list(set(keywords))

    def _check_keyword_coverage(self, records: Sequence[PaperRecord], keywords: List[str]) -> float:
        """Check what fraction of keywords are covered in records."""
        if not keywords:
            return 1.0

        covered = 0
        for kw in keywords:
            kw_lower = kw.lower()
            for r in records:
                text = (r.title + " " + (r.abstract or "")).lower()
                if kw_lower in text:
                    covered += 1
                    break

        return covered / len(keywords)


def generate_adjusted_queries(
    original_queries: List[Dict[str, Any]],
    gate_result: GateResult,
    topic: str,
) -> List[Dict[str, Any]]:
    """Generate adjusted search queries based on gate result.

    Args:
        original_queries: Original search queries.
        gate_result: Quality gate result with suggestions.
        topic: Research topic.

    Returns:
        Adjusted search queries.
    """
    adjusted = list(original_queries)

    # Add suggestions as new queries
    for suggestion in gate_result.suggestions[:3]:
        if '检索' in suggestion or 'search' in suggestion.lower():
            # Extract potential keywords from suggestion
            adjusted.append({
                "query": suggestion,
                "intent": f"补充检索：{suggestion[:30]}",
                "priority": 2,
            })

    # Simplify existing queries if too complex
    for i, q in enumerate(adjusted):
        query = q.get("query", "")
        # Remove overly complex boolean operators
        if query.count("AND") > 3 or query.count("OR") > 5:
            # Simplify to essential terms
            terms = re.findall(r'"([^"]+)"', query)
            if terms:
                simplified = " OR ".join(f'"{t}"' for t in terms[:4])
                adjusted[i]["query"] = simplified

    return adjusted


def generate_adjusted_screening_params(
    gate_result: GateResult,
    target_refs: int,
) -> Dict[str, Any]:
    """Generate adjusted screening parameters based on gate result.

    Args:
        gate_result: Quality gate result.
        target_refs: Target reference count.

    Returns:
        Adjusted parameters.
    """
    params = {
        "relax_standards": False,
        "include_backup": False,
        "focus_topics": [],
    }

    # Check if we need to relax standards
    if "不足" in str(gate_result.issues):
        params["relax_standards"] = True
        params["include_backup"] = True

    # Extract topics that need more papers
    for issue in gate_result.issues:
        if "主题" in issue or "覆盖" in issue:
            # Try to extract topic name
            params["focus_topics"].append(issue)

    return params
