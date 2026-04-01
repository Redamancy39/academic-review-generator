# Core data models for academic review system
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Current year constant
CURRENT_YEAR = datetime.now().year


@dataclass
class RunConfig:
    """Configuration for a single review generation run."""

    topic: str
    user_description: str = ""  # 用户对综述的理解和写作期望
    journal_type: str = "中文核心期刊"  # 期刊类型：中文核心期刊、中文顶级期刊、SCI期刊、EI期刊
    language: str = "中文"  # 综述语言：中文、英文
    word_count_min: int = 4000
    word_count_max: int = 6000
    target_refs: int = 40
    retrieval_pool_size: int = 100  # 检索池大小，建议是目标引用数的2-3倍
    year_window: int = 5
    review_rounds_min: int = 2
    review_rounds_max: int = 3
    output_path: Path = Path("outputs/final_review.md")
    output_dir: Path = Path("outputs")
    model_name: str = "openai/qwen3.5-plus"
    model_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    wos_api_base: str = "https://api.clarivate.com/apis/wos-starter/v1/documents"
    request_timeout: int = 30
    minimum_acceptable_refs: int = 35
    old_paper_ratio_limit: float = 0.15

    def __post_init__(self) -> None:
        if isinstance(self.output_path, str):
            self.output_path = Path(self.output_path)
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)


@dataclass
class PaperRecord:
    """Record representing a single academic paper."""

    ref_id: str
    title: str
    authors: List[str]
    year: Optional[int]
    journal: str
    doi: str
    url: str
    abstract: str
    keywords: List[str]
    source_db: str
    jcr_quartile: str
    document_type: str
    times_cited: int
    language: str
    is_recent: bool
    is_high_tier: bool
    relevance_score: float = 0.0
    # P2 fields - journal metrics
    impact_factor: Optional[float] = None
    cite_score: Optional[float] = None
    # P2 fields - author metrics
    corresponding_author: Optional[str] = None
    author_h_indices: List[int] = field(default_factory=list)
    # P1 fields - semantic similarity
    semantic_score: float = 0.0
    # Tracking metadata
    is_selected: bool = False
    user_notes: str = ""


@dataclass
class EvidenceNote:
    """Evidence note extracted from a paper for synthesis."""

    ref_id: str
    section_hint: str
    research_problem: str
    core_viewpoint: str
    new_method: str
    data_or_experiment: str
    main_conclusion: str
    strengths: str
    limitations: str
    theme_tags: List[str] = field(default_factory=list)


@dataclass
class SectionOutline:
    """Outline for a section of the review."""

    title: str
    goal: str
    target_words: int
    key_questions: List[str] = field(default_factory=list)
    must_cover: List[str] = field(default_factory=list)
    planned_tables: List[str] = field(default_factory=list)


@dataclass
class ReviewIssue:
    """Issue identified during review."""

    severity: str  # "blocking", "major", "minor"
    category: str
    description: str
    affected_section: str
    action: str


@dataclass
class ReviewReport:
    """Report from a review round."""

    round_index: int
    scorecard: Dict[str, float]
    blocking_issues: List[ReviewIssue] = field(default_factory=list)
    major_issues: List[ReviewIssue] = field(default_factory=list)
    minor_issues: List[ReviewIssue] = field(default_factory=list)
    missing_topics: List[str] = field(default_factory=list)
    weak_tables: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)
    revision_instructions: List[str] = field(default_factory=list)
    decision: str = "revise"

    def has_blocking(self) -> bool:
        return bool(self.blocking_issues)

    def min_dimension_score(self) -> float:
        return min(self.scorecard.values()) if self.scorecard else 0.0

    def total_score(self) -> float:
        """Calculate total score (sum of all dimension scores, max 60)."""
        if not self.scorecard:
            return 0.0
        return round(sum(self.scorecard.values()), 2)


@dataclass
class TopicAnalysis:
    """Analysis result of an input topic."""

    domain: str
    sub_domains: List[str]
    keywords: List[str]
    search_terms: List[str]
    suggested_sections: List[Dict[str, Any]]
    relevance_hints: List[str]


@dataclass
class AgentDefinition:
    """Definition for a CrewAI agent."""

    role: str
    goal: str
    backstory: str
    verbose: bool = True
    tools: List[str] = field(default_factory=list)


@dataclass
class TaskDefinition:
    """Definition for a CrewAI task."""

    description: str
    expected_output: str
    agent_role: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUsage:
    """Token usage statistics for a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    stage: str = ""  # 规划、检索、筛选、分析、撰写、审稿、修订
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            from datetime import datetime
            self.timestamp = datetime.now().isoformat()


@dataclass
class WorkflowTokenUsage:
    """Token usage statistics for the entire workflow."""

    stages: Dict[str, TokenUsage] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0  # 估算费用（人民币）

    def add_stage(self, stage: str, input_tokens: int, output_tokens: int) -> None:
        """Add token usage for a stage."""
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            stage=stage,
        )
        self.stages[stage] = usage
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        # 估算费用 (qwen-plus: 输入 0.8元/百万tokens, 输出 2.0元/百万tokens)
        self.estimated_cost = (
            self.total_input_tokens * 0.8 / 1_000_000 +
            self.total_output_tokens * 2.0 / 1_000_000
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stages": {k: to_jsonable(v) for k, v in self.stages.items()},
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": round(self.estimated_cost, 4),
        }


# Utility functions
def to_jsonable(obj: Any) -> Any:
    """Convert dataclass objects to JSON-serializable format."""
    # Handle Path objects first (including WindowsPath, PosixPath)
    if isinstance(obj, Path):
        return str(obj)

    # Handle dataclass objects
    if hasattr(obj, "__dataclass_fields__"):
        result = asdict(obj)
        # Recursively process the result to handle nested Path objects
        return to_jsonable(result)

    # Handle dictionaries
    if isinstance(obj, dict):
        return {key: to_jsonable(value) for key, value in obj.items()}

    # Handle lists and tuples
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(item) for item in obj]

    # Handle other non-serializable types
    if hasattr(obj, '__dict__'):
        try:
            return to_jsonable(obj.__dict__)
        except Exception:
            pass

    return obj


def evidence_note_from_dict(item: Dict[str, Any]) -> EvidenceNote:
    """Create EvidenceNote from dictionary."""
    return EvidenceNote(
        ref_id=item.get("ref_id", ""),
        section_hint=item.get("section_hint", ""),
        research_problem=item.get("research_problem", ""),
        core_viewpoint=item.get("core_viewpoint", ""),
        new_method=item.get("new_method", ""),
        data_or_experiment=item.get("data_or_experiment", ""),
        main_conclusion=item.get("main_conclusion", ""),
        strengths=item.get("strengths", ""),
        limitations=item.get("limitations", ""),
        theme_tags=item.get("theme_tags", []) or [],
    )


def review_issue_from_dict(item: Dict[str, Any], severity: str) -> ReviewIssue:
    """Create ReviewIssue from dictionary."""
    return ReviewIssue(
        severity=severity,
        category=item.get("category", ""),
        description=item.get("description", ""),
        affected_section=item.get("affected_section", ""),
        action=item.get("action", ""),
    )
