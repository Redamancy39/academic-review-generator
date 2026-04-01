# Agent Generator Service - dynamically generates CrewAI agent definitions
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, Template

from ..core.models import AgentDefinition, TopicAnalysis


class AgentGenerator:
    """Service for generating CrewAI agent definitions dynamically."""

    # Default agent roles
    DEFAULT_ROLES = [
        "planner",
        "retriever",
        "screener",
        "analyzer",
        "writer",
        "reviewer",
    ]

    def __init__(self, template_dir: Optional[Path] = None) -> None:
        """Initialize the agent generator.

        Args:
            template_dir: Directory containing agent templates.
        """
        if template_dir is None:
            template_dir = Path(__file__).parent.parent / "core" / "prompts" / "agent_templates"

        self.template_dir = template_dir
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def generate(
        self,
        role: str,
        topic_analysis: TopicAnalysis,
        config: Optional[Dict[str, Any]] = None,
    ) -> AgentDefinition:
        """Generate an agent definition for a specific role.

        Args:
            role: The agent role (planner, retriever, screener, analyzer, writer, reviewer).
            topic_analysis: The parsed topic analysis.
            config: Optional configuration overrides.

        Returns:
            AgentDefinition object.
        """
        config = config or {}

        # Get the template
        template = self._get_template(role)

        # Prepare context
        context = self._prepare_context(role, topic_analysis, config)

        # Render the template
        rendered = template.render(context)

        # Parse the rendered content
        return self._parse_rendered(rendered, role)

    def generate_all(
        self,
        topic_analysis: TopicAnalysis,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, AgentDefinition]:
        """Generate all agent definitions for a review workflow.

        Args:
            topic_analysis: The parsed topic analysis.
            config: Optional configuration overrides.

        Returns:
            Dictionary mapping role names to AgentDefinition objects.
        """
        agents = {}
        for role in self.DEFAULT_ROLES:
            agents[role] = self.generate(role, topic_analysis, config)
        return agents

    def _get_template(self, role: str) -> Template:
        """Get the template for a specific role."""
        template_name = f"{role}.j2"
        try:
            return self.env.get_template(template_name)
        except Exception:
            # Return a default template if specific one not found
            return self.env.from_string(self._get_default_template(role))

    def _get_default_template(self, role: str) -> str:
        """Get default template content for a role."""
        defaults = {
            "planner": """role: "综述选题与框架总策划"
goal: |
  规划一篇高质量的{{ domain }}领域综述，主题为"{{ topic }}"。
backstory: |
  你擅长把宽泛研究主题拆解为可执行的系统综述路线。
verbose: true""",
            "retriever": """role: "学术检索策略专家"
goal: |
  把主题"{{ topic }}"转化为高质量检索式。
backstory: |
  你熟悉各大数据库的检索逻辑。
verbose: true""",
            "screener": """role: "高水平文献筛选审稿人"
goal: |
  从候选文献中筛出高质量核心文献池。
backstory: |
  你对来源质量和主题覆盖度要求严格。
verbose: true""",
            "analyzer": """role: "文献综合分析研究员"
goal: |
  提炼每篇文献的核心内容，形成跨文献比较。
backstory: |
  你擅长抓取共性、差异、争议和研究空白。
verbose: true""",
            "writer": """role: "综述主笔"
goal: |
  基于证据库撰写高质量的中文综述。
backstory: |
  你熟悉综述文风，强调归纳、比较和批判。
verbose: true""",
            "reviewer": """role: "严苛终审专家"
goal: |
  对综述进行苛刻审查，推动多轮修订。
backstory: |
  你对综述稿件要求近乎苛刻。
verbose: true""",
        }
        return defaults.get(role, defaults["planner"])

    def _prepare_context(
        self,
        role: str,
        topic_analysis: TopicAnalysis,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Prepare the template rendering context."""
        return {
            "topic": config.get("topic", ""),
            "domain": topic_analysis.domain,
            "keywords": topic_analysis.keywords,
            "sub_domains": topic_analysis.sub_domains,
            "journal_type": config.get("journal_type", "中文顶级期刊"),
            "word_count_min": config.get("word_count_min", 4000),
            "word_count_max": config.get("word_count_max", 6000),
            "target_refs": config.get("target_refs", 40),
            "year_window": config.get("year_window", 5),
            "paper_count": config.get("paper_count", 0),
        }

    def _parse_rendered(self, rendered: str, role: str) -> AgentDefinition:
        """Parse rendered template content into AgentDefinition."""
        # Simple YAML-like parsing
        lines = rendered.strip().split("\n")
        data = {}
        current_key = None
        current_value = []

        for line in lines:
            if ":" in line and not line.startswith(" "):
                if current_key and current_value:
                    data[current_key] = "\n".join(current_value).strip()
                parts = line.split(":", 1)
                current_key = parts[0].strip()
                current_value = [parts[1].strip()] if len(parts) > 1 else []
            else:
                current_value.append(line)

        if current_key and current_value:
            data[current_key] = "\n".join(current_value).strip()

        return AgentDefinition(
            role=data.get("role", f"{role}_agent"),
            goal=data.get("goal", ""),
            backstory=data.get("backstory", ""),
            verbose=data.get("verbose", "true").lower() == "true",
        )

    def create_crewai_agent(self, definition: AgentDefinition, llm: Any) -> Any:
        """Create a CrewAI Agent from the definition.

        Args:
            definition: The agent definition.
            llm: The LLM instance to use.

        Returns:
            CrewAI Agent instance.
        """
        try:
            from crewai import Agent
            return Agent(
                role=definition.role,
                goal=definition.goal,
                backstory=definition.backstory,
                verbose=definition.verbose,
                llm=llm,
            )
        except ImportError:
            raise RuntimeError("CrewAI not installed. Please install it first.")
