# Prompt Renderer Service - renders prompts using Jinja2 templates
import json
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, Template
from markupsafe import Markup


def tojson_filter(value: Any, indent: Optional[int] = None) -> str:
    """Custom JSON filter for Jinja2 that supports indent parameter.

    Args:
        value: The value to serialize to JSON.
        indent: Optional indentation level.

    Returns:
        JSON string (marked as safe for Jinja2).
    """
    result = json.dumps(value, ensure_ascii=False, indent=indent)
    return Markup(result)


class PromptRenderer:
    """Service for rendering prompts using Jinja2 templates."""

    def __init__(self, template_dir: Optional[Path] = None) -> None:
        """Initialize the prompt renderer.

        Args:
            template_dir: Directory containing prompt templates.
        """
        if template_dir is None:
            template_dir = Path(__file__).parent.parent / "core" / "prompts" / "task_templates"

        self.template_dir = template_dir
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Add custom filters
        self.env.filters["tojson"] = tojson_filter

    def render(
        self,
        template_name: str,
        context: Dict[str, Any],
    ) -> str:
        """Render a prompt template with the given context.

        Args:
            template_name: Name of the template file (without .j2 extension).
            context: Dictionary of variables to pass to the template.

        Returns:
            Rendered prompt string.
        """
        template = self._get_template(template_name)
        return template.render(**context)

    def render_plan_review(
        self,
        topic: str,
        word_count_min: int,
        word_count_max: int,
        target_refs: int,
        year_window: int,
        user_description: str = "",
        journal_type: str = "中文核心期刊",
        language: str = "中文",
    ) -> str:
        """Render the plan review prompt."""
        return self.render("plan_review", {
            "topic": topic,
            "word_count_min": word_count_min,
            "word_count_max": word_count_max,
            "target_refs": target_refs,
            "year_window": year_window,
            "user_description": user_description,
            "journal_type": journal_type,
            "language": language,
        })

    def render_retrieve_papers(
        self,
        topic: str,
        search_queries: list,
    ) -> str:
        """Render the retrieve papers prompt."""
        return self.render("retrieve_papers", {
            "topic": topic,
            "search_queries": search_queries,
        })

    def render_screen_papers(
        self,
        target_refs: int,
        year_window: int,
        old_paper_ratio_percent: int,
        coverage_topics: list,
        candidate_summary: str,
    ) -> str:
        """Render the screen papers prompt."""
        return self.render("screen_papers", {
            "target_refs": target_refs,
            "year_window": year_window,
            "old_paper_ratio_percent": old_paper_ratio_percent,
            "coverage_topics": coverage_topics,
            "candidate_summary": candidate_summary,
        })

    def render_analyze_papers(
        self,
        topic: str,
        section_titles: list,
        paper_summary: str,
    ) -> str:
        """Render the analyze papers prompt."""
        return self.render("analyze_papers", {
            "topic": topic,
            "section_titles": section_titles,
            "paper_summary": paper_summary,
        })

    def render_write_review(
        self,
        topic: str,
        word_count_min: int,
        word_count_max: int,
        plan: dict,
        paper_records: str,
        evidence_notes: list,
        synthesis: dict,
        user_description: str = "",
    ) -> str:
        """Render the write review prompt."""
        return self.render("write_review", {
            "topic": topic,
            "word_count_min": word_count_min,
            "word_count_max": word_count_max,
            "plan": plan,
            "paper_records": paper_records,
            "evidence_notes": evidence_notes,
            "synthesis": synthesis,
            "user_description": user_description,
        })

    def render_review_draft(
        self,
        draft_content: str,
        synthesis: dict,
        word_count_info: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render the review draft prompt.

        Args:
            draft_content: The draft content to review.
            synthesis: Synthesis information.
            word_count_info: Optional word count information containing:
                - body_word_count: Word count excluding references
                - target_min: Minimum target word count
                - target_max: Maximum target word count
        """
        return self.render("review_draft", {
            "draft_content": draft_content,
            "synthesis": synthesis,
            "word_count_info": word_count_info or {},
        })

    def render_revise_draft(
        self,
        current_draft: str,
        review_report: dict,
        plan: dict,
        evidence_bank: dict,
        round_index: int,
        user_description: str = "",
    ) -> str:
        """Render the revise draft prompt."""
        return self.render("revise_draft", {
            "current_draft": current_draft,
            "review_report": review_report,
            "plan": plan,
            "evidence_bank": evidence_bank,
            "round_index": round_index,
            "user_description": user_description,
        })

    def render_polish_draft(
        self,
        draft: str,
        topic: str,
        journal_type: str = "中文核心期刊",
        language: str = "中文",
        user_description: str = "",
    ) -> str:
        """Render the polish draft prompt.

        Args:
            draft: The draft content to polish.
            topic: The review topic.
            journal_type: Target journal type.
            language: Review language.
            user_description: User's writing expectations.
        """
        return self.render("polish_draft", {
            "draft": draft,
            "topic": topic,
            "journal_type": journal_type,
            "language": language,
            "user_description": user_description,
        })

    def _get_template(self, name: str) -> Template:
        """Get a template by name."""
        template_name = f"{name}.j2"
        try:
            return self.env.get_template(template_name)
        except Exception:
            raise ValueError(f"Template not found: {template_name}")

    def register_template(self, name: str, content: str) -> None:
        """Register a custom template.

        Args:
            name: Template name.
            content: Template content.
        """
        # Write to template directory
        template_path = self.template_dir / f"{name}.j2"
        template_path.write_text(content, encoding="utf-8")

    def list_templates(self) -> list:
        """List available templates."""
        return [p.stem for p in self.template_dir.glob("*.j2")]
