# Workflow Runner Service - executes CrewAI workflows
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..core.models import (
    EvidenceNote,
    PaperRecord,
    ReviewReport,
    RunConfig,
    TopicAnalysis,
    WorkflowTokenUsage,
    evidence_note_from_dict,
    review_issue_from_dict,
    to_jsonable,
)
from ..core.retrievers import RetrieverManager, score_paper
from .agent_generator import AgentGenerator
from .checkpoint_manager import (
    CheckpointData,
    CheckpointManager,
    CheckpointStage,
    restore_config,
    restore_evidence_bank,
    restore_records,
)
from .prompt_renderer import PromptRenderer
from .quality_gate import (
    GateDecision,
    GateResult,
    QualityGate,
    generate_adjusted_queries,
    generate_adjusted_screening_params,
)


def safe_json_loads(raw_text: str) -> Any:
    """Safely parse JSON from text that may contain markdown code blocks or formatting issues.

    Returns:
        Parsed JSON (dict or list), or empty dict if parsing fails.
    """
    if not raw_text:
        return {}

    cleaned = raw_text.strip()

    # Try to extract JSON from markdown code blocks (non-greedy)
    # Match ```json ... ``` or ``` ... ``` containing JSON
    fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fenced_match:
        cleaned = fenced_match.group(1).strip()

    # If no code block, try to extract JSON object/array
    if not cleaned.startswith("{") and not cleaned.startswith("["):
        first_brace = min(
            [idx for idx in [cleaned.find("{"), cleaned.find("[")] if idx >= 0],
            default=-1,
        )
        last_brace = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if first_brace >= 0 and last_brace >= first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]
        else:
            # No JSON structure found
            print(f"Warning: No JSON structure found in response")
            print(f"Response preview: {cleaned[:200]}...")
            return {}

    # Clean up common issues in LLM-generated JSON
    # Remove control characters except newline and tab
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", cleaned)

    try:
        result = json.loads(cleaned)
        # Ensure result is dict or list, not string
        if isinstance(result, str):
            print(f"Warning: JSON parsed to string, attempting to re-parse")
            result = json.loads(result)
        return result
    except json.JSONDecodeError as e:
        # Try to repair common issues
        try:
            # Attempt to fix trailing commas
            fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
            # Attempt to fix unescaped backslashes
            fixed = re.sub(r"\\([^\"nrtu\\])", r"\\\\\1", fixed)
            result = json.loads(fixed)
            if isinstance(result, str):
                result = json.loads(result)
            return result
        except json.JSONDecodeError:
            print(f"Warning: Failed to parse JSON. Error: {e}")
            print(f"Problematic text (first 500 chars): {cleaned[:500]}")
            return {}  # Return empty dict instead of raising


def split_revision_payload(raw_text: str) -> Tuple[Dict[str, Any], str]:
    """Split revision response into JSON and markdown parts."""
    revision = {}
    marker = "---REVISED_DRAFT---"

    # Try to extract JSON from the response
    try:
        # First try fenced JSON block
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", raw_text)
        if json_match:
            revision = safe_json_loads(json_match.group(1))
    except Exception as e:
        print(f"Warning: Failed to parse revision JSON: {e}")
        revision = {}

    # Extract markdown part
    if marker in raw_text:
        markdown = raw_text.split(marker, 1)[1].strip()
    else:
        # If no marker, treat the whole thing as markdown
        # (after removing any JSON blocks)
        markdown = re.sub(r"```json\s*[\s\S]*?\s*```", "", raw_text).strip()

    return revision, markdown


def estimate_tokens(text: str) -> int:
    """Estimate token count for text.

    For Chinese: ~1.5 characters per token
    For English: ~4 characters per token
    """
    if not text:
        return 0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def estimate_word_count(text: str) -> int:
    """Estimate word count for Chinese + English text."""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_tokens = len(re.findall(r"[A-Za-z0-9]+", text))
    return chinese_chars + latin_tokens


def compact_paper_summary(records: Sequence[PaperRecord], limit: int = 60) -> str:
    """Create a compact summary of papers for prompts."""
    lines = []
    for record in records[:limit]:
        lines.append(
            f"{record.ref_id} | {record.year} | {record.journal} | {record.jcr_quartile} | "
            f"{record.title} | 关键词: {', '.join(record.keywords[:5])} | 摘要: {record.abstract[:220]}"
        )
    return "\n".join(lines)


def check_topic_coverage(
    records: Sequence[PaperRecord],
    topic_keywords: List[str],
    min_papers_per_topic: int = 2,
) -> Dict[str, Any]:
    """Check if papers cover all topics/sub-topics.

    Args:
        records: List of paper records.
        topic_keywords: List of topic keywords to check coverage for.
        min_papers_per_topic: Minimum papers required per topic.

    Returns:
        Dictionary with coverage analysis results.
    """
    if not topic_keywords or not records:
        return {
            "coverage_ok": True,
            "covered_topics": [],
            "underrepresented_topics": list(topic_keywords) if topic_keywords else [],
            "coverage_scores": {},
            "suggested_queries": [],
        }

    # Normalize keywords for matching
    topic_lower = [kw.lower().strip() for kw in topic_keywords]

    # Count papers per topic
    coverage_counts: Dict[str, int] = {kw: 0 for kw in topic_keywords}
    coverage_scores: Dict[str, float] = {kw: 0.0 for kw in topic_keywords}

    for record in records:
        # Check title, abstract, and keywords
        text_fields = [
            record.title.lower(),
            (record.abstract or "").lower(),
            " ".join(kw.lower() for kw in record.keywords),
        ]
        combined_text = " ".join(text_fields)

        for idx, topic in enumerate(topic_lower):
            if topic in combined_text:
                coverage_counts[topic_keywords[idx]] += 1
                # Weight by relevance score
                coverage_scores[topic_keywords[idx]] += record.relevance_score

    # Normalize scores
    for topic in topic_keywords:
        if coverage_counts[topic] > 0:
            coverage_scores[topic] = round(coverage_scores[topic] / coverage_counts[topic], 2)

    # Identify underrepresented topics
    covered_topics = [
        topic for topic in topic_keywords
        if coverage_counts[topic] >= min_papers_per_topic
    ]
    underrepresented_topics = [
        topic for topic in topic_keywords
        if coverage_counts[topic] < min_papers_per_topic
    ]

    # Generate suggested queries for underrepresented topics
    suggested_queries = []
    for topic in underrepresented_topics:
        # Add variations of the query
        suggested_queries.append(topic)
        # Add English translation hint if topic is Chinese
        if any('\u4e00' <= c <= '\u9fff' for c in topic):
            suggested_queries.append(f'"{topic}" OR "{topic}"')

    return {
        "coverage_ok": len(underrepresented_topics) == 0,
        "covered_topics": covered_topics,
        "underrepresented_topics": underrepresented_topics,
        "coverage_counts": coverage_counts,
        "coverage_scores": coverage_scores,
        "suggested_queries": suggested_queries[:5],  # Limit to 5 suggestions
    }


def prefilter_domain_relevance(
    records: Sequence[PaperRecord],
    topic: str,
    domain_keywords: List[str],
    exclusion_keywords: List[str],
    min_relevance_score: float = 3.0,
) -> Tuple[List[PaperRecord], List[PaperRecord]]:
    """Pre-filter papers by domain relevance.

    This function filters out papers that are clearly outside the target domain,
    reducing the burden on the LLM-based screener.

    Args:
        records: List of paper records.
        topic: Main research topic.
        domain_keywords: Keywords that indicate relevance to the domain.
        exclusion_keywords: Keywords that indicate irrelevance.
        min_relevance_score: Minimum relevance score to keep.

    Returns:
        Tuple of (relevant records, excluded records with reasons).
    """
    if not records:
        return [], []

    relevant = []
    excluded = []

    # Normalize keywords
    domain_lower = [kw.lower().strip() for kw in domain_keywords]
    exclusion_lower = [kw.lower().strip() for kw in exclusion_keywords]

    for record in records:
        # Combine text fields for checking
        title_lower = (record.title or "").lower()
        abstract_lower = (record.abstract or "").lower()
        keywords_lower = " ".join(kw.lower() for kw in record.keywords)
        journal_lower = (record.journal or "").lower()

        combined_text = f"{title_lower} {abstract_lower} {keywords_lower}"

        # Check for exclusion keywords first
        exclusion_hits = []
        for kw in exclusion_lower:
            if kw in combined_text:
                exclusion_hits.append(kw)

        # Check for domain keywords
        domain_hits = []
        for kw in domain_lower:
            if kw in combined_text:
                domain_hits.append(kw)

        # Calculate relevance score
        # Penalty for exclusion keywords
        exclusion_penalty = len(exclusion_hits) * 2.0

        # Bonus for domain keywords
        domain_bonus = len(domain_hits) * 1.0

        # Title match bonus
        title_domain_hits = sum(1 for kw in domain_lower if kw in title_lower)
        domain_bonus += title_domain_hits * 2.0

        # Calculate final score
        final_score = max(0, record.relevance_score + domain_bonus - exclusion_penalty)

        # Decision
        if exclusion_hits and not domain_hits:
            # Has exclusion keywords but no domain keywords - likely irrelevant
            excluded.append(record)
        elif len(exclusion_hits) > len(domain_hits):
            # More exclusion hits than domain hits
            excluded.append(record)
        elif final_score >= min_relevance_score:
            record.relevance_score = final_score
            relevant.append(record)
        elif domain_hits:
            # Has some domain relevance, keep it
            record.relevance_score = final_score
            relevant.append(record)
        else:
            # Low relevance, exclude
            excluded.append(record)

    # Sort by relevance score
    relevant.sort(key=lambda r: r.relevance_score, reverse=True)

    return relevant, excluded


# Default exclusion keywords for food science domain
FOOD_SCIENCE_EXCLUSION_KEYWORDS = [
    # Medical/Healthcare
    "clinical trial", "patient", "diagnosis", "surgery", "treatment",
    "hospital", "medical imaging", "radiology", "pathology",
    # Finance/Business
    "stock market", "financial", "investment", "trading", "cryptocurrency",
    "banking", "insurance", "marketing", "consumer behavior",
    # Environment (non-food)
    "climate change", "air pollution", "water quality monitoring",
    "biodiversity", "ecosystem", "forestry",
    # Communication/Network
    "5g network", "wireless communication", "signal processing",
    "antenna", "satellite communication",
    # Energy
    "power grid", "renewable energy", "solar panel", "wind turbine",
    "battery management", "electric vehicle",
    # Pure AI/ML methodology
    "neural architecture search", "gradient descent optimization",
    "loss function design", "batch normalization",
]

# Default domain keywords for food science
FOOD_SCIENCE_DOMAIN_KEYWORDS = [
    "food", "meat", "fruit", "vegetable", "dairy", "fish", "seafood",
    "freshness", "quality", "safety", "spoilage", "detection",
    "agricultural", "crop", "harvest", "postharvest",
    "nutrition", "protein", "carbohydrate", "fat",
    "microbial", "bacteria", "pathogen",
    "sensory", "taste", "odor", "texture", "color",
    "shelf life", "storage", "packaging",
    "hyperspectral", "nir", "electronic nose", "electronic tongue",
    "food processing", "food industry",
]


def serialise_records_for_prompt(records: Sequence[PaperRecord]) -> str:
    """Serialize records for use in prompts."""
    lines = []
    for record in records:
        lines.append(
            f"{record.ref_id} | {record.title} | {record.year} | {record.journal} | {record.jcr_quartile} | "
            f"DOI={record.doi or 'N/A'} | URL={record.url or 'N/A'}"
        )
    return "\n".join(lines)


def format_reference(record: PaperRecord) -> str:
    """Format a reference for the bibliography."""
    authors = ", ".join(record.authors[:4]) if record.authors else "Unknown"
    year = record.year or "n.d."
    source_note = record.jcr_quartile if record.jcr_quartile != "Unknown" else record.source_db
    doi_part = f" DOI: {record.doi}." if record.doi else ""
    url_part = f" {record.url}" if record.url else ""
    return f"{authors}. {record.title}[J]. {record.journal}, {year}. {source_note}.{doi_part}{url_part}".strip()


class WorkflowRunner:
    """Service for running the complete review generation workflow with iterative refinement."""

    def __init__(
        self,
        config: RunConfig,
        topic_analysis: TopicAnalysis,
        wos_api_key: Optional[str] = None,
        progress_callback: Optional[Callable[[str, str, float], None]] = None,
        source_progress_callback: Optional[Callable[[str, str, int, Optional[str]], None]] = None,
        stage_data_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        pause_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        enable_checkpoint: bool = True,
        enable_quality_gate: bool = True,
        max_iterations_per_stage: int = 2,
    ) -> None:
        """Initialize the workflow runner.

        Args:
            config: Run configuration.
            topic_analysis: Parsed topic analysis.
            wos_api_key: Optional WOS API key.
            progress_callback: Optional callback for progress updates.
            source_progress_callback: Optional callback for per-source retrieval progress.
            stage_data_callback: Optional callback for intermediate stage data (for frontend display).
            pause_callback: Optional callback for pausing in semi-auto mode.
            enable_checkpoint: Whether to enable checkpointing (default True).
            enable_quality_gate: Whether to enable quality gate checks (default True).
            max_iterations_per_stage: Maximum iterations per stage before giving up.
        """
        self.config = config
        self.topic_analysis = topic_analysis
        self.wos_api_key = wos_api_key
        self.progress_callback = progress_callback
        self.source_progress_callback = source_progress_callback
        self.stage_data_callback = stage_data_callback
        self.pause_callback = pause_callback
        self.enable_checkpoint = enable_checkpoint
        self.enable_quality_gate = enable_quality_gate
        self.max_iterations_per_stage = max_iterations_per_stage

        # Initialize services
        self.agent_generator = AgentGenerator()
        self.prompt_renderer = PromptRenderer()
        self.retriever_manager = RetrieverManager(config, wos_api_key, source_progress_callback)

        # CrewAI components (initialized lazily)
        self.llm = None
        self.agents: Dict[str, Any] = {}

        # Token usage tracking
        self.token_usage = WorkflowTokenUsage()

        # Checkpoint manager
        self.checkpoint_manager = CheckpointManager(config.output_dir)

        # Quality gate
        self.quality_gate = QualityGate(config, topic_analysis.keywords)

        # Iteration tracking
        self.stage_iterations: Dict[str, int] = {}
        self.gate_history: List[Dict[str, Any]] = []

        # 半自动模式状态
        self.is_paused = False
        self.pause_reason: Optional[str] = None

    def _report_progress(self, stage: str, message: str, progress: float) -> None:
        """Report progress to callback if available."""
        if self.progress_callback:
            self.progress_callback(stage, message, progress)

    def _report_stage_data(self, stage: str, data: Dict[str, Any]) -> None:
        """Report intermediate stage data to callback if available."""
        if self.stage_data_callback:
            try:
                self.stage_data_callback(stage, data)
            except Exception as e:
                print(f"[WorkflowRunner] stage_data_callback error: {e}")

    def _check_pause_and_wait(self, pause_point: str, pause_data: Dict[str, Any]) -> bool:
        """Check if workflow should pause at this point and wait for user action.

        Args:
            pause_point: The pause point identifier (e.g., "after_planning").
            pause_data: Data to pass to the pause callback.

        Returns:
            True if paused, False if should continue.
        """
        if not self.config.should_pause_at(pause_point):
            return False

        self.is_paused = True
        self.pause_reason = pause_point

        if self.pause_callback:
            self.pause_callback(pause_point, pause_data)

        return True

    def _save_checkpoint(self, stage: str, progress: float, message: str = "", **kwargs) -> None:
        """Save a checkpoint if enabled."""
        if not self.enable_checkpoint:
            return

        checkpoint = CheckpointData(
            stage=stage,
            progress=progress,
            message=message,
            config=to_jsonable(self.config),
            topic_analysis=to_jsonable(self.topic_analysis),
            token_usage=self.token_usage.to_dict(),
            **kwargs,
        )
        self.checkpoint_manager.save(checkpoint)

    def can_resume(self) -> bool:
        """Check if there's a checkpoint to resume from."""
        return self.checkpoint_manager.exists()

    def get_resume_stage(self) -> Optional[str]:
        """Get the stage to resume from."""
        return self.checkpoint_manager.get_stage()

    def clear_checkpoint(self) -> None:
        """Clear the checkpoint."""
        self.checkpoint_manager.clear()

    def _determine_resume_point(self, checkpoint: CheckpointData) -> str:
        """Determine where to resume based on checkpoint data.

        Args:
            checkpoint: The loaded checkpoint.

        Returns:
            The stage to resume from.
        """
        # Check what data is available to determine resume point
        if checkpoint.final_draft and checkpoint.validation:
            return CheckpointStage.COMPLETE
        elif checkpoint.draft and checkpoint.evidence_bank:
            return CheckpointStage.REVIEW
        elif checkpoint.evidence_bank and checkpoint.selected_records:
            return CheckpointStage.WRITING
        elif checkpoint.selected_records and checkpoint.plan:
            return CheckpointStage.ANALYSIS
        elif checkpoint.raw_records and checkpoint.plan:
            return CheckpointStage.SCREENING
        elif checkpoint.plan:
            return CheckpointStage.RETRIEVAL
        elif checkpoint.config and checkpoint.topic_analysis:
            return CheckpointStage.PLANNING
        else:
            return CheckpointStage.INIT

    def _init_llm(self) -> Any:
        """Initialize the LLM."""
        if self.llm is None:
            try:
                from crewai import LLM
                import os
                self.llm = LLM(
                    model=self.config.model_name,
                    base_url=self.config.model_base_url,
                    api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
                )
            except ImportError:
                raise RuntimeError("CrewAI not installed. Please install it first.")
        return self.llm

    def _init_agents(self) -> Dict[str, Any]:
        """Initialize CrewAI agents."""
        if not self.agents:
            llm = self._init_llm()
            agent_configs = {
                "topic": self.config.topic,
                "word_count_min": self.config.word_count_min,
                "word_count_max": self.config.word_count_max,
                "target_refs": self.config.target_refs,
                "year_window": self.config.year_window,
            }
            definitions = self.agent_generator.generate_all(self.topic_analysis, agent_configs)
            for role, definition in definitions.items():
                self.agents[f"{role}_agent"] = self.agent_generator.create_crewai_agent(definition, llm)
        return self.agents

    def _execute_agent(self, agent: Any, description: str, expected_output: str, stage: str = "") -> str:
        """Execute a single agent task and track token usage."""
        try:
            from crewai import Crew, Process, Task
            task = Task(description=description, expected_output=expected_output, agent=agent)
            crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
            result = crew.kickoff()

            # Extract raw output
            if hasattr(result, "raw"):
                output = str(result.raw)
            elif hasattr(result, "output"):
                output = str(result.output)
            else:
                output = str(result)

            # Track token usage (estimate based on text length)
            if stage:
                input_tokens = estimate_tokens(description)
                output_tokens = estimate_tokens(output)
                self.token_usage.add_stage(stage, input_tokens, output_tokens)

            return output
        except ImportError:
            raise RuntimeError("CrewAI not installed. Please install it first.")

    async def run(self, resume: bool = True, resume_from_stage: Optional[str] = None) -> Dict[str, Any]:
        """Run the complete workflow with iterative refinement support.

        This method implements an iterative workflow that can:
        - Check quality at each stage
        - Roll back to previous stages when quality issues are detected
        - Adjust parameters and retry

        Args:
            resume: Whether to resume from checkpoint if available (default True).
            resume_from_stage: Specific stage to resume from (overrides checkpoint).

        Returns:
            Dictionary containing all outputs and results.
        """
        # Ensure output directory exists
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        results = {
            "topic": self.config.topic,
            "config": to_jsonable(self.config),
            "topic_analysis": to_jsonable(self.topic_analysis),
            "gate_history": [],
        }

        # Check for checkpoint to resume from
        checkpoint = None
        start_stage = CheckpointStage.INIT

        if resume and not resume_from_stage:
            checkpoint = self.checkpoint_manager.load()
            if checkpoint:
                start_stage = self._determine_resume_point(checkpoint)
                print(f"[Resume] Starting from stage: {start_stage}")
                if checkpoint.token_usage:
                    for stage_name, usage in checkpoint.token_usage.get("stages", {}).items():
                        self.token_usage.add_stage(
                            stage_name,
                            usage.get("input_tokens", 0),
                            usage.get("output_tokens", 0),
                        )
        elif resume_from_stage:
            start_stage = resume_from_stage
            checkpoint = self.checkpoint_manager.load()

        # Initialize agents
        self._report_progress("init", "正在初始化Agent...", 0.05)
        agents = self._init_agents()

        # Initialize state variables
        plan: Optional[Dict[str, Any]] = checkpoint.plan if checkpoint else None
        raw_records: List[PaperRecord] = []
        selected_records: List[PaperRecord] = []
        evidence_bank: Optional[Dict[str, Any]] = checkpoint.evidence_bank if checkpoint else None
        draft: Optional[str] = checkpoint.draft if checkpoint else None
        final_draft: Optional[str] = None
        reports: List[ReviewReport] = []

        # Restore from checkpoint
        if checkpoint:
            if checkpoint.raw_records:
                raw_records = restore_records(checkpoint.raw_records)
            if checkpoint.selected_records:
                selected_records = restore_records(checkpoint.selected_records)
            if checkpoint.review_reports:
                reports = [ReviewReport(**r) for r in checkpoint.review_reports]

        # Current stage tracking
        current_stage = start_stage

        # Main workflow loop with iteration support
        while current_stage != CheckpointStage.COMPLETE:

            # ========== PLANNING STAGE ==========
            if current_stage == CheckpointStage.INIT or current_stage == CheckpointStage.PLANNING:
                self._report_progress("planning", "正在规划综述框架...", 0.1)
                plan = await self._plan_review(agents)
                results["plan"] = plan
                self._save_json("search_plan.json", plan)
                # 报告章节结构给前端
                self._report_stage_data("planning", {"sections": plan.get("sections", [])})
                self._save_checkpoint(CheckpointStage.PLANNING, 0.1, "规划完成", plan=plan)

                # 半自动模式：检查是否需要在规划后暂停
                if self._check_pause_and_wait("after_planning", {
                    "plan": plan,
                    "topic_analysis": to_jsonable(self.topic_analysis),
                    "plan_sections": plan.get("sections", []),
                }):
                    results["paused"] = True
                    results["pause_reason"] = "after_planning"
                    return results

                current_stage = CheckpointStage.RETRIEVAL

            # ========== RETRIEVAL STAGE ==========
            elif current_stage == CheckpointStage.RETRIEVAL:
                self._report_progress("retrieval", "正在检索文献...", 0.2)

                # Adjust queries if retrying
                queries = plan.get("search_queries", []) if plan else []
                if self.stage_iterations.get("retrieval", 0) > 0:
                    queries = self._adjust_retrieval_queries(queries, results.get("last_gate_result"))

                raw_records = await self._retrieve_papers_with_queries(plan, agents, queries)
                raw_records_jsonable = to_jsonable(raw_records)
                results["raw_records"] = raw_records_jsonable
                self._save_json("raw_wos_results.json", {"records": raw_records_jsonable})
                # 报告检索到的文献给前端
                self._report_stage_data("retrieval", {"records": raw_records_jsonable})

                # Quality gate check
                if self.enable_quality_gate:
                    gate_result = self.quality_gate.check_retrieval_quality(raw_records)
                    results["last_gate_result"] = {
                        "stage": "retrieval",
                        "decision": gate_result.decision.value,
                        "score": gate_result.score,
                        "issues": gate_result.issues,
                        "suggestions": gate_result.suggestions,
                    }
                    results["gate_history"].append(results["last_gate_result"])

                    if not gate_result.passed:
                        print(f"[QualityGate] Retrieval quality issue: {gate_result.issues}")
                        self.stage_iterations["retrieval"] = self.stage_iterations.get("retrieval", 0) + 1

                        if gate_result.decision == GateDecision.ROLLBACK:
                            current_stage = CheckpointStage.PLANNING
                            self._report_progress("planning", "检索质量不达标，返回规划阶段调整策略...", 0.1)
                            continue
                        elif gate_result.decision == GateDecision.RETRY and self.quality_gate.can_retry("retrieval"):
                            self.quality_gate.increment_retry("retrieval")
                            continue

                self._save_checkpoint(CheckpointStage.RETRIEVAL, 0.2, "检索完成", plan=plan, raw_records=raw_records_jsonable)
                current_stage = CheckpointStage.SCREENING

            # ========== SCREENING STAGE ==========
            elif current_stage == CheckpointStage.SCREENING:
                self._report_progress("screening", "正在筛选文献...", 0.35)
                selected_records = await self._screen_papers(raw_records, plan, agents)
                results["selected_records"] = [to_jsonable(r) for r in selected_records]
                self._save_json("screened_papers.json", {"selected_records": results["selected_records"]})
                # 报告筛选后的文献给前端
                self._report_stage_data("screening", {"selected_records": results["selected_records"]})

                # Quality gate check
                if self.enable_quality_gate:
                    gate_result = self.quality_gate.check_screening_quality(selected_records, raw_records)
                    results["last_gate_result"] = {
                        "stage": "screening",
                        "decision": gate_result.decision.value,
                        "score": gate_result.score,
                        "issues": gate_result.issues,
                        "suggestions": gate_result.suggestions,
                    }
                    results["gate_history"].append(results["last_gate_result"])

                    if not gate_result.passed:
                        print(f"[QualityGate] Screening quality issue: {gate_result.issues}")
                        self.stage_iterations["screening"] = self.stage_iterations.get("screening", 0) + 1

                        if gate_result.decision == GateDecision.ROLLBACK:
                            current_stage = CheckpointStage.RETRIEVAL
                            self._report_progress("retrieval", "筛选结果不足，返回检索阶段补充文献...", 0.2)
                            continue
                        elif gate_result.decision == GateDecision.RETRY and self.quality_gate.can_retry("screening"):
                            self.quality_gate.increment_retry("screening")
                            continue

                self._save_checkpoint(
                    CheckpointStage.SCREENING, 0.35, "筛选完成",
                    plan=plan, raw_records=to_jsonable(raw_records), selected_records=results["selected_records"],
                )

                # 半自动模式：检查是否需要在筛选后暂停
                if self._check_pause_and_wait("after_screening", {
                    "selected_records": results["selected_records"],
                    "raw_records": results.get("raw_records", []),
                    "total_selected": len(selected_records),
                    "total_retrieved": len(raw_records),
                }):
                    results["paused"] = True
                    results["pause_reason"] = "after_screening"
                    return results

                current_stage = CheckpointStage.ANALYSIS

            # ========== ANALYSIS STAGE ==========
            elif current_stage == CheckpointStage.ANALYSIS:
                self._report_progress("analysis", "正在分析文献...", 0.5)
                evidence_bank = await self._extract_evidence(selected_records, plan, agents)
                results["evidence_bank"] = evidence_bank
                self._save_json("evidence_bank.json", evidence_bank)
                self._save_checkpoint(
                    CheckpointStage.ANALYSIS, 0.5, "分析完成",
                    plan=plan, raw_records=to_jsonable(raw_records),
                    selected_records=results["selected_records"], evidence_bank=evidence_bank,
                )
                current_stage = CheckpointStage.WRITING

            # ========== WRITING STAGE ==========
            elif current_stage == CheckpointStage.WRITING:
                self._report_progress("writing", "正在撰写综述...", 0.65)
                draft = await self._write_draft(plan, evidence_bank, agents)
                results["draft_v1"] = draft
                self._save_text("draft_v1.md", draft)
                # 报告草稿预览给前端
                self._report_stage_data("writing", {"draft": draft})

                # Quality gate check for draft
                if self.enable_quality_gate:
                    gate_result = self.quality_gate.check_draft_quality(draft, selected_records)
                    results["last_gate_result"] = {
                        "stage": "writing",
                        "decision": gate_result.decision.value,
                        "score": gate_result.score,
                        "issues": gate_result.issues,
                        "suggestions": gate_result.suggestions,
                    }
                    results["gate_history"].append(results["last_gate_result"])

                    if not gate_result.passed:
                        print(f"[QualityGate] Draft quality issue: {gate_result.issues}")
                        self.stage_iterations["writing"] = self.stage_iterations.get("writing", 0) + 1

                        if gate_result.decision == GateDecision.RETRY and self.quality_gate.can_retry("writing"):
                            self.quality_gate.increment_retry("writing")
                            # Re-write with specific instructions
                            draft = await self._rewrite_draft(plan, evidence_bank, agents, draft, gate_result.suggestions)
                            results["draft_v2"] = draft
                            self._save_text("draft_v2.md", draft)
                            continue

                self._save_checkpoint(
                    CheckpointStage.WRITING, 0.65, "撰写完成",
                    plan=plan, raw_records=to_jsonable(raw_records),
                    selected_records=results["selected_records"], evidence_bank=evidence_bank, draft=draft,
                )
                current_stage = CheckpointStage.REVIEW

            # ========== REVIEW STAGE ==========
            elif current_stage == CheckpointStage.REVIEW:
                self._report_progress("review", "正在进行审稿...", 0.75)
                final_draft, reports = await self._run_review_loop(plan, evidence_bank, agents, draft)
                results["final_draft"] = final_draft
                results["review_reports"] = [to_jsonable(r) for r in reports]
                self._save_checkpoint(
                    CheckpointStage.REVIEW, 0.80, "审稿完成",
                    plan=plan, selected_records=results["selected_records"],
                    evidence_bank=evidence_bank, final_draft=final_draft,
                )
                current_stage = CheckpointStage.POLISH

            # ========== POLISH STAGE ==========
            elif current_stage == CheckpointStage.POLISH:
                self._report_progress("polish", "正在进行专业润色...", 0.85)
                polished_draft, polish_summary = await self._polish_draft(final_draft, agents)
                final_draft = polished_draft
                results["final_draft"] = final_draft
                results["polish_summary"] = polish_summary
                self._save_checkpoint(
                    CheckpointStage.POLISH, 0.90, "润色完成",
                    plan=plan, selected_records=results["selected_records"],
                    final_draft=final_draft,
                )
                current_stage = CheckpointStage.FINALIZING

            # ========== FINALIZING STAGE ==========
            elif current_stage == CheckpointStage.FINALIZING:
                self._report_progress("finalizing", "正在生成最终稿件...", 0.95)
                final_markdown, validation = self._finalize(final_draft, selected_records)

                # 质量门控检查 - 不通过时触发修正
                if self.enable_quality_gate and not validation.get("passes", True):
                    print(f"[QualityGate] Final validation issues: {validation}")

                    # 处理字数超标问题：压缩稿件（最多尝试 2 次）
                    word_count = validation.get("word_count", 0)
                    if word_count > self.config.word_count_max:
                        print(f"[QualityGate] 字数超标 ({word_count} > {self.config.word_count_max})，触发压缩...")
                        self._report_progress("finalizing", f"字数超标，正在压缩稿件...", 0.92)

                        for attempt in range(2):
                            final_draft = await self._compress_draft(final_draft, self.config.word_count_max)
                            final_markdown, validation = self._finalize(final_draft, selected_records)

                            new_word_count = validation.get("word_count", 0)
                            if new_word_count <= self.config.word_count_max:
                                print(f"[QualityGate] 压缩成功，当前字数: {new_word_count}")
                                break

                            print(f"[QualityGate] 第 {attempt + 1} 次压缩后仍超标 ({new_word_count})，{'再次尝试...' if attempt < 1 else '放弃压缩'}")

                    # 处理文献不足问题：记录警告，但不阻塞
                    ref_count = validation.get("unique_citation_count", 0)
                    if ref_count < self.config.minimum_acceptable_refs:
                        print(f"[QualityGate] 警告：文献数量不足 ({ref_count} < {self.config.minimum_acceptable_refs})")

                results["final_markdown"] = final_markdown
                results["validation"] = validation
                self._save_text(str(self.config.output_path), final_markdown)
                self._save_json("validation_report.json", validation)

                current_stage = CheckpointStage.COMPLETE

        # Add token usage summary
        results["token_usage"] = self.token_usage.to_dict()
        results["stage_iterations"] = self.stage_iterations
        self._save_json("token_usage.json", results["token_usage"])

        self._report_progress("complete", "工作流执行完成", 1.0)
        return results

    def _adjust_retrieval_queries(
        self,
        original_queries: List[Dict[str, Any]],
        last_gate_result: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Adjust retrieval queries based on previous gate result."""
        if not last_gate_result:
            return original_queries

        suggestions = last_gate_result.get("suggestions", [])
        adjusted = list(original_queries)

        # Add suggested queries
        for suggestion in suggestions[:3]:
            adjusted.append({
                "query": suggestion,
                "intent": f"补充检索：{suggestion[:30]}",
                "priority": 2,
            })

        return adjusted

    async def _retrieve_papers_with_queries(
        self,
        plan: Dict[str, Any],
        agents: Dict[str, Any],
        queries: List[Dict[str, Any]],
    ) -> List[PaperRecord]:
        """Retrieve papers with specific queries."""
        if not queries:
            queries = plan.get("search_queries", [{"query": self.config.topic, "intent": self.config.topic, "priority": 1}])

        # Get retrieval strategy from agent
        prompt = self.prompt_renderer.render_retrieve_papers(
            topic=self.config.topic,
            search_queries=queries,
        )
        try:
            strategy_raw = self._execute_agent(
                agents["retriever_agent"],
                prompt,
                "检索策略 JSON。",
                stage="retrieval",
            )
            strategy = safe_json_loads(strategy_raw)
            queries = strategy.get("queries", queries)
        except Exception:
            pass

        # Fetch from all sources
        records, notices = self.retriever_manager.fetch_all(queries)

        # Score papers
        terms = self.topic_analysis.keywords + self.topic_analysis.search_terms
        for record in records:
            record.relevance_score = score_paper(record, self.config, terms)
        records.sort(key=lambda r: r.relevance_score, reverse=True)

        # Check topic coverage and potentially trigger supplementary retrieval
        coverage = check_topic_coverage(
            records=records,
            topic_keywords=self.topic_analysis.keywords[:10],
            min_papers_per_topic=2,
        )

        if not coverage["coverage_ok"] and coverage["suggested_queries"]:
            self._report_progress("retrieval", f"主题覆盖不足，补充检索: {', '.join(coverage['underrepresented_topics'][:3])}", 0.25)

            supplementary_queries = [
                {"query": q, "intent": f"补充检索: {q}", "priority": 2}
                for q in coverage["suggested_queries"][:3]
            ]

            if supplementary_queries:
                extra_records, _ = self.retriever_manager.fetch_all(supplementary_queries)

                for record in extra_records:
                    record.relevance_score = score_paper(record, self.config, terms)

                existing_ids = {r.ref_id for r in records}
                for record in extra_records:
                    if record.ref_id not in existing_ids:
                        records.append(record)
                        existing_ids.add(record.ref_id)

                records.sort(key=lambda r: r.relevance_score, reverse=True)

        return records

    async def _rewrite_draft(
        self,
        plan: Dict[str, Any],
        evidence_bank: Dict[str, Any],
        agents: Dict[str, Any],
        current_draft: str,
        suggestions: List[str],
    ) -> str:
        """Rewrite draft with specific improvements."""
        # Create improvement-focused prompt
        improvement_prompt = f"""
请根据以下反馈意见改进综述稿件：

当前问题：
{chr(10).join(f'- {s}' for s in suggestions)}

改进要求：
1. 针对每个问题逐一修改
2. 保持原文优点
3. 确保修改后的内容符合字数要求

当前稿件：
{current_draft[:12000]}

请输出修改后的完整稿件（Markdown 格式）。
"""
        return self._execute_agent(
            agents["writer_agent"],
            improvement_prompt,
            "改进后的 Markdown 综述稿件。",
            stage="rewriting",
        )

    async def _plan_review(self, agents: Dict[str, Any]) -> Dict[str, Any]:
        """Generate the review plan."""
        prompt = self.prompt_renderer.render_plan_review(
            topic=self.config.topic,
            word_count_min=self.config.word_count_min,
            word_count_max=self.config.word_count_max,
            target_refs=self.config.target_refs,
            year_window=self.config.year_window,
            user_description=self.config.user_description,
            journal_type=self.config.journal_type,
            language=self.config.language,
        )
        try:
            raw = self._execute_agent(
                agents["planner_agent"],
                prompt,
                "结构化 JSON 综述执行计划。",
                stage="planning",
            )
            return safe_json_loads(raw)
        except Exception:
            return self._fallback_plan()

    def _fallback_plan(self) -> Dict[str, Any]:
        """Generate a fallback plan when LLM fails."""
        return {
            "review_title": self.config.topic,
            "scope_statement": f"聚焦近 3-5 年{self.topic_analysis.domain}领域的研究进展。",
            "search_queries": [
                {"query": self.config.topic, "intent": self.config.topic, "priority": 1}
            ],
            "inclusion_rules": ["优先近5年英文论文", "优先JCR Q1/Q2期刊"],
            "exclusion_rules": ["与主题无实质关系的论文"],
            "sections": self.topic_analysis.suggested_sections,
            "table_plan": [
                {"table_id": "表1", "title": "代表性文献总览", "columns": ["作者", "年份", "期刊", "方法", "结论"]},
                {"table_id": "表2", "title": "方法比较", "columns": ["方法", "优势", "局限", "适用场景"]},
                {"table_id": "表3", "title": "未来方向", "columns": ["研究空白", "改进路径", "应用前景"]},
            ],
            "quality_targets": {
                "target_refs": self.config.target_refs,
                "minimum_acceptable_refs": self.config.minimum_acceptable_refs,
                "word_count_min": self.config.word_count_min,
                "word_count_max": self.config.word_count_max,
                "year_window": self.config.year_window,
            },
        }

    async def _retrieve_papers(
        self,
        plan: Dict[str, Any],
        agents: Dict[str, Any],
    ) -> List[PaperRecord]:
        """Retrieve papers from multiple sources."""
        queries = plan.get("search_queries", [{"query": self.config.topic, "intent": self.config.topic, "priority": 1}])

        # Get retrieval strategy from agent
        prompt = self.prompt_renderer.render_retrieve_papers(
            topic=self.config.topic,
            search_queries=queries,
        )
        try:
            strategy_raw = self._execute_agent(
                agents["retriever_agent"],
                prompt,
                "检索策略 JSON。",
                stage="retrieval",
            )
            strategy = safe_json_loads(strategy_raw)
            queries = strategy.get("queries", queries)
        except Exception:
            pass

        # Fetch from all sources
        records, notices = self.retriever_manager.fetch_all(queries)

        # Score papers
        terms = self.topic_analysis.keywords + self.topic_analysis.search_terms
        for record in records:
            record.relevance_score = score_paper(record, self.config, terms)
        records.sort(key=lambda r: r.relevance_score, reverse=True)

        # Check topic coverage and potentially trigger supplementary retrieval
        coverage = check_topic_coverage(
            records=records,
            topic_keywords=self.topic_analysis.keywords[:10],
            min_papers_per_topic=2,
        )

        if not coverage["coverage_ok"] and coverage["suggested_queries"]:
            self._report_progress("retrieval", f"主题覆盖不足，补充检索: {', '.join(coverage['underrepresented_topics'][:3])}", 0.25)

            # Add supplementary queries for underrepresented topics
            supplementary_queries = [
                {"query": q, "intent": f"补充检索: {q}", "priority": 2}
                for q in coverage["suggested_queries"][:3]
            ]

            if supplementary_queries:
                extra_records, _ = self.retriever_manager.fetch_all(supplementary_queries)

                # Score and merge
                for record in extra_records:
                    record.relevance_score = score_paper(record, self.config, terms)

                # Merge and dedupe
                existing_ids = {r.ref_id for r in records}
                for record in extra_records:
                    if record.ref_id not in existing_ids:
                        records.append(record)
                        existing_ids.add(record.ref_id)

                records.sort(key=lambda r: r.relevance_score, reverse=True)

        return records

    async def _screen_papers(
        self,
        records: List[PaperRecord],
        plan: Dict[str, Any],
        agents: Dict[str, Any],
    ) -> List[PaperRecord]:
        """Screen and select papers with domain relevance filtering."""
        from datetime import datetime
        current_year = datetime.now().year
        cutoff_year = current_year - self.config.year_window + 1

        # Step 1: Domain relevance pre-filtering
        domain_keywords = self.topic_analysis.keywords[:10]
        relevant_records, excluded_records = prefilter_domain_relevance(
            records=records,
            topic=self.config.topic,
            domain_keywords=domain_keywords + FOOD_SCIENCE_DOMAIN_KEYWORDS,
            exclusion_keywords=FOOD_SCIENCE_EXCLUSION_KEYWORDS,
            min_relevance_score=3.0,
        )

        print(f"[Screening] Pre-filtered: {len(relevant_records)} relevant, {len(excluded_records)} excluded")

        # Step 2: Time and quality filter
        prelim = [
            r for r in relevant_records
            if (r.year and r.year >= cutoff_year) or r.relevance_score >= 5.0 or r.times_cited >= 50
        ]

        # If too few after filtering, relax criteria
        if len(prelim) < self.config.minimum_acceptable_refs:
            prelim = relevant_records

        prelim.sort(key=lambda r: r.relevance_score, reverse=True)
        candidate_pool = prelim[:self.config.retrieval_pool_size]

        # Step 3: Screen with agent
        prompt = self.prompt_renderer.render_screen_papers(
            target_refs=self.config.target_refs,
            year_window=self.config.year_window,
            old_paper_ratio_percent=int(self.config.old_paper_ratio_limit * 100),
            coverage_topics=self.topic_analysis.keywords[:10],
            candidate_summary=compact_paper_summary(candidate_pool),
        )
        try:
            screening_raw = self._execute_agent(
                agents["screener_agent"],
                prompt,
                "筛选结果 JSON。",
                stage="screening",
            )
            screening = safe_json_loads(screening_raw)
            record_map = {r.ref_id: r for r in candidate_pool}
            selected = [record_map[ref_id] for ref_id in screening.get("selected_ref_ids", []) if ref_id in record_map]

            # Log exclusion reasons if available
            if screening.get("exclusion_reasons"):
                print(f"[Screening] LLM exclusion reasons: {len(screening['exclusion_reasons'])} papers")

            # 如果筛选结果不足，从 candidate_pool 中按相关性补充
            if len(selected) < self.config.target_refs:
                print(f"[Screening] 筛选结果不足 ({len(selected)} < {self.config.target_refs})，从候选池补充...")
                selected_ids = {r.ref_id for r in selected}
                remaining = [r for r in candidate_pool if r.ref_id not in selected_ids]
                remaining.sort(key=lambda r: r.relevance_score, reverse=True)
                need_count = self.config.target_refs - len(selected)
                selected.extend(remaining[:need_count])
                print(f"[Screening] 补充后共 {len(selected)} 篇文献")

            # 如果仍然不足最低要求，返回全部候选
            if len(selected) < self.config.minimum_acceptable_refs:
                print(f"[Screening] 警告：文献数量仍不足最低要求 ({len(selected)} < {self.config.minimum_acceptable_refs})")

            return selected
        except Exception as e:
            print(f"[Screening] Agent screening failed: {e}")
            return candidate_pool[:self.config.target_refs]

    async def _extract_evidence(
        self,
        records: List[PaperRecord],
        plan: Dict[str, Any],
        agents: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract evidence notes from papers."""
        notes: List[EvidenceNote] = []
        section_titles = [s.get("title", "") for s in plan.get("sections", [])]

        # Process in batches
        batch_num = 0
        for i in range(0, len(records), 8):
            batch = records[i:i+8]
            batch_num += 1
            prompt = self.prompt_renderer.render_analyze_papers(
                topic=self.config.topic,
                section_titles=section_titles,
                paper_summary=compact_paper_summary(batch, limit=20),
            )
            try:
                batch_raw = self._execute_agent(
                    agents["analyzer_agent"],
                    prompt,
                    "文献批次分析 JSON。",
                    stage=f"analysis_batch_{batch_num}",
                )
                batch_result = safe_json_loads(batch_raw)
                notes.extend(evidence_note_from_dict(item) for item in batch_result.get("notes", []))
            except Exception:
                continue

        return {
            "selected_records": [to_jsonable(r) for r in records],
            "evidence_notes": [to_jsonable(n) for n in notes],
            "synthesis": {
                "common_themes": [],
                "differences": [],
                "controversies": [],
                "research_gaps": [],
                "future_directions": [],
            },
        }

    async def _write_draft(
        self,
        plan: Dict[str, Any],
        evidence_bank: Dict[str, Any],
        agents: Dict[str, Any],
    ) -> str:
        """Write the initial draft."""
        selected_records = [PaperRecord(**r) for r in evidence_bank.get("selected_records", [])]
        prompt = self.prompt_renderer.render_write_review(
            topic=self.config.topic,
            word_count_min=self.config.word_count_min,
            word_count_max=self.config.word_count_max,
            plan=plan,
            paper_records=serialise_records_for_prompt(selected_records),
            evidence_notes=evidence_bank.get("evidence_notes", []),
            synthesis=evidence_bank.get("synthesis", {}),
            user_description=self.config.user_description,
        )
        return self._execute_agent(agents["writer_agent"], prompt, "完整 Markdown 综述稿件。", stage="writing")

    async def _run_review_loop(
        self,
        plan: Dict[str, Any],
        evidence_bank: Dict[str, Any],
        agents: Dict[str, Any],
        current_draft: str,
    ) -> Tuple[str, List[ReviewReport]]:
        """Run the review-revise loop."""
        reports: List[ReviewReport] = []
        current_round = 1

        while current_round <= self.config.review_rounds_max:
            # 计算正文字数（去除参考文献部分）
            body_text = re.sub(r'\n## 参考文献\s*.*$', '', current_draft, flags=re.S)
            body_word_count = estimate_word_count(body_text)

            # Review - 传入完整稿件和字数信息
            prompt = self.prompt_renderer.render_review_draft(
                draft_content=current_draft,
                synthesis=evidence_bank.get("synthesis", {}),
                word_count_info={
                    "body_word_count": body_word_count,
                    "target_min": self.config.word_count_min,
                    "target_max": self.config.word_count_max,
                },
            )
            try:
                report_raw = self._execute_agent(
                    agents["reviewer_agent"],
                    prompt,
                    "审稿报告 JSON。",
                    stage=f"review_round_{current_round}",
                )
                report_dict = safe_json_loads(report_raw)

                # Ensure report_dict is a dict
                if not isinstance(report_dict, dict):
                    print(f"Warning: report_dict is {type(report_dict).__name__}, using default")
                    report_dict = {}

            except Exception as e:
                print(f"Warning: Failed to parse review report in round {current_round}: {e}")
                report_dict = {}

            # Create default values if missing
            default_scorecard = {
                "文献覆盖度": 8.0,
                "新近性与刊源质量": 8.0,
                "归纳与比较深度": 8.0,
                "批判性分析质量": 8.0,
                "研究空白与未来方向质量": 8.0,
                "引用与格式规范性": 8.0,
            }
            scorecard = report_dict.get("scorecard", default_scorecard)
            if not isinstance(scorecard, dict):
                scorecard = default_scorecard

            # Parse issues safely
            blocking_issues = []
            major_issues = []
            minor_issues = []

            for i in report_dict.get("blocking_issues", []):
                if isinstance(i, dict):
                    blocking_issues.append(review_issue_from_dict(i, "blocking"))

            for i in report_dict.get("major_issues", []):
                if isinstance(i, dict):
                    major_issues.append(review_issue_from_dict(i, "major"))

            for i in report_dict.get("minor_issues", []):
                if isinstance(i, dict):
                    minor_issues.append(review_issue_from_dict(i, "minor"))

            report = ReviewReport(
                round_index=current_round,
                scorecard=scorecard,
                blocking_issues=blocking_issues,
                major_issues=major_issues,
                minor_issues=minor_issues,
                decision=report_dict.get("decision", "revise") if isinstance(report_dict, dict) else "revise",
            )
            reports.append(report)
            self._save_json(f"review_round_{current_round}.json", to_jsonable(report))

            # Check if we should continue
            # total_score 满分 60 分，54 分（90%）以上可以接受
            # min_dimension_score 满分 10 分，8 分以下说明有明显短板
            must_continue = (
                current_round < self.config.review_rounds_min
                or report.has_blocking()
                or report.min_dimension_score() < 8.0
                or report.total_score() < 54.0  # 满分 60，90% 为 54 分
            )
            if not must_continue or current_round >= self.config.review_rounds_max:
                break

            # Revise - 传入完整稿件，不截断
            prompt = self.prompt_renderer.render_revise_draft(
                current_draft=current_draft,
                review_report=report_dict,
                plan=plan,
                evidence_bank=evidence_bank,
                round_index=current_round + 1,
                user_description=self.config.user_description,
            )
            revision_raw = self._execute_agent(
                agents["writer_agent"],
                prompt,
                "修订响应 JSON + 修订后的 Markdown。",
                stage=f"revision_round_{current_round}",
            )
            _, current_draft = split_revision_payload(revision_raw)
            self._save_text(f"draft_v{current_round + 1}.md", current_draft)
            current_round += 1

        return current_draft, reports

    async def _polish_draft(
        self,
        draft: str,
        agents: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """Polish the draft for language quality.

        Args:
            draft: The draft content to polish.
            agents: Dictionary of agents.

        Returns:
            Tuple of (polished_draft, polish_summary).
        """
        prompt = self.prompt_renderer.render_polish_draft(
            draft=draft,
            topic=self.config.topic,
            journal_type=self.config.journal_type,
            language=self.config.language,
            user_description=self.config.user_description,
        )

        polish_raw = self._execute_agent(
            agents["polisher_agent"],
            prompt,
            "润色后的 Markdown 稿件和润色说明 JSON。",
            stage="polish",
        )

        # 解析润色结果
        result = safe_json_loads(polish_raw)

        if isinstance(result, dict) and "polished_draft" in result:
            polished_draft = result.get("polished_draft", draft)
            polish_summary = result.get("polish_summary", {})
        else:
            # 如果 JSON 解析失败，尝试提取 Markdown
            _, polished_draft = split_revision_payload(polish_raw)
            polish_summary = {"raw_response": polish_raw[:500]}

        # 保存润色后的稿件
        self._save_text("polished_draft.md", polished_draft)
        self._save_json("polish_summary.json", polish_summary)

        return polished_draft, polish_summary

    def _finalize(
        self,
        final_draft: str,
        selected_records: List[PaperRecord],
    ) -> Tuple[str, Dict[str, Any]]:
        """Finalize the review with proper citations."""
        # Validate citations
        ref_map = {r.ref_id: r for r in selected_records}
        cited_ids = re.findall(r"\[@(REF\d{3})\]", final_draft)
        ordered_ids = []
        seen = set()
        for ref_id in cited_ids:
            if ref_id not in seen and ref_id in ref_map:
                ordered_ids.append(ref_id)
                seen.add(ref_id)

        # Replace internal citations
        ordered_lookup = {ref_id: i for i, ref_id in enumerate(ordered_ids, start=1)}
        def replacer(match: re.Match) -> str:
            ref_id = match.group(1)
            return f"[{ordered_lookup[ref_id]}]" if ref_id in ordered_lookup else f"[{ref_id}]"
        body = re.sub(r"\[@(REF\d{3})\]", replacer, final_draft)

        # Strip existing references section
        match = re.search(r"\n## 参考文献\s*.*$", body, re.S)
        if match:
            body = body[:match.start()].rstrip() + "\n"

        # Build references
        references = [
            f"{i}. {format_reference(ref_map[ref_id])}"
            for i, ref_id in enumerate(ordered_ids, start=1)
        ]

        final_markdown = body + "\n\n## 参考文献\n\n" + "\n".join(references) + "\n"

        # Validation report - 字数只统计正文，不包含参考文献
        body_word_count = estimate_word_count(body)
        validation = {
            "word_count": body_word_count,  # 仅正文字数
            "word_count_with_refs": estimate_word_count(final_markdown),  # 包含参考文献的总字数
            "unique_citation_count": len(ordered_ids),
            "within_word_range": self.config.word_count_min <= body_word_count <= self.config.word_count_max,
            "has_minimum_refs": len(ordered_ids) >= self.config.minimum_acceptable_refs,
            "passes": (
                len(ordered_ids) >= self.config.minimum_acceptable_refs
                and self.config.word_count_min <= body_word_count <= self.config.word_count_max
            ),
        }

        return final_markdown, validation

    def _save_json(self, filename: str, data: Any) -> None:
        """Save JSON data to file."""
        path = self.config.output_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_text(self, filename: str, content: str) -> None:
        """Save text to file."""
        if isinstance(filename, str):
            # 如果是纯文件名（不含路径分隔符），则添加 output_dir
            if '/' not in filename and '\\' not in filename:
                path = self.config.output_dir / filename
            else:
                path = Path(filename)
        else:
            path = filename

        # 确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def _compress_draft(self, draft: str, target_word_count: int) -> str:
        """Compress draft to target word count while preserving key content.

        Args:
            draft: The draft content to compress.
            target_word_count: Target word count.

        Returns:
            Compressed draft content.
        """
        import asyncio

        # 计算压缩比例
        current_words = len(draft)
        if current_words <= target_word_count:
            return draft

        compression_ratio = target_word_count / current_words
        print(f"[QualityGate] 压缩比例: {compression_ratio:.2%}")

        # 使用 Agent 压缩稿件
        prompt = f"""请将以下综述稿件压缩到约 {target_word_count} 字。

当前字数：约 {current_words} 字
目标字数：约 {target_word_count} 字

压缩要求：
1. 保留核心论点和关键数据
2. 精简冗余描述和重复内容
3. 合并相似段落
4. 保留所有表格（可以简化表格内容）
5. 保留所有引用标记 [@REFxxx]
6. 保持章节结构完整

请直接输出压缩后的完整稿件，不要输出任何解释。

---

{draft}
"""
        try:
            writer_agent = self.agents.get("writer_agent")
            if not writer_agent:
                print("[QualityGate] writer_agent 未初始化，返回原稿件")
                return draft

            # 使用 asyncio.to_thread 包装同步调用，避免阻塞事件循环
            compressed = await asyncio.to_thread(
                self._execute_agent,
                writer_agent,
                prompt,
                "压缩后的 Markdown 稿件。",
                "compression",
            )
            return compressed if compressed else draft
        except Exception as e:
            print(f"[QualityGate] 压缩失败: {e}，返回原稿件")
            return draft
