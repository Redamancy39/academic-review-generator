# Reviews API - review task management endpoints
import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ...config import settings
from ...core.models import RunConfig, TopicAnalysis
from ...services.topic_parser import TopicParser
from ...services.workflow_runner import WorkflowRunner

router = APIRouter(prefix="/reviews", tags=["reviews"])

# In-memory task storage (in production, use a database)
tasks_storage: Dict[str, Dict[str, Any]] = {}


class ReviewCreateRequest(BaseModel):
    """Request model for creating a review task."""
    topic: str
    user_description: str = ""  # 用户对综述的理解和写作期望
    journal_type: str = "中文核心期刊"  # 期刊类型
    language: str = "中文"  # 综述语言
    word_count_min: int = 4000
    word_count_max: int = 6000
    target_refs: int = 40
    retrieval_pool_size: int = 100  # 检索池大小
    year_window: int = 5
    review_rounds_min: int = 2
    review_rounds_max: int = 3
    # 半自动模式配置
    mode: str = "auto"  # "auto" | "semi-auto"
    pause_points: List[str] = []  # ["after_planning", "after_screening", ...]


class SourceProgress(BaseModel):
    """Progress for a single data source."""
    status: str  # pending, running, completed, failed
    count: int = 0
    error: Optional[str] = None


class TopicAnalysisResponse(BaseModel):
    """Response model for topic analysis results."""
    domain: str = ""
    sub_domains: List[str] = []
    keywords: List[str] = []
    search_terms: List[str] = []


class SectionInfo(BaseModel):
    """Information about a review section."""
    title: str
    goal: str
    target_words: int
    key_questions: List[str] = []


class PaperBrief(BaseModel):
    """Brief information about a paper."""
    ref_id: str
    title: str
    year: Optional[int] = None
    journal: str = ""
    jcr_quartile: str = ""
    relevance_score: float = 0.0
    abstract_preview: str = ""  # First 200 chars of abstract


class ReviewTaskResponse(BaseModel):
    """Response model for a review task."""
    task_id: str
    status: str
    topic: str
    created_at: str
    progress: float
    current_stage: str
    message: str
    current_source: Optional[str] = None
    sources_progress: Optional[Dict[str, SourceProgress]] = None
    # 新增：中间过程数据
    topic_analysis: Optional[TopicAnalysisResponse] = None
    plan_sections: Optional[List[SectionInfo]] = None
    retrieved_papers: Optional[List[PaperBrief]] = None
    selected_papers: Optional[List[PaperBrief]] = None
    draft_preview: Optional[str] = None  # 当前稿件预览（前500字）
    # 半自动模式状态
    mode: str = "auto"
    pause_points: List[str] = []
    is_paused: bool = False
    pause_reason: Optional[str] = None  # "after_planning" | "after_screening" | ...
    awaiting_user_action: bool = False


class TokenUsageResponse(BaseModel):
    """Response model for token usage."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    stages: Dict[str, Any] = {}


class ReviewResultResponse(BaseModel):
    """Response model for review results."""
    task_id: str
    status: str
    topic: str
    final_markdown: Optional[str] = None
    validation: Optional[Dict[str, Any]] = None
    token_usage: Optional[TokenUsageResponse] = None
    error: Optional[str] = None


@router.post("/create", response_model=ReviewTaskResponse)
async def create_review(
    request: ReviewCreateRequest,
    background_tasks: BackgroundTasks,
) -> ReviewTaskResponse:
    """Create a new review generation task.

    Args:
        request: The review creation request.
        background_tasks: FastAPI background tasks.

    Returns:
        Created task information.
    """
    if not request.topic or len(request.topic.strip()) < 10:
        raise HTTPException(status_code=400, detail="Topic must be at least 10 characters long.")

    task_id = str(uuid.uuid4())

    # Create task record
    tasks_storage[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "topic": request.topic,
        "created_at": datetime.now().isoformat(),
        "progress": 0.0,
        "current_stage": "init",
        "message": "Task created, waiting to start",
        "request": request.model_dump(),
        "current_source": None,
        "sources_progress": {
            "OpenAlex": {"status": "pending", "count": 0},
            "PubMed": {"status": "pending", "count": 0},
            "Crossref": {"status": "pending", "count": 0},
        },
        # 半自动模式状态
        "mode": request.mode,
        "pause_points": request.pause_points,
        "is_paused": False,
        "pause_reason": None,
        "awaiting_user_action": False,
        "user_feedback": None,
    }

    # Start background task
    background_tasks.add_task(run_review_task, task_id, request)

    return ReviewTaskResponse(
        task_id=task_id,
        status="pending",
        topic=request.topic,
        created_at=tasks_storage[task_id]["created_at"],
        progress=0.0,
        current_stage="init",
        message="Task created successfully",
    )


@router.get("/{task_id}", response_model=ReviewTaskResponse)
async def get_review_status(task_id: str) -> ReviewTaskResponse:
    """Get the status of a review task.

    Args:
        task_id: The task ID.

    Returns:
        Task status information.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]

    # Build sources_progress if available
    sources_progress = None
    if "sources_progress" in task:
        sources_progress = {
            k: SourceProgress(**v) for k, v in task["sources_progress"].items()
        }

    # Build topic_analysis if available
    topic_analysis = None
    if "topic_analysis" in task:
        ta = task["topic_analysis"]
        topic_analysis = TopicAnalysisResponse(
            domain=ta.get("domain", ""),
            sub_domains=ta.get("sub_domains", []),
            keywords=ta.get("keywords", []),
            search_terms=ta.get("search_terms", []),
        )

    # Build plan_sections if available
    plan_sections = None
    if "plan_sections" in task:
        plan_sections = [
            SectionInfo(
                title=s.get("title", ""),
                goal=s.get("goal", ""),
                target_words=s.get("target_words", 0),
                key_questions=s.get("key_questions", []),
            )
            for s in task["plan_sections"]
        ]

    # Build paper lists if available
    retrieved_papers = None
    if "retrieved_papers_preview" in task:
        retrieved_papers = [
            PaperBrief(
                ref_id=p.get("ref_id", ""),
                title=p.get("title", ""),
                year=p.get("year"),
                journal=p.get("journal", ""),
                jcr_quartile=p.get("jcr_quartile", ""),
                relevance_score=p.get("relevance_score", 0.0),
                abstract_preview=p.get("abstract_preview", ""),
            )
            for p in task["retrieved_papers_preview"]
        ]

    selected_papers = None
    if "selected_papers_preview" in task:
        selected_papers = [
            PaperBrief(
                ref_id=p.get("ref_id", ""),
                title=p.get("title", ""),
                year=p.get("year"),
                journal=p.get("journal", ""),
                jcr_quartile=p.get("jcr_quartile", ""),
                relevance_score=p.get("relevance_score", 0.0),
                abstract_preview=p.get("abstract_preview", ""),
            )
            for p in task["selected_papers_preview"]
        ]

    return ReviewTaskResponse(
        task_id=task_id,
        status=task["status"],
        topic=task["topic"],
        created_at=task["created_at"],
        progress=task.get("progress", 0.0),
        current_stage=task.get("current_stage", ""),
        message=task.get("message", ""),
        current_source=task.get("current_source"),
        sources_progress=sources_progress,
        topic_analysis=topic_analysis,
        plan_sections=plan_sections,
        retrieved_papers=retrieved_papers,
        selected_papers=selected_papers,
        draft_preview=task.get("draft_preview"),
        # 半自动模式状态
        mode=task.get("mode", "auto"),
        pause_points=task.get("pause_points", []),
        is_paused=task.get("is_paused", False),
        pause_reason=task.get("pause_reason"),
        awaiting_user_action=task.get("awaiting_user_action", False),
    )


@router.get("/{task_id}/result", response_model=ReviewResultResponse)
async def get_review_result(task_id: str) -> ReviewResultResponse:
    """Get the result of a completed review task.

    Args:
        task_id: The task ID.

    Returns:
        Review results including the final markdown.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]

    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Task is not completed. Current status: {task['status']}")

    return ReviewResultResponse(
        task_id=task_id,
        status=task["status"],
        topic=task["topic"],
        final_markdown=task.get("final_markdown"),
        validation=task.get("validation"),
        token_usage=task.get("token_usage"),
        error=task.get("error"),
    )


@router.get("/", response_model=List[ReviewTaskResponse])
async def list_reviews() -> List[ReviewTaskResponse]:
    """List all review tasks.

    Returns:
        List of all tasks.
    """
    return [
        ReviewTaskResponse(
            task_id=task["task_id"],
            status=task["status"],
            topic=task["topic"],
            created_at=task["created_at"],
            progress=task.get("progress", 0.0),
            current_stage=task.get("current_stage", ""),
            message=task.get("message", ""),
        )
        for task in tasks_storage.values()
    ]


# ==================== 半自动模式 API ====================

class ConfirmRequest(BaseModel):
    """Request model for confirming a paused stage."""
    action: str  # "continue" | "revise"
    modifications: Dict[str, Any] = {}

    # 规划阶段修改
    updated_keywords: List[str] = []
    updated_search_terms: List[str] = []
    updated_sections: List[Dict[str, Any]] = []

    # 筛选阶段修改
    added_paper_ids: List[str] = []
    removed_paper_ids: List[str] = []


class ChatRequest(BaseModel):
    """Request model for chatting with AI about the current stage."""
    message: str


class ChatResponse(BaseModel):
    """Response model for chat with AI."""
    response: str
    suggested_modifications: Dict[str, Any] = {}


class ConfirmResponse(BaseModel):
    """Response model for confirm action."""
    task_id: str
    status: str
    message: str
    is_paused: bool = False
    pause_reason: Optional[str] = None


@router.post("/{task_id}/confirm", response_model=ConfirmResponse)
async def confirm_stage(
    task_id: str,
    request: ConfirmRequest,
    background_tasks: BackgroundTasks,
) -> ConfirmResponse:
    """Confirm or revise the current paused stage.

    Args:
        task_id: The task ID.
        request: The confirmation request.
        background_tasks: FastAPI background tasks.

    Returns:
        Confirmation result.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]

    if not task.get("is_paused"):
        raise HTTPException(status_code=400, detail="Task is not paused.")

    if not task.get("awaiting_user_action"):
        raise HTTPException(status_code=400, detail="Task is not awaiting user action.")

    # 保存用户反馈
    user_feedback = {
        "action": request.action,
        "modifications": request.modifications,
        "updated_keywords": request.updated_keywords,
        "updated_search_terms": request.updated_search_terms,
        "updated_sections": request.updated_sections,
        "added_paper_ids": request.added_paper_ids,
        "removed_paper_ids": request.removed_paper_ids,
    }
    task["user_feedback"] = user_feedback

    if request.action == "continue":
        # 用户确认继续
        task["is_paused"] = False
        task["awaiting_user_action"] = False
        task["message"] = "User confirmed. Continuing workflow..."

        # 恢复后台任务
        background_tasks.add_task(resume_review_task, task_id)

        return ConfirmResponse(
            task_id=task_id,
            status=task["status"],
            message="Stage confirmed. Continuing workflow...",
            is_paused=False,
        )

    elif request.action == "revise":
        # 用户请求修改
        task["message"] = "User requested revisions. Processing..."
        task["awaiting_user_action"] = False

        # 根据暂停原因处理修改
        pause_reason = task.get("pause_reason")
        if pause_reason == "after_planning":
            # 处理规划阶段修改
            if request.updated_keywords or request.updated_search_terms:
                task["pending_keywords_update"] = request.updated_keywords
                task["pending_search_terms_update"] = request.updated_search_terms
            if request.updated_sections:
                task["pending_sections_update"] = request.updated_sections

        elif pause_reason == "after_screening":
            # 处理筛选阶段修改
            if request.added_paper_ids:
                task["pending_add_papers"] = request.added_paper_ids
            if request.removed_paper_ids:
                task["pending_remove_papers"] = request.removed_paper_ids

        # 恢复后台任务
        task["is_paused"] = False
        background_tasks.add_task(resume_review_task, task_id)

        return ConfirmResponse(
            task_id=task_id,
            status=task["status"],
            message="Modifications applied. Continuing workflow...",
            is_paused=False,
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")


@router.post("/{task_id}/chat", response_model=ChatResponse)
async def chat_with_ai(task_id: str, request: ChatRequest) -> ChatResponse:
    """Chat with AI about the current paused stage.

    This allows users to discuss modifications with the AI before confirming.

    Args:
        task_id: The task ID.
        request: The chat request.

    Returns:
        AI response with suggested modifications.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]

    if not task.get("is_paused"):
        raise HTTPException(status_code=400, detail="Task is not paused. Chat is only available when paused.")

    # 构建上下文
    pause_reason = task.get("pause_reason", "")
    context = build_chat_context(task, pause_reason)

    # 调用 LLM 进行对话
    import requests
    try:
        prompt = f"""你是一个学术综述生成助手。用户正在审核综述生成流程的中间结果，需要你的帮助。

## 当前阶段
{pause_reason}

## 当前状态数据
{context}

## 用户的问题/请求
{request.message}

## 你的任务
1. 理解用户的意图
2. 给出专业、有帮助的建议
3. 如果用户想要修改，提供具体的修改建议

请用简洁、专业的语言回复。如果要建议修改，请在回复末尾用 JSON 格式给出建议：
```json
{{
  "suggested_keywords": ["关键词1", "关键词2"],
  "suggested_search_terms": ["检索词1", "检索词2"],
  "suggested_paper_changes": {{
    "add": ["REF001"],
    "remove": ["REF002"]
  }}
}}
```
"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.dashscope_api_key}",
        }
        data = {
            "model": settings.model_name.replace("openai/", ""),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 2000,
        }
        response = requests.post(
            f"{settings.model_base_url}/chat/completions",
            headers=headers,
            json=data,
            timeout=60,
        )

        if response.status_code == 200:
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

            # 尝试提取建议的修改
            suggested_modifications = extract_suggested_modifications(content)

            return ChatResponse(
                response=content,
                suggested_modifications=suggested_modifications,
            )
        else:
            return ChatResponse(
                response=f"AI 服务暂时不可用，请稍后重试。（错误：{response.status_code}）",
                suggested_modifications={},
            )
    except Exception as e:
        return ChatResponse(
            response=f"对话过程中出现错误：{str(e)}",
            suggested_modifications={},
        )


@router.post("/{task_id}/abort")
async def abort_task(task_id: str) -> Dict[str, Any]:
    """Abort a paused task.

    Args:
        task_id: The task ID.

    Returns:
        Abort result.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]

    if task["status"] not in ["running", "paused"]:
        raise HTTPException(status_code=400, detail=f"Cannot abort task with status: {task['status']}")

    task["status"] = "aborted"
    task["is_paused"] = False
    task["awaiting_user_action"] = False
    task["message"] = "Task aborted by user."

    return {
        "task_id": task_id,
        "status": "aborted",
        "message": "Task has been aborted.",
    }


# ==================== 辅助函数 ====================

def build_chat_context(task: Dict[str, Any], pause_reason: str) -> str:
    """Build context string for AI chat."""
    context_parts = []

    if pause_reason == "after_planning":
        # 规划阶段上下文
        if "topic_analysis" in task:
            ta = task["topic_analysis"]
            context_parts.append(f"主题领域：{ta.get('domain', '')}")
            context_parts.append(f"关键词：{', '.join(ta.get('keywords', []))}")
            context_parts.append(f"检索词：{', '.join(ta.get('search_terms', []))}")

        if "plan_sections" in task:
            sections = task["plan_sections"]
            context_parts.append("\n章节结构：")
            for s in sections[:5]:
                context_parts.append(f"  - {s.get('title', '')}（目标 {s.get('target_words', 0)} 字）")

    elif pause_reason == "after_screening":
        # 筛选阶段上下文
        if "total_retrieved" in task:
            context_parts.append(f"检索到文献：{task['total_retrieved']} 篇")
        if "total_selected" in task:
            context_parts.append(f"筛选后文献：{task['total_selected']} 篇")

        if "selected_papers_preview" in task:
            context_parts.append("\n选中的文献（前10篇）：")
            for p in task["selected_papers_preview"][:10]:
                context_parts.append(f"  - {p.get('ref_id', '')}: {p.get('title', '')} ({p.get('year', '')}, {p.get('journal', '')})")

        if "retrieved_papers_preview" in task:
            context_parts.append("\n候选文献（前10篇）：")
            for p in task["retrieved_papers_preview"][:10]:
                context_parts.append(f"  - {p.get('ref_id', '')}: {p.get('title', '')} ({p.get('year', '')})")

    return "\n".join(context_parts)


def extract_suggested_modifications(content: str) -> Dict[str, Any]:
    """Extract suggested modifications from AI response."""
    import re
    import json

    suggestions = {}

    # 尝试提取 JSON 块
    json_match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            suggestions = parsed
        except json.JSONDecodeError:
            pass

    return suggestions


def resume_review_task(task_id: str) -> None:
    """Resume a paused review task.

    Args:
        task_id: The task ID.
    """
    task = tasks_storage[task_id]

    try:
        task["status"] = "running"
        task["message"] = "Resuming workflow..."

        # 从 checkpoint 恢复
        output_dir = settings.output_dir / task_id
        from ...services.checkpoint_manager import CheckpointManager
        from ...services.workflow_runner import WorkflowRunner

        checkpoint_manager = CheckpointManager(output_dir)

        # 获取原始请求
        request_data = task.get("request", {})
        user_feedback = task.get("user_feedback", {})

        # 创建配置
        config = RunConfig(
            topic=request_data.get("topic", ""),
            user_description=request_data.get("user_description", ""),
            journal_type=request_data.get("journal_type", "中文核心期刊"),
            language=request_data.get("language", "中文"),
            word_count_min=request_data.get("word_count_min", 4000),
            word_count_max=request_data.get("word_count_max", 6000),
            target_refs=request_data.get("target_refs", 40),
            retrieval_pool_size=request_data.get("retrieval_pool_size", 100),
            year_window=request_data.get("year_window", 5),
            review_rounds_min=request_data.get("review_rounds_min", 2),
            review_rounds_max=request_data.get("review_rounds_max", 3),
            output_dir=output_dir,
            output_path=output_dir / "final_review.md",
            mode=request_data.get("mode", "auto"),
            pause_points=request_data.get("pause_points", []),
        )

        # 获取 topic_analysis
        topic_analysis_data = task.get("topic_analysis", {})
        from ...core.models import TopicAnalysis
        topic_analysis = TopicAnalysis(
            domain=topic_analysis_data.get("domain", ""),
            sub_domains=topic_analysis_data.get("sub_domains", []),
            keywords=topic_analysis_data.get("keywords", []),
            search_terms=topic_analysis_data.get("search_terms", []),
            suggested_sections=topic_analysis_data.get("suggested_sections", []),
            relevance_hints=topic_analysis_data.get("relevance_hints", []),
        )

        # 进度回调
        def progress_callback(stage: str, message: str, progress: float) -> None:
            task["current_stage"] = stage
            task["message"] = message
            task["progress"] = progress

        def source_progress_callback(source: str, status: str, count: int, error: Optional[str] = None) -> None:
            if "sources_progress" not in task:
                task["sources_progress"] = {}
            task["sources_progress"][source] = {
                "status": status,
                "count": count,
                "error": error,
            }
            task["current_source"] = source if status == "running" else None

        def stage_data_callback(stage: str, data: Dict[str, Any]) -> None:
            # 同之前实现的 stage_data_callback
            if stage == "planning":
                if "sections" in data:
                    task["plan_sections"] = data["sections"]
            elif stage == "retrieval":
                if "records" in data:
                    records = data["records"]
                    task["raw_records"] = records
                    task["retrieved_papers_preview"] = [
                        {
                            "ref_id": r.get("ref_id", ""),
                            "title": r.get("title", ""),
                            "year": r.get("year"),
                            "journal": r.get("journal", ""),
                            "jcr_quartile": r.get("jcr_quartile", ""),
                            "relevance_score": r.get("relevance_score", 0.0),
                            "abstract_preview": (r.get("abstract", "") or "")[:200],
                        }
                        for r in records[:20]
                    ]
                    task["total_retrieved"] = len(records)
            elif stage == "screening":
                if "selected_records" in data:
                    records = data["selected_records"]
                    task["selected_records"] = records
                    task["selected_papers_preview"] = [
                        {
                            "ref_id": r.get("ref_id", ""),
                            "title": r.get("title", ""),
                            "year": r.get("year"),
                            "journal": r.get("journal", ""),
                            "jcr_quartile": r.get("jcr_quartile", ""),
                            "relevance_score": r.get("relevance_score", 0.0),
                            "abstract_preview": (r.get("abstract", "") or "")[:200],
                        }
                        for r in records[:20]
                    ]
                    task["total_selected"] = len(records)
            elif stage == "writing":
                if "draft" in data:
                    task["draft_preview"] = data["draft"][:500] + "..." if len(data["draft"]) > 500 else data["draft"]

        def pause_callback(pause_reason: str, pause_data: Dict[str, Any]) -> None:
            """暂停回调 - 当工作流需要暂停时调用"""
            task["is_paused"] = True
            task["pause_reason"] = pause_reason
            task["awaiting_user_action"] = True
            task["message"] = f"Workflow paused at {pause_reason}. Awaiting user action."
            task.update(pause_data)

        # 创建 runner
        runner = WorkflowRunner(
            config=config,
            topic_analysis=topic_analysis,
            wos_api_key=settings.wos_api_key or None,
            progress_callback=progress_callback,
            source_progress_callback=source_progress_callback,
            stage_data_callback=stage_data_callback,
            pause_callback=pause_callback,
        )

        # 运行
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(runner.run(resume=True))
        finally:
            loop.close()

        # 检查是否被暂停
        if task.get("is_paused"):
            return  # 不更新状态，等待用户操作

        # 完成
        task["status"] = "completed"
        task["progress"] = 1.0
        task["message"] = "Review generation completed successfully"
        task["final_markdown"] = results.get("final_markdown")
        task["validation"] = results.get("validation")
        task["token_usage"] = results.get("token_usage")
        task["raw_records"] = results.get("raw_records")
        task["selected_records"] = results.get("selected_records")

    except Exception as e:
        task["status"] = "failed"
        task["message"] = f"Error: {str(e)}"
        task["error"] = str(e)


class CoverageResponse(BaseModel):
    """Response model for coverage analysis."""
    task_id: str
    coverage_ok: bool
    covered_topics: List[str]
    underrepresented_topics: List[str]
    coverage_counts: Dict[str, int]
    suggested_queries: List[str]


@router.get("/{task_id}/coverage", response_model=CoverageResponse)
async def get_coverage_analysis(task_id: str) -> CoverageResponse:
    """Get topic coverage analysis for a task.

    Args:
        task_id: The task ID.

    Returns:
        Coverage analysis results.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]
    raw_records = task.get("raw_records", [])

    if not raw_records:
        raise HTTPException(status_code=400, detail="No records found for this task.")

    # Get topic keywords
    request = task.get("request", {})
    topic = request.get("topic", "")

    # Simple keyword extraction from topic
    from ...services.topic_parser import TopicParser
    parser = TopicParser()
    topic_analysis = parser.parse(topic)
    keywords = topic_analysis.keywords[:10]

    # Check coverage
    from ...services.workflow_runner import check_topic_coverage
    from ...core.models import PaperRecord

    records = [PaperRecord(**r) if isinstance(r, dict) else r for r in raw_records]
    coverage = check_topic_coverage(records, keywords, min_papers_per_topic=2)

    return CoverageResponse(
        task_id=task_id,
        coverage_ok=coverage["coverage_ok"],
        covered_topics=coverage["covered_topics"],
        underrepresented_topics=coverage["underrepresented_topics"],
        coverage_counts=coverage["coverage_counts"],
        suggested_queries=coverage["suggested_queries"],
    )


def run_review_task(task_id: str, request: ReviewCreateRequest) -> None:
    """Run the review task in the background.

    Args:
        task_id: The task ID.
        request: The review creation request.
    """
    task = tasks_storage[task_id]

    try:
        task["status"] = "running"
        task["message"] = "Starting workflow..."

        # Parse topic using LLM for better keyword extraction
        parser = TopicParser(
            llm_client=settings.dashscope_api_key,
            llm_model=settings.model_name,
            llm_base_url=settings.model_base_url,
        )
        topic_analysis = parser.parse_with_llm_sync(request.topic, request.user_description)
        print(f"[TopicParser] 提取的关键词: {topic_analysis.keywords}")
        print(f"[TopicParser] 提取的检索词: {topic_analysis.search_terms}")

        # Save topic analysis to task immediately
        task["topic_analysis"] = {
            "domain": topic_analysis.domain,
            "sub_domains": topic_analysis.sub_domains,
            "keywords": topic_analysis.keywords,
            "search_terms": topic_analysis.search_terms,
        }

        # Create config
        output_dir = settings.output_dir / task_id
        config = RunConfig(
            topic=request.topic,
            user_description=request.user_description,
            journal_type=request.journal_type,
            language=request.language,
            word_count_min=request.word_count_min,
            word_count_max=request.word_count_max,
            target_refs=request.target_refs,
            retrieval_pool_size=request.retrieval_pool_size,
            year_window=request.year_window,
            review_rounds_min=request.review_rounds_min,
            review_rounds_max=request.review_rounds_max,
            output_dir=output_dir,
            output_path=output_dir / "final_review.md",
        )

        # Progress callback
        def progress_callback(stage: str, message: str, progress: float) -> None:
            task["current_stage"] = stage
            task["message"] = message
            task["progress"] = progress

        # Source progress callback for retrieval stage
        def source_progress_callback(source: str, status: str, count: int, error: Optional[str] = None) -> None:
            if "sources_progress" not in task:
                task["sources_progress"] = {}
            task["sources_progress"][source] = {
                "status": status,
                "count": count,
                "error": error,
            }
            task["current_source"] = source if status == "running" else None

        # Stage data callback - receives intermediate results from each stage
        def stage_data_callback(stage: str, data: Dict[str, Any]) -> None:
            """Callback to receive intermediate data from workflow stages."""
            if stage == "planning":
                # Save plan sections
                if "sections" in data:
                    task["plan_sections"] = data["sections"]
            elif stage == "retrieval":
                # Save retrieved papers preview (first 20) AND full records
                if "records" in data:
                    records = data["records"]
                    # Save full records for export
                    task["raw_records"] = records
                    # Save preview for display
                    task["retrieved_papers_preview"] = [
                        {
                            "ref_id": r.get("ref_id", ""),
                            "title": r.get("title", ""),
                            "year": r.get("year"),
                            "journal": r.get("journal", ""),
                            "jcr_quartile": r.get("jcr_quartile", ""),
                            "relevance_score": r.get("relevance_score", 0.0),
                            "abstract_preview": (r.get("abstract", "") or "")[:200],
                        }
                        for r in records[:20]
                    ]
                    task["total_retrieved"] = len(records)
            elif stage == "screening":
                # Save selected papers preview AND full records
                if "selected_records" in data:
                    records = data["selected_records"]
                    # Save full records for export
                    task["selected_records"] = records
                    # Save preview for display
                    task["selected_papers_preview"] = [
                        {
                            "ref_id": r.get("ref_id", ""),
                            "title": r.get("title", ""),
                            "year": r.get("year"),
                            "journal": r.get("journal", ""),
                            "jcr_quartile": r.get("jcr_quartile", ""),
                            "relevance_score": r.get("relevance_score", 0.0),
                            "abstract_preview": (r.get("abstract", "") or "")[:200],
                        }
                        for r in records[:20]
                    ]
                    task["total_selected"] = len(records)
            elif stage == "writing":
                # Save draft preview
                if "draft" in data:
                    task["draft_preview"] = data["draft"][:500] + "..." if len(data["draft"]) > 500 else data["draft"]

        # Run workflow
        runner = WorkflowRunner(
            config=config,
            topic_analysis=topic_analysis,
            wos_api_key=settings.wos_api_key or None,
            progress_callback=progress_callback,
            source_progress_callback=source_progress_callback,
            stage_data_callback=stage_data_callback,
        )

        # Run in async context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(runner.run())
        finally:
            loop.close()

        # Store results
        task["status"] = "completed"
        task["progress"] = 1.0
        task["message"] = "Review generation completed successfully"
        task["final_markdown"] = results.get("final_markdown")
        task["validation"] = results.get("validation")
        task["token_usage"] = results.get("token_usage")

    except Exception as e:
        task["status"] = "failed"
        task["message"] = f"Error: {str(e)}"
        task["error"] = str(e)


# ============ Checkpoint Management Endpoints ============

class CheckpointInfoResponse(BaseModel):
    """Response model for checkpoint info."""
    task_id: str
    has_checkpoint: bool
    stage: Optional[str] = None
    progress: Optional[float] = None
    message: Optional[str] = None
    timestamp: Optional[str] = None


class CheckpointListResponse(BaseModel):
    """Response model for checkpoint list."""
    task_id: str
    checkpoints: List[Dict[str, Any]]


class ResumeRequest(BaseModel):
    """Request model for resuming a task."""
    stage: Optional[str] = None  # Specific stage to resume from


class ResumeResponse(BaseModel):
    """Response model for resume operation."""
    task_id: str
    status: str
    message: str
    resume_from_stage: Optional[str] = None


@router.get("/{task_id}/checkpoint", response_model=CheckpointInfoResponse)
async def get_checkpoint_info(task_id: str) -> CheckpointInfoResponse:
    """Get checkpoint information for a task.

    Args:
        task_id: The task ID.

    Returns:
        Checkpoint information.
    """
    output_dir = settings.output_dir / task_id
    from ...services.checkpoint_manager import CheckpointManager

    checkpoint_manager = CheckpointManager(output_dir)
    checkpoint = checkpoint_manager.load()

    if checkpoint:
        return CheckpointInfoResponse(
            task_id=task_id,
            has_checkpoint=True,
            stage=checkpoint.stage,
            progress=checkpoint.progress,
            message=checkpoint.message,
            timestamp=checkpoint.timestamp,
        )
    else:
        return CheckpointInfoResponse(
            task_id=task_id,
            has_checkpoint=False,
        )


@router.get("/{task_id}/checkpoints", response_model=CheckpointListResponse)
async def list_checkpoints(task_id: str) -> CheckpointListResponse:
    """List all checkpoint backups for a task.

    Args:
        task_id: The task ID.

    Returns:
        List of checkpoint information.
    """
    output_dir = settings.output_dir / task_id
    from ...services.checkpoint_manager import CheckpointManager

    checkpoint_manager = CheckpointManager(output_dir)
    checkpoints = checkpoint_manager.list_checkpoints()

    return CheckpointListResponse(
        task_id=task_id,
        checkpoints=checkpoints,
    )


@router.post("/{task_id}/resume", response_model=ResumeResponse)
async def resume_task(
    task_id: str,
    request: ResumeRequest,
    background_tasks: BackgroundTasks,
) -> ResumeResponse:
    """Resume a failed/interrupted task from checkpoint.

    Args:
        task_id: The task ID.
        request: Resume request with optional stage override.
        background_tasks: FastAPI background tasks.

    Returns:
        Resume operation status.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]

    # Check current status
    if task["status"] == "running":
        raise HTTPException(status_code=400, detail="Task is already running.")

    # Check if checkpoint exists
    output_dir = settings.output_dir / task_id
    from ...services.checkpoint_manager import CheckpointManager

    checkpoint_manager = CheckpointManager(output_dir)
    if not checkpoint_manager.exists():
        raise HTTPException(status_code=404, detail="No checkpoint found for this task.")

    checkpoint = checkpoint_manager.load()
    resume_stage = request.stage or checkpoint.stage if checkpoint else None

    # Start background resume task
    background_tasks.add_task(resume_review_task, task_id, request.stage)

    return ResumeResponse(
        task_id=task_id,
        status="resuming",
        message=f"Resuming task from checkpoint",
        resume_from_stage=resume_stage,
    )


@router.delete("/{task_id}/checkpoint")
async def clear_checkpoint(task_id: str) -> Dict[str, Any]:
    """Clear the checkpoint for a task.

    Args:
        task_id: The task ID.

    Returns:
        Status message.
    """
    output_dir = settings.output_dir / task_id
    from ...services.checkpoint_manager import CheckpointManager

    checkpoint_manager = CheckpointManager(output_dir)
    if not checkpoint_manager.exists():
        raise HTTPException(status_code=404, detail="No checkpoint found.")

    checkpoint_manager.clear()

    return {"task_id": task_id, "message": "Checkpoint cleared successfully"}


def resume_review_task(task_id: str, resume_from_stage: Optional[str] = None) -> None:
    """Resume a review task from checkpoint.

    Args:
        task_id: The task ID.
        resume_from_stage: Optional specific stage to resume from.
    """
    task = tasks_storage[task_id]
    request_data = task.get("request", {})

    try:
        task["status"] = "running"
        task["message"] = "Resuming from checkpoint..."

        # Parse topic using LLM for better keyword extraction
        parser = TopicParser(
            llm_client=settings.dashscope_api_key,
            llm_model=settings.model_name,
            llm_base_url=settings.model_base_url,
        )
        topic_analysis = parser.parse_with_llm_sync(
            request_data.get("topic", ""),
            request_data.get("user_description", "")
        )

        # Create config
        output_dir = settings.output_dir / task_id
        config = RunConfig(
            topic=request_data.get("topic", ""),
            user_description=request_data.get("user_description", ""),
            journal_type=request_data.get("journal_type", "中文核心期刊"),
            language=request_data.get("language", "中文"),
            word_count_min=request_data.get("word_count_min", 4000),
            word_count_max=request_data.get("word_count_max", 6000),
            target_refs=request_data.get("target_refs", 40),
            retrieval_pool_size=request_data.get("retrieval_pool_size", 100),
            year_window=request_data.get("year_window", 5),
            review_rounds_min=request_data.get("review_rounds_min", 2),
            review_rounds_max=request_data.get("review_rounds_max", 3),
            output_dir=output_dir,
            output_path=output_dir / "final_review.md",
        )

        # Progress callback
        def progress_callback(stage: str, message: str, progress: float) -> None:
            task["current_stage"] = stage
            task["message"] = message
            task["progress"] = progress

        # Source progress callback for retrieval stage
        def source_progress_callback(source: str, status: str, count: int, error: Optional[str] = None) -> None:
            if "sources_progress" not in task:
                task["sources_progress"] = {}
            task["sources_progress"][source] = {
                "status": status,
                "count": count,
                "error": error,
            }
            task["current_source"] = source if status == "running" else None

        # Run workflow with resume enabled
        runner = WorkflowRunner(
            config=config,
            topic_analysis=topic_analysis,
            wos_api_key=settings.wos_api_key or None,
            progress_callback=progress_callback,
            source_progress_callback=source_progress_callback,
            enable_checkpoint=True,
        )

        # Run in async context with resume
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                runner.run(resume=True, resume_from_stage=resume_from_stage)
            )
        finally:
            loop.close()

        # Store results
        task["status"] = "completed"
        task["progress"] = 1.0
        task["message"] = "Review generation completed successfully"
        task["final_markdown"] = results.get("final_markdown")
        task["validation"] = results.get("validation")
        task["token_usage"] = results.get("token_usage")
        task["raw_records"] = results.get("raw_records")
        task["selected_records"] = results.get("selected_records")

    except Exception as e:
        task["status"] = "failed"
        task["message"] = f"Error: {str(e)}"
        task["error"] = str(e)
