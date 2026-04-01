from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

try:
    from crewai import Agent, Crew, LLM, Process, Task

    CREWAI_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    Agent = Crew = LLM = Process = Task = None
    CREWAI_IMPORT_ERROR = exc


DEFAULT_TOPIC = "大模型在食品安全领域的研究进展与应用综述"
DEFAULT_WOS_API_BASE = "https://api.clarivate.com/apis/wos-starter/v1/documents"
DEFAULT_MODEL_NAME = "openai/qwen3.5-plus"
DEFAULT_MODEL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_OUTPUT = Path("outputs/final_review.md")
CURRENT_YEAR = datetime.now().year


@dataclass
class RunConfig:
    topic: str
    word_count_min: int
    word_count_max: int
    target_refs: int
    year_window: int
    review_rounds_min: int
    review_rounds_max: int
    output_path: Path
    output_dir: Path
    model_name: str
    model_base_url: str
    wos_api_base: str
    request_timeout: int = 30
    retrieval_pool_size: int = 70
    minimum_acceptable_refs: int = 35
    old_paper_ratio_limit: float = 0.15


@dataclass
class PaperRecord:
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


@dataclass
class EvidenceNote:
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
    title: str
    goal: str
    target_words: int
    key_questions: List[str] = field(default_factory=list)
    must_cover: List[str] = field(default_factory=list)
    planned_tables: List[str] = field(default_factory=list)


@dataclass
class ReviewIssue:
    severity: str
    category: str
    description: str
    affected_section: str
    action: str


@dataclass
class ReviewReport:
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
        if not self.scorecard:
            return 0.0
        return round(sum(self.scorecard.values()) / len(self.scorecard) * 10, 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成面向中文顶级期刊风格的食品安全大模型综述工作流。"
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--word-count-min", type=int, default=4000)
    parser.add_argument("--word-count-max", type=int, default=6000)
    parser.add_argument("--target-refs", type=int, default=40)
    parser.add_argument("--year-window", type=int, default=5)
    parser.add_argument("--review-rounds-min", type=int, default=2)
    parser.add_argument("--review-rounds-max", type=int, default=3)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def load_config(args: argparse.Namespace) -> RunConfig:
    output_path = Path(args.output)
    return RunConfig(
        topic=args.topic,
        word_count_min=args.word_count_min,
        word_count_max=args.word_count_max,
        target_refs=args.target_refs,
        year_window=args.year_window,
        review_rounds_min=args.review_rounds_min,
        review_rounds_max=args.review_rounds_max,
        output_path=output_path,
        output_dir=output_path.parent,
        model_name=os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME),
        model_base_url=os.getenv("MODEL_BASE_URL", DEFAULT_MODEL_BASE_URL),
        wos_api_base=os.getenv("WOS_API_BASE", DEFAULT_WOS_API_BASE),
    )


def ensure_runtime_ready() -> None:
    if CREWAI_IMPORT_ERROR is not None:
        raise RuntimeError(
            "未检测到 crewai。请先安装依赖，例如 `pip install -r requirements.txt`。"
        ) from CREWAI_IMPORT_ERROR

    required_envs = ["DASHSCOPE_API_KEY", "WOS_API_KEY"]
    missing = [name for name in required_envs if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"缺少环境变量：{', '.join(missing)}")


def ensure_output_dirs(config: RunConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)


def build_llm(config: RunConfig) -> LLM:
    return LLM(
        model=config.model_name,
        base_url=config.model_base_url,
        api_key=os.environ["DASHSCOPE_API_KEY"],
    )


def build_tools(config: RunConfig) -> Dict[str, Any]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "crew-ai-food-safety-review/1.0",
            "Accept": "application/json",
        }
    )
    return {
        "session": session,
        "timeout": config.request_timeout,
        "wos_base": config.wos_api_base.rstrip("/"),
        "wos_headers": {"X-ApiKey": os.environ["WOS_API_KEY"]},
    }


def build_agents(llm: LLM) -> Dict[str, Agent]:
    return {
        "planner_agent": Agent(
            role="综述选题与框架总策划",
            goal="规划一篇符合中文顶级期刊要求的食品安全领域大模型综述，输出严谨的提纲、检索词和质量目标。",
            backstory="你擅长把宽泛研究主题拆解为可以执行的系统综述路线，尤其重视结构完整、综述深度与期刊口径。",
            verbose=True,
            llm=llm,
        ),
        "retrieval_agent": Agent(
            role="学术检索策略专家",
            goal="把主题转化为适配 WOS 与开放官方学术源的检索式，确保文献覆盖完整且与食品安全高度相关。",
            backstory="你熟悉 WOS、Crossref、PubMed、OpenAlex 的检索逻辑，善于设计布尔检索式与主题词扩展。",
            verbose=True,
            llm=llm,
        ),
        "screening_agent": Agent(
            role="高水平文献筛选审稿人",
            goal="从候选文献中筛出近期、高质量、主题相关且结构均衡的核心文献池。",
            backstory="你长期参与综述审稿，对来源质量、新近性、分区和主题覆盖度要求都很严格。",
            verbose=True,
            llm=llm,
        ),
        "analysis_agent": Agent(
            role="文献综合分析研究员",
            goal="提炼每篇文献的研究问题、核心观点、新方法、证据与局限，并形成跨文献比较。",
            backstory="你不做平铺直叙式摘要，而是擅长抓取共性、差异、争议和研究空白。",
            verbose=True,
            llm=llm,
        ),
        "writer_agent": Agent(
            role="中文顶刊综述主笔",
            goal="基于证据库撰写一篇 4000-6000 字的高质量中文综述，确保批判性、结构性和可追溯引用。",
            backstory="你熟悉中文顶级期刊综述文风，强调段落归纳、方法比较、问题导向和未来方向。",
            verbose=True,
            llm=llm,
        ),
        "reviewer_agent": Agent(
            role="严苛终审专家",
            goal="对综述进行苛刻审查，必须指出弱点、证据不足与结构问题，并推动多轮修订。",
            backstory="你对综述稿件要求近乎苛刻，不会接受泛泛总结、空洞未来展望和无证据支持的判断。",
            verbose=True,
            llm=llm,
        ),
    }


def execute_single_agent(agent: Agent, description: str, expected_output: str) -> str:
    task = Task(description=description, expected_output=expected_output, agent=agent)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
    result = crew.kickoff()
    return crew_output_to_text(result)


def crew_output_to_text(result: Any) -> str:
    if hasattr(result, "raw"):
        return str(result.raw)
    if hasattr(result, "output"):
        return str(result.output)
    return str(result)


def safe_json_loads(raw_text: str) -> Any:
    cleaned = raw_text.strip()
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", cleaned, re.S)
    if fenced_match:
        cleaned = fenced_match.group(1)
    else:
        first_brace = min(
            [idx for idx in [cleaned.find("{"), cleaned.find("[")] if idx >= 0],
            default=-1,
        )
        last_brace = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if first_brace >= 0 and last_brace >= first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]
    return json.loads(cleaned)


def split_revision_payload(raw_text: str) -> Tuple[Dict[str, Any], str]:
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw_text, re.S)
    revision = json.loads(json_match.group(1)) if json_match else {}
    marker = "---REVISED_DRAFT---"
    markdown = raw_text.split(marker, 1)[1].strip() if marker in raw_text else raw_text.strip()
    return revision, markdown


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, dict):
        return {key: to_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(item) for item in obj]
    return obj


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def estimate_word_count(text: str) -> int:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_tokens = len(re.findall(r"[A-Za-z0-9]+", text))
    return chinese_chars + latin_tokens


def chunked(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fallback_plan(config: RunConfig) -> Dict[str, Any]:
    return {
        "review_title": config.topic,
        "scope_statement": "聚焦近 3-5 年大模型在食品安全领域的应用、方法演进、问题和未来方向。",
        "search_queries": [
            {
                "query": "TS=(\"large language model*\" OR LLM OR GPT OR \"foundation model*\") AND TS=(\"food safety\" OR \"food quality\" OR traceability OR compliance OR inspection OR \"food risk\")",
                "intent": "大模型与食品安全总览",
                "priority": 1,
            },
            {
                "query": "TS=(\"large language model*\" OR LLM OR GPT) AND TS=(\"food safety\" AND (regulation OR compliance OR standard* OR label*))",
                "intent": "监管合规与标准问答",
                "priority": 1,
            },
            {
                "query": "TS=(\"large language model*\" OR multimodal OR vision-language) AND TS=(\"food inspection\" OR \"food quality\" OR contamination OR adulteration)",
                "intent": "多模态与检测场景",
                "priority": 2,
            },
            {
                "query": "TS=(agent OR \"retrieval augmented generation\" OR RAG OR \"knowledge graph\") AND TS=(\"food safety\" OR traceability OR recall)",
                "intent": "RAG、知识图谱与 Agent",
                "priority": 2,
            },
        ],
        "inclusion_rules": [
            "优先近 5 年英文论文",
            "优先 JCR Q1/Q2 期刊",
            "必须与食品安全、食品质量、合规、溯源、质检或风险预警相关",
            "优先 article/review",
        ],
        "exclusion_rules": [
            "与食品安全无实质关系的通用大模型论文",
            "没有摘要或元数据严重缺失的记录",
            "明显重复或低可信渠道转载",
        ],
        "sections": [
            {
                "title": "摘要",
                "goal": "概括研究背景、主要脉络、关键结论与未来方向。",
                "target_words": 350,
                "key_questions": ["为什么该主题重要？", "综述的主线和贡献是什么？"],
                "must_cover": ["应用场景", "方法演进", "局限与展望"],
                "planned_tables": [],
            },
            {
                "title": "引言",
                "goal": "说明食品安全数字化治理需求与大模型介入的现实背景。",
                "target_words": 700,
                "key_questions": ["传统方法的瓶颈是什么？", "为何需要大模型能力？"],
                "must_cover": ["行业背景", "研究意义", "综述定位"],
                "planned_tables": [],
            },
            {
                "title": "大模型在食品安全中的主要应用场景",
                "goal": "归纳法规问答、风险预警、标签审核、溯源召回、质检解析等应用。",
                "target_words": 1200,
                "key_questions": ["主要落地场景有哪些？", "不同场景的核心价值和限制是什么？"],
                "must_cover": ["法规合规", "风险预警", "多模态检测", "供应链溯源"],
                "planned_tables": ["表1"],
            },
            {
                "title": "核心方法与技术路线比较",
                "goal": "系统比较微调、RAG、多模态、知识图谱、Agent 等方法路线。",
                "target_words": 1100,
                "key_questions": ["不同技术路线解决什么问题？", "方法之间如何互补？"],
                "must_cover": ["微调", "RAG", "知识图谱", "多模态", "Agent"],
                "planned_tables": ["表2"],
            },
            {
                "title": "代表性研究的共性、差异与争议",
                "goal": "对代表性研究进行分组比较，提炼证据一致性与分歧。",
                "target_words": 800,
                "key_questions": ["前人研究形成了哪些共识？", "有哪些结论分歧？"],
                "must_cover": ["共性", "差异", "争议"],
                "planned_tables": [],
            },
            {
                "title": "现有研究的优势与局限",
                "goal": "批判性总结现有工作的优势、局限和方法边界。",
                "target_words": 700,
                "key_questions": ["现有工作真正的进步是什么？", "哪些问题尚未解决？"],
                "must_cover": ["数据问题", "可靠性", "可解释性", "评测缺失"],
                "planned_tables": [],
            },
            {
                "title": "研究空白与未来切入点",
                "goal": "从证据链推出下一阶段可执行研究方向。",
                "target_words": 700,
                "key_questions": ["真正的研究空白是什么？", "未来切入点如何与食品安全场景结合？"],
                "must_cover": ["研究空白", "方法建议", "场景切入点"],
                "planned_tables": ["表3"],
            },
            {
                "title": "结论",
                "goal": "凝练全文主张和总体判断。",
                "target_words": 300,
                "key_questions": ["最终结论是什么？"],
                "must_cover": ["总体评价", "研究趋势"],
                "planned_tables": [],
            },
        ],
        "table_plan": [
            {
                "table_id": "表1",
                "title": "代表性文献总览",
                "columns": ["作者", "年份", "期刊", "研究对象", "方法", "主要结论"],
            },
            {
                "table_id": "表2",
                "title": "核心方法比较",
                "columns": ["方法路线", "输入数据", "优势", "局限", "适用场景"],
            },
            {
                "table_id": "表3",
                "title": "研究空白与未来方向",
                "columns": ["研究空白", "潜在改进路径", "适用食品安全环节"],
            },
        ],
        "quality_targets": {
            "target_refs": config.target_refs,
            "minimum_acceptable_refs": config.minimum_acceptable_refs,
            "word_count_min": config.word_count_min,
            "word_count_max": config.word_count_max,
            "year_window": config.year_window,
        },
    }


def render_outline(plan: Dict[str, Any]) -> str:
    lines = [f"# {plan['review_title']}", "", "## 范围说明", plan["scope_statement"], ""]
    lines.append("## 结构提纲")
    for section in plan["sections"]:
        lines.append(f"### {section['title']}")
        lines.append(f"- 目标：{section['goal']}")
        lines.append(f"- 目标字数：{section['target_words']}")
        lines.append(f"- 核心问题：{'；'.join(section['key_questions'])}")
        lines.append(f"- 必须覆盖：{'；'.join(section['must_cover'])}")
        if section["planned_tables"]:
            lines.append(f"- 关联表格：{'、'.join(section['planned_tables'])}")
        lines.append("")
    lines.append("## 表格设计")
    for table in plan["table_plan"]:
        lines.append(f"- {table['table_id']}：{table['title']}（{'、'.join(table['columns'])}）")
    return "\n".join(lines).strip() + "\n"


def plan_review(config: RunConfig, agents: Dict[str, Agent]) -> Dict[str, Any]:
    prompt = textwrap.dedent(
        f"""
        请围绕主题“{config.topic}”生成一个严格的系统综述执行计划。
        约束如下：
        1. 最终文章为中文，字数 {config.word_count_min}-{config.word_count_max}。
        2. 参考文献目标 {config.target_refs} 篇，英文文献优先，近 {config.year_window} 年优先。
        3. 文献来源以 WOS 为主，必要时补充 Crossref、PubMed、OpenAlex。
        4. 期刊质量优先 JCR Q1/Q2。
        5. 内容必须体现共性、差异、争议、优势、局限、研究空白与未来方向。
        6. 至少规划 3 个表格。

        请严格输出 JSON，对应字段：
        {{
          "review_title": str,
          "scope_statement": str,
          "search_queries": [{{"query": str, "intent": str, "priority": int}}],
          "inclusion_rules": [str],
          "exclusion_rules": [str],
          "sections": [
            {{
              "title": str,
              "goal": str,
              "target_words": int,
              "key_questions": [str],
              "must_cover": [str],
              "planned_tables": [str]
            }}
          ],
          "table_plan": [{{"table_id": str, "title": str, "columns": [str]}}],
          "quality_targets": {{
            "target_refs": int,
            "minimum_acceptable_refs": int,
            "word_count_min": int,
            "word_count_max": int,
            "year_window": int
          }}
        }}
        """
    )
    try:
        raw = execute_single_agent(
            agents["planner_agent"],
            prompt,
            "结构化 JSON 综述执行计划。",
        )
        plan = safe_json_loads(raw)
    except Exception:
        plan = fallback_plan(config)

    write_json(config.output_dir / "search_plan.json", plan)
    write_text(config.output_dir / "outline.md", render_outline(plan))
    return plan


def request_json(
    session: requests.Session,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Any:
    response = session.get(url, headers=headers, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def extract_authors(raw: Any) -> List[str]:
    authors: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("display_name") or item.get("full_name") or item.get("name")
                if name:
                    authors.append(str(name))
            elif isinstance(item, str):
                authors.append(item)
    elif isinstance(raw, dict):
        for key in ("authors", "author", "names"):
            if key in raw:
                authors.extend(extract_authors(raw[key]))
    return authors[:10]


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_quartile(value: Any) -> str:
    text = str(value or "").upper().strip()
    for candidate in ("Q1", "Q2", "Q3", "Q4"):
        if candidate in text:
            return candidate
    return "Unknown"


def parse_wos_item(item: Dict[str, Any], config: RunConfig) -> PaperRecord:
    identifiers = item.get("identifiers", {}) if isinstance(item.get("identifiers"), dict) else {}
    source = item.get("source", {}) if isinstance(item.get("source"), dict) else {}
    title = (
        item.get("title")
        or item.get("titles", {}).get("title")
        or item.get("title_summary", {}).get("title")
        or ""
    )
    authors = extract_authors(item.get("names") or item.get("authors") or item.get("author"))
    year = coerce_int(
        item.get("published")
        or item.get("publishYear")
        or item.get("source", {}).get("publishYear")
        or item.get("year")
    )
    doi = identifiers.get("doi") or item.get("doi") or ""
    url = (
        item.get("links", {}).get("record")
        if isinstance(item.get("links"), dict)
        else item.get("url") or ""
    )
    journal = source.get("sourceTitle") or source.get("title") or item.get("journal") or ""
    abstract = item.get("abstract") or item.get("abstract_text") or ""
    keywords = item.get("keywords") or source.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keyword.strip() for keyword in keywords.split(";") if keyword.strip()]
    quartile = normalize_quartile(
        item.get("jcr_quartile")
        or item.get("jcrQuartile")
        or source.get("jcr_quartile")
        or source.get("quartile")
    )
    return PaperRecord(
        ref_id=str(item.get("uid") or f"WOS-{normalize_title(title)[:12]}"),
        title=title,
        authors=authors,
        year=year or None,
        journal=journal,
        doi=doi,
        url=url,
        abstract=abstract,
        keywords=list(keywords),
        source_db="WOS",
        jcr_quartile=quartile,
        document_type=str(item.get("document_type") or item.get("doctype") or "article"),
        times_cited=coerce_int(item.get("timesCited") or item.get("citations")),
        language=str(item.get("language") or "English"),
        is_recent=bool(year and year >= CURRENT_YEAR - config.year_window + 1),
        is_high_tier=quartile in {"Q1", "Q2"},
    )


def parse_crossref_item(item: Dict[str, Any], config: RunConfig) -> PaperRecord:
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
        is_recent=bool(year and year >= CURRENT_YEAR - config.year_window + 1),
        is_high_tier=False,
    )


def parse_openalex_item(item: Dict[str, Any], config: RunConfig) -> PaperRecord:
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
    quartile = normalize_quartile(
        source.get("x_indexed_in")
        or source.get("quartile")
        or item.get("jcr_quartile")
    )
    return PaperRecord(
        ref_id=f"OA-{item.get('id', '').split('/')[-1] or normalize_title(item.get('title', ''))[:12]}",
        title=item.get("title", ""),
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
        is_recent=bool(year and year >= CURRENT_YEAR - config.year_window + 1),
        is_high_tier=quartile in {"Q1", "Q2"},
    )


def parse_pubmed_summary(item: Dict[str, Any], config: RunConfig) -> PaperRecord:
    title = item.get("title", "")
    authors = [author.get("name", "") for author in item.get("authors", []) if author.get("name")]
    year = None
    year_match = re.search(r"(19|20)\d{2}", str(item.get("pubdate", "")))
    if year_match:
        year = int(year_match.group(0))
    doi = ""
    url = ""
    for article_id in item.get("articleids", []):
        if article_id.get("idtype") == "doi":
            doi = article_id.get("value", "")
            url = f"https://doi.org/{doi}"
            break
    return PaperRecord(
        ref_id=f"PM-{item.get('uid', normalize_title(title)[:12])}",
        title=title,
        authors=authors[:10],
        year=year,
        journal=item.get("fulljournalname", ""),
        doi=doi,
        url=url,
        abstract="",
        keywords=[],
        source_db="PubMed",
        jcr_quartile="Unknown",
        document_type=str(item.get("pubtype", ["article"])[0] if item.get("pubtype") else "article"),
        times_cited=0,
        language="English",
        is_recent=bool(year and year >= CURRENT_YEAR - config.year_window + 1),
        is_high_tier=False,
    )


def fetch_wos_records(
    queries: Sequence[Dict[str, Any]],
    tools: Dict[str, Any],
    config: RunConfig,
) -> Tuple[List[PaperRecord], List[str]]:
    records: List[PaperRecord] = []
    notices: List[str] = []
    for query_def in queries:
        try:
            payload = request_json(
                tools["session"],
                tools["wos_base"],
                headers=tools["wos_headers"],
                params={"db": "WOS", "q": query_def["query"], "limit": 20, "page": 1},
                timeout=tools["timeout"],
            )
            raw_hits = payload.get("hits") or payload.get("data") or payload.get("records") or []
            for item in raw_hits:
                try:
                    records.append(parse_wos_item(item, config))
                except Exception:
                    continue
        except Exception as exc:
            notices.append(f"WOS 查询失败：{query_def['intent']} -> {exc}")
    return records, notices


def fetch_crossref_records(
    queries: Sequence[Dict[str, Any]],
    tools: Dict[str, Any],
    config: RunConfig,
) -> List[PaperRecord]:
    records: List[PaperRecord] = []
    since_year = CURRENT_YEAR - config.year_window + 1
    for query_def in queries[:4]:
        try:
            payload = request_json(
                tools["session"],
                "https://api.crossref.org/works",
                params={
                    "query.title": query_def["intent"],
                    "rows": 15,
                    "filter": f"from-pub-date:{since_year}-01-01,type:journal-article",
                    "sort": "published",
                    "order": "desc",
                },
                timeout=tools["timeout"],
            )
            for item in payload.get("message", {}).get("items", []):
                records.append(parse_crossref_item(item, config))
        except Exception:
            continue
    return records


def fetch_openalex_records(
    queries: Sequence[Dict[str, Any]],
    tools: Dict[str, Any],
    config: RunConfig,
) -> List[PaperRecord]:
    records: List[PaperRecord] = []
    since_year = CURRENT_YEAR - config.year_window + 1
    for query_def in queries[:4]:
        try:
            payload = request_json(
                tools["session"],
                "https://api.openalex.org/works",
                params={
                    "search": query_def["intent"],
                    "filter": f"publication_year:>{since_year - 1},type:article",
                    "sort": "publication_year:desc,cited_by_count:desc",
                    "per-page": 15,
                },
                timeout=tools["timeout"],
            )
            for item in payload.get("results", []):
                records.append(parse_openalex_item(item, config))
        except Exception:
            continue
    return records


def fetch_pubmed_records(
    queries: Sequence[Dict[str, Any]],
    tools: Dict[str, Any],
    config: RunConfig,
) -> List[PaperRecord]:
    records: List[PaperRecord] = []
    for query_def in queries[:3]:
        try:
            search_payload = request_json(
                tools["session"],
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": query_def["intent"],
                    "retmode": "json",
                    "retmax": 10,
                    "sort": "pub date",
                },
                timeout=tools["timeout"],
            )
            ids = search_payload.get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            summary_payload = request_json(
                tools["session"],
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
                timeout=tools["timeout"],
            )
            result = summary_payload.get("result", {})
            for uid in ids:
                item = result.get(uid)
                if item:
                    records.append(parse_pubmed_summary(item, config))
        except Exception:
            continue
    return records


def score_paper(record: PaperRecord, config: RunConfig, terms: Sequence[str]) -> float:
    text = f"{record.title} {' '.join(record.keywords)} {record.abstract}".lower()
    relevance_hits = sum(1 for term in terms if term in text)
    recent_bonus = 2.0 if record.is_recent else -1.0
    tier_bonus = 2.5 if record.is_high_tier else 0.5 if record.jcr_quartile == "Unknown" else -0.5
    citation_bonus = min(math.log1p(max(record.times_cited, 0)), 3.0)
    source_bonus = 2.0 if record.source_db == "WOS" else 1.0
    abstract_bonus = 1.0 if record.abstract else 0.0
    type_bonus = 0.6 if "review" in record.document_type.lower() or "article" in record.document_type.lower() else -0.2
    return round(relevance_hits * 0.8 + recent_bonus + tier_bonus + citation_bonus + source_bonus + abstract_bonus + type_bonus, 3)


def choose_better_record(left: PaperRecord, right: PaperRecord) -> PaperRecord:
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
    deduped: Dict[str, PaperRecord] = {}
    for record in records:
        key = record.doi.lower().strip() if record.doi else normalize_title(record.title)
        existing = deduped.get(key)
        deduped[key] = choose_better_record(existing, record) if existing else record
    result = list(deduped.values())
    for index, record in enumerate(result, start=1):
        record.ref_id = f"REF{index:03d}"
    return result


def topic_terms(_: RunConfig) -> List[str]:
    return [
        "food safety",
        "food quality",
        "food inspection",
        "food traceability",
        "food regulation",
        "food risk",
        "foodborne",
        "label",
        "recall",
        "adulteration",
        "contamination",
        "llm",
        "large language model",
        "retrieval augmented generation",
        "knowledge graph",
        "agent",
        "multimodal",
    ]


def retrieve_papers(
    config: RunConfig,
    plan: Dict[str, Any],
    agents: Dict[str, Agent],
    tools: Dict[str, Any],
) -> Dict[str, Any]:
    prompt = textwrap.dedent(
        f"""
        请基于以下综述主题与检索计划，生成更适合 WOS 的高质量检索式建议。

        主题：{config.topic}
        当前计划：
        {json.dumps(plan['search_queries'], ensure_ascii=False, indent=2)}

        输出 JSON：
        {{
          "queries": [{{"query": str, "intent": str, "priority": int}}],
          "screening_focus": [str],
          "source_notes": [str]
        }}
        """
    )
    try:
        retrieval_strategy = safe_json_loads(
            execute_single_agent(agents["retrieval_agent"], prompt, "检索策略 JSON。")
        )
        queries = retrieval_strategy.get("queries") or plan["search_queries"]
    except Exception:
        retrieval_strategy = {"queries": plan["search_queries"], "screening_focus": [], "source_notes": []}
        queries = plan["search_queries"]

    wos_records, notices = fetch_wos_records(queries, tools, config)
    fallback_records: List[PaperRecord] = []
    if not wos_records:
        notices.append("WOS 未返回结果，已自动启用开放学术源补充。")
    if len(wos_records) < config.target_refs:
        fallback_records.extend(fetch_openalex_records(queries, tools, config))
        fallback_records.extend(fetch_crossref_records(queries, tools, config))
        fallback_records.extend(fetch_pubmed_records(queries, tools, config))

    merged = deduplicate_records(wos_records + fallback_records)
    terms = topic_terms(config)
    for record in merged:
        record.relevance_score = score_paper(record, config, terms)
    merged.sort(key=lambda item: item.relevance_score, reverse=True)

    raw_payload = {
        "retrieval_strategy": retrieval_strategy,
        "notices": notices,
        "records": [to_jsonable(record) for record in merged],
        "source_stats": {
            "WOS": len(wos_records),
            "fallback": len(fallback_records),
            "deduplicated_total": len(merged),
        },
    }
    write_json(config.output_dir / "raw_wos_results.json", raw_payload)
    return raw_payload


def compact_paper_summary(records: Sequence[PaperRecord], limit: int = 60) -> str:
    lines = []
    for record in records[:limit]:
        lines.append(
            f"{record.ref_id} | {record.year} | {record.journal} | {record.jcr_quartile} | "
            f"{record.title} | 关键词: {', '.join(record.keywords[:5])} | 摘要: {record.abstract[:220]}"
        )
    return "\n".join(lines)


def screen_and_rank_papers(
    config: RunConfig,
    raw_payload: Dict[str, Any],
    agents: Dict[str, Agent],
) -> List[PaperRecord]:
    all_records = [PaperRecord(**item) for item in raw_payload["records"]]
    cutoff_year = CURRENT_YEAR - config.year_window + 1
    prelim = [
        record
        for record in all_records
        if (
            (record.year and record.year >= cutoff_year)
            or record.relevance_score >= 5.0
            or record.times_cited >= 50
        )
    ]
    prelim.sort(key=lambda item: item.relevance_score, reverse=True)
    candidate_pool = prelim[: config.retrieval_pool_size]
    prompt = textwrap.dedent(
        f"""
        你现在是文献筛选审稿人。请从候选文献中筛出一组高质量核心文献池。

        约束：
        1. 目标保留约 {config.target_refs} 篇核心文献。
        2. 英文文献优先。
        3. 优先近 {config.year_window} 年。
        4. 优先 JCR Q1/Q2。
        5. 主题覆盖要兼顾：法规合规、风险预警、多模态检测、溯源召回、RAG、微调、知识图谱、Agent。
        6. 允许少量经典文献，但比例不应超过 {int(config.old_paper_ratio_limit * 100)}%。

        候选列表：
        {compact_paper_summary(candidate_pool)}

        请输出 JSON：
        {{
          "selected_ref_ids": [str],
          "backup_ref_ids": [str],
          "coverage_assessment": [str],
          "selection_rationale": [str],
          "risk_notes": [str]
        }}
        """
    )
    try:
        screening_result = safe_json_loads(
            execute_single_agent(agents["screening_agent"], prompt, "筛选结果 JSON。")
        )
    except Exception:
        screening_result = {
            "selected_ref_ids": [record.ref_id for record in candidate_pool[: config.target_refs]],
            "backup_ref_ids": [record.ref_id for record in candidate_pool[config.target_refs : config.target_refs + 10]],
            "coverage_assessment": [],
            "selection_rationale": ["回退到基于规则的排序结果。"],
            "risk_notes": [],
        }

    record_map = {record.ref_id: record for record in candidate_pool}
    selected = [record_map[ref_id] for ref_id in screening_result["selected_ref_ids"] if ref_id in record_map]
    if len(selected) < config.minimum_acceptable_refs:
        selected = candidate_pool[: max(config.target_refs, config.minimum_acceptable_refs)]

    write_json(
        config.output_dir / "screened_papers.json",
        {
            "screening_result": screening_result,
            "selected_records": [to_jsonable(record) for record in selected],
            "candidate_count": len(candidate_pool),
        },
    )
    return selected


def evidence_note_from_dict(item: Dict[str, Any]) -> EvidenceNote:
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


def extract_evidence_notes(
    config: RunConfig,
    selected_records: Sequence[PaperRecord],
    plan: Dict[str, Any],
    agents: Dict[str, Agent],
) -> Dict[str, Any]:
    notes: List[EvidenceNote] = []
    batch_outputs: List[Dict[str, Any]] = []
    for batch in chunked(list(selected_records), 8):
        prompt = textwrap.dedent(
            f"""
            请对以下文献批次做深度分析，不要逐篇流水账概述，而要尽量提炼其研究问题、核心观点、新方法、证据和局限。

            综述主题：{config.topic}
            当前综述结构：{json.dumps([section['title'] for section in plan['sections']], ensure_ascii=False)}

            文献批次：
            {compact_paper_summary(batch, limit=20)}

            输出 JSON：
            {{
              "notes": [
                {{
                  "ref_id": str,
                  "section_hint": str,
                  "research_problem": str,
                  "core_viewpoint": str,
                  "new_method": str,
                  "data_or_experiment": str,
                  "main_conclusion": str,
                  "strengths": str,
                  "limitations": str,
                  "theme_tags": [str]
                }}
              ],
              "batch_insights": [str]
            }}
            """
        )
        try:
            batch_result = safe_json_loads(
                execute_single_agent(agents["analysis_agent"], prompt, "文献批次分析 JSON。")
            )
        except Exception:
            batch_result = {
                "notes": [
                    {
                        "ref_id": record.ref_id,
                        "section_hint": "核心方法与技术路线比较",
                        "research_problem": f"围绕 {record.title} 对应场景的建模与应用问题。",
                        "core_viewpoint": record.abstract[:120] or record.title,
                        "new_method": "需人工补充",
                        "data_or_experiment": "需人工补充",
                        "main_conclusion": record.abstract[:120] or "需人工补充",
                        "strengths": "自动回退生成，待后续细化",
                        "limitations": "自动回退生成，待后续细化",
                        "theme_tags": record.keywords[:5],
                    }
                    for record in batch
                ],
                "batch_insights": ["批次分析回退到规则摘要。"],
            }
        batch_outputs.append(batch_result)
        notes.extend(evidence_note_from_dict(item) for item in batch_result["notes"])

    synthesis_prompt = textwrap.dedent(
        f"""
        请基于以下证据笔记，做跨文献综合分析，突出共性、差异、争议、研究空白和未来方向。

        证据笔记：
        {json.dumps([to_jsonable(note) for note in notes], ensure_ascii=False, indent=2)}

        输出 JSON：
        {{
          "common_themes": [str],
          "differences": [str],
          "controversies": [str],
          "research_gaps": [str],
          "future_directions": [str],
          "table_seeds": {{
            "表1": [str],
            "表2": [str],
            "表3": [str]
          }}
        }}
        """
    )
    try:
        synthesis = safe_json_loads(
            execute_single_agent(agents["analysis_agent"], synthesis_prompt, "跨文献综合分析 JSON。")
        )
    except Exception:
        synthesis = {
            "common_themes": ["现有研究普遍强调食品安全知识增强和流程自动化。"],
            "differences": ["不同研究在数据来源、评测方式和应用场景上差异显著。"],
            "controversies": ["大模型在高风险决策中的可靠性与可解释性仍存在争议。"],
            "research_gaps": ["缺少统一评测基准、真实场景数据集和长期部署证据。"],
            "future_directions": ["加强 RAG、多模态、知识图谱与 Agent 协同，以及人机协同闭环。"],
            "table_seeds": {"表1": [], "表2": [], "表3": []},
        }

    evidence_bank = {
        "selected_records": [to_jsonable(record) for record in selected_records],
        "evidence_notes": [to_jsonable(note) for note in notes],
        "batch_outputs": batch_outputs,
        "synthesis": synthesis,
    }
    write_json(config.output_dir / "evidence_bank.json", evidence_bank)
    return evidence_bank


def serialise_records_for_prompt(records: Sequence[PaperRecord]) -> str:
    lines = []
    for record in records:
        lines.append(
            f"{record.ref_id} | {record.title} | {record.year} | {record.journal} | {record.jcr_quartile} | "
            f"DOI={record.doi or 'N/A'} | URL={record.url or 'N/A'}"
        )
    return "\n".join(lines)


def write_review_draft(
    config: RunConfig,
    plan: Dict[str, Any],
    evidence_bank: Dict[str, Any],
    agents: Dict[str, Agent],
    round_label: str = "v1",
) -> str:
    prompt = textwrap.dedent(
        f"""
        请写一篇中文顶级期刊风格的综述稿件，主题为“{config.topic}”。

        刚性要求：
        1. 字数控制在 {config.word_count_min}-{config.word_count_max}。
        2. 不允许逐篇摘要式堆砌，必须按主题归纳、比较、批判分析。
        3. 每个主要章节都要体现共性、差异、优缺点或争议点。
        4. 未来方向必须由前文证据推出，不能空泛。
        5. 请在正文中仅使用内部引用标记 `[@REFxxx]`，例如 `[@REF001]`。不要自己转成数字编号。
        6. 请包含至少 3 个 Markdown 表格，分别对应表1、表2、表3。
        7. 请保留“## 参考文献”标题，但不要手工写参考文献条目，留空即可。

        综述计划：
        {json.dumps(plan, ensure_ascii=False, indent=2)}

        已筛选核心文献：
        {serialise_records_for_prompt([PaperRecord(**item) for item in evidence_bank['selected_records']])}

        证据笔记：
        {json.dumps(evidence_bank['evidence_notes'], ensure_ascii=False, indent=2)}

        跨文献综合分析：
        {json.dumps(evidence_bank['synthesis'], ensure_ascii=False, indent=2)}

        请直接输出 Markdown 正文。
        """
    )
    markdown = execute_single_agent(agents["writer_agent"], prompt, "完整 Markdown 综述稿件。")
    write_text(config.output_dir / f"draft_{round_label}.md", markdown)
    return markdown


def review_issue_from_dict(item: Dict[str, Any], severity: str) -> ReviewIssue:
    return ReviewIssue(
        severity=severity,
        category=item.get("category", ""),
        description=item.get("description", ""),
        affected_section=item.get("affected_section", ""),
        action=item.get("action", ""),
    )


def parse_review_report(round_index: int, payload: Dict[str, Any]) -> ReviewReport:
    return ReviewReport(
        round_index=round_index,
        scorecard=payload.get("scorecard", {}),
        blocking_issues=[review_issue_from_dict(item, "blocking") for item in payload.get("blocking_issues", [])],
        major_issues=[review_issue_from_dict(item, "major") for item in payload.get("major_issues", [])],
        minor_issues=[review_issue_from_dict(item, "minor") for item in payload.get("minor_issues", [])],
        missing_topics=payload.get("missing_topics", []),
        weak_tables=payload.get("weak_tables", []),
        unsupported_claims=payload.get("unsupported_claims", []),
        revision_instructions=payload.get("revision_instructions", []),
        decision=payload.get("decision", "revise"),
    )


def review_draft(
    config: RunConfig,
    agents: Dict[str, Agent],
    draft_markdown: str,
    evidence_bank: Dict[str, Any],
    round_index: int,
) -> ReviewReport:
    prompt = textwrap.dedent(
        f"""
        请以中文顶刊综述终审专家身份，严格审查以下稿件。你的任务是挑错，不要泛泛表扬。

        审稿标准：
        1. 文献覆盖度
        2. 新近性与刊源质量
        3. 归纳与比较深度
        4. 批判性分析质量
        5. 研究空白与未来方向质量
        6. 引用与格式规范性

        稿件如下：
        {draft_markdown[:16000]}

        证据库摘要：
        {json.dumps(evidence_bank['synthesis'], ensure_ascii=False, indent=2)}

        严格输出 JSON：
        {{
          "scorecard": {{
            "文献覆盖度": float,
            "新近性与刊源质量": float,
            "归纳与比较深度": float,
            "批判性分析质量": float,
            "研究空白与未来方向质量": float,
            "引用与格式规范性": float
          }},
          "blocking_issues": [{{"category": str, "description": str, "affected_section": str, "action": str}}],
          "major_issues": [{{"category": str, "description": str, "affected_section": str, "action": str}}],
          "minor_issues": [{{"category": str, "description": str, "affected_section": str, "action": str}}],
          "missing_topics": [str],
          "weak_tables": [str],
          "unsupported_claims": [str],
          "revision_instructions": [str],
          "decision": "accept" | "revise"
        }}
        """
    )
    payload = safe_json_loads(execute_single_agent(agents["reviewer_agent"], prompt, "审稿报告 JSON。"))
    report = parse_review_report(round_index, payload)
    write_json(config.output_dir / f"review_round_{round_index}.json", to_jsonable(report))
    return report


def revise_draft(
    config: RunConfig,
    agents: Dict[str, Agent],
    current_draft: str,
    review_report: ReviewReport,
    plan: Dict[str, Any],
    evidence_bank: Dict[str, Any],
    round_index: int,
) -> Tuple[Dict[str, Any], str]:
    prompt = textwrap.dedent(
        f"""
        请根据审稿意见严格修订综述，不允许忽略问题。修订后请给出逐条响应。

        当前稿件：
        {current_draft[:16000]}

        审稿意见：
        {json.dumps(to_jsonable(review_report), ensure_ascii=False, indent=2)}

        综述计划：
        {json.dumps(plan, ensure_ascii=False, indent=2)}

        证据库：
        {json.dumps(evidence_bank, ensure_ascii=False, indent=2)}

        输出格式必须严格如下：
        ```json
        {{
          "round": {round_index},
          "responses": [
            {{
              "issue": str,
              "resolution": str,
              "affected_section": str
            }}
          ]
        }}
        ```
        ---REVISED_DRAFT---
        <修订后的完整 Markdown 稿件>
        """
    )
    revision_response, revised_markdown = split_revision_payload(
        execute_single_agent(agents["writer_agent"], prompt, "修订响应 JSON + 修订后的 Markdown。")
    )
    write_json(config.output_dir / f"revision_response_round_{round_index}.json", revision_response)
    write_text(config.output_dir / f"draft_v{round_index}.md", revised_markdown)
    return revision_response, revised_markdown


def run_review_loop(
    config: RunConfig,
    plan: Dict[str, Any],
    evidence_bank: Dict[str, Any],
    agents: Dict[str, Agent],
) -> Tuple[str, List[ReviewReport]]:
    current_draft = write_review_draft(config, plan, evidence_bank, agents, round_label="v1")
    reports: List[ReviewReport] = []
    current_round = 1
    while current_round <= config.review_rounds_max:
        report = review_draft(config, agents, current_draft, evidence_bank, current_round)
        reports.append(report)
        must_continue = (
            current_round < config.review_rounds_min
            or report.has_blocking()
            or report.min_dimension_score() < 8.0
            or report.total_score() < 90.0
        )
        if not must_continue or current_round >= config.review_rounds_max:
            break
        _, current_draft = revise_draft(
            config,
            agents,
            current_draft,
            report,
            plan,
            evidence_bank,
            current_round + 1,
        )
        current_round += 1
    return current_draft, reports


def format_reference(record: PaperRecord) -> str:
    authors = ", ".join(record.authors[:4]) if record.authors else "Unknown"
    year = record.year or "n.d."
    source_note = record.jcr_quartile if record.jcr_quartile != "Unknown" else record.source_db
    doi_part = f" DOI: {record.doi}." if record.doi else ""
    url_part = f" {record.url}" if record.url else ""
    return f"{authors}. {record.title}[J]. {record.journal}, {year}. {source_note}.{doi_part}{url_part}".strip()


def validate_citations(
    config: RunConfig,
    markdown: str,
    selected_records: Sequence[PaperRecord],
) -> Dict[str, Any]:
    ref_map = {record.ref_id: record for record in selected_records}
    cited_ids = re.findall(r"\[@(REF\d{3})\]", markdown)
    unknown_ids = sorted({ref_id for ref_id in cited_ids if ref_id not in ref_map})
    unique_ids = []
    seen = set()
    for ref_id in cited_ids:
        if ref_id not in seen:
            unique_ids.append(ref_id)
            seen.add(ref_id)
    table_line_count = len(re.findall(r"(^|\n)\|.+\|", markdown))
    word_count = estimate_word_count(markdown)
    validation = {
        "word_count": word_count,
        "unique_citation_count": len(unique_ids),
        "unknown_citation_ids": unknown_ids,
        "references_section_present": "## 参考文献" in markdown,
        "has_minimum_refs": len(unique_ids) >= config.minimum_acceptable_refs,
        "within_word_range": config.word_count_min <= word_count <= config.word_count_max,
        "table_block_detected": table_line_count >= 9,
        "passes": (
            not unknown_ids
            and len(unique_ids) >= config.minimum_acceptable_refs
            and config.word_count_min <= word_count <= config.word_count_max
            and "## 参考文献" in markdown
            and table_line_count >= 9
        ),
    }
    return validation


def replace_internal_citations(markdown: str, ordered_ids: Sequence[str]) -> str:
    ordered_lookup = {ref_id: index for index, ref_id in enumerate(ordered_ids, start=1)}

    def replacer(match: re.Match[str]) -> str:
        ref_id = match.group(1)
        return f"[{ordered_lookup[ref_id]}]" if ref_id in ordered_lookup else f"[{ref_id}]"

    return re.sub(r"\[@(REF\d{3})\]", replacer, markdown)


def strip_existing_references_section(markdown: str) -> str:
    match = re.search(r"\n## 参考文献\s*.*$", markdown, re.S)
    if match:
        return markdown[: match.start()].rstrip() + "\n"
    return markdown.rstrip() + "\n"


def finalize_review(
    config: RunConfig,
    final_draft: str,
    selected_records: Sequence[PaperRecord],
) -> Tuple[str, Dict[str, Any]]:
    validation = validate_citations(config, final_draft, selected_records)
    ordered_ids: List[str] = []
    seen = set()
    for ref_id in re.findall(r"\[@(REF\d{3})\]", final_draft):
        if ref_id not in seen:
            ordered_ids.append(ref_id)
            seen.add(ref_id)

    final_body = replace_internal_citations(strip_existing_references_section(final_draft), ordered_ids)
    record_map = {record.ref_id: record for record in selected_records}
    references = [
        f"{index}. {format_reference(record_map[ref_id])}"
        for index, ref_id in enumerate(ordered_ids, start=1)
        if ref_id in record_map
    ]
    final_markdown = final_body.rstrip() + "\n\n## 参考文献\n\n" + "\n".join(references) + "\n"
    write_json(config.output_dir / "validation_report.json", validation)
    write_text(config.output_path, final_markdown)
    return final_markdown, validation


def main() -> None:
    args = parse_args()
    config = load_config(args)
    ensure_output_dirs(config)
    ensure_runtime_ready()

    print("=" * 80)
    print(f"食品安全综述工作流启动：{config.topic}")
    print(f"输出目录：{config.output_dir}")
    print("=" * 80)

    llm = build_llm(config)
    tools = build_tools(config)
    agents = build_agents(llm)

    plan = plan_review(config, agents)
    raw_payload = retrieve_papers(config, plan, agents, tools)
    selected_records = screen_and_rank_papers(config, raw_payload, agents)
    evidence_bank = extract_evidence_notes(config, selected_records, plan, agents)
    final_draft, reports = run_review_loop(config, plan, evidence_bank, agents)
    _, validation = finalize_review(config, final_draft, selected_records)

    write_json(
        config.output_dir / "run_summary.json",
        {
            "topic": config.topic,
            "selected_reference_count": len(selected_records),
            "review_rounds_completed": len(reports),
            "review_scores": [report.scorecard for report in reports],
            "final_total_scores": [report.total_score() for report in reports],
            "validation": validation,
        },
    )

    print("=" * 80)
    print("工作流执行完成。")
    print(f"最终稿件：{config.output_path}")
    print(f"验证结果：{config.output_dir / 'validation_report.json'}")
    if not validation["passes"]:
        print("注意：最终稿件未完全通过所有质量闸门，请重点查看 validation_report.json 与 review_round_*.json。")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
