# Agents API - agent generation endpoints
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.models import TopicAnalysis
from ...services.agent_generator import AgentGenerator

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentGenerateRequest(BaseModel):
    """Request model for agent generation."""
    topic: str
    domain: str
    keywords: List[str]
    sub_domains: Optional[List[str]] = None
    journal_type: str = "中文顶级期刊"
    word_count_min: int = 4000
    word_count_max: int = 6000
    target_refs: int = 40
    year_window: int = 5


class AgentDefinitionResponse(BaseModel):
    """Response model for a single agent definition."""
    role: str
    goal: str
    backstory: str
    verbose: bool


class AgentsGenerateResponse(BaseModel):
    """Response model for all agent definitions."""
    planner: AgentDefinitionResponse
    retriever: AgentDefinitionResponse
    screener: AgentDefinitionResponse
    analyzer: AgentDefinitionResponse
    writer: AgentDefinitionResponse
    reviewer: AgentDefinitionResponse


@router.post("/generate", response_model=AgentsGenerateResponse)
async def generate_agents(request: AgentGenerateRequest) -> AgentsGenerateResponse:
    """Generate agent definitions based on topic analysis.

    Args:
        request: The agent generation request with topic analysis.

    Returns:
        Generated agent definitions for all workflow roles.
    """
    if not request.topic:
        raise HTTPException(status_code=400, detail="Topic is required.")

    # Create topic analysis from request
    topic_analysis = TopicAnalysis(
        domain=request.domain,
        sub_domains=request.sub_domains or [],
        keywords=request.keywords,
        search_terms=[],
        suggested_sections=[],
        relevance_hints=[],
    )

    # Generate agents
    generator = AgentGenerator()
    config = {
        "topic": request.topic,
        "journal_type": request.journal_type,
        "word_count_min": request.word_count_min,
        "word_count_max": request.word_count_max,
        "target_refs": request.target_refs,
        "year_window": request.year_window,
    }
    definitions = generator.generate_all(topic_analysis, config)

    def to_response(definition) -> AgentDefinitionResponse:
        return AgentDefinitionResponse(
            role=definition.role,
            goal=definition.goal,
            backstory=definition.backstory,
            verbose=definition.verbose,
        )

    return AgentsGenerateResponse(
        planner=to_response(definitions["planner"]),
        retriever=to_response(definitions["retriever"]),
        screener=to_response(definitions["screener"]),
        analyzer=to_response(definitions["analyzer"]),
        writer=to_response(definitions["writer"]),
        reviewer=to_response(definitions["reviewer"]),
    )
