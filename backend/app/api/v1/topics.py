# Topics API - topic analysis endpoints
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...services.topic_parser import TopicParser
from ..deps import get_dashscope_api_key

router = APIRouter(prefix="/topics", tags=["topics"])


class TopicAnalyzeRequest(BaseModel):
    """Request model for topic analysis."""
    topic: str


class TopicAnalyzeResponse(BaseModel):
    """Response model for topic analysis."""
    domain: str
    sub_domains: list[str]
    keywords: list[str]
    search_terms: list[str]
    suggested_sections: list[Dict[str, Any]]
    relevance_hints: list[str]


@router.post("/analyze", response_model=TopicAnalyzeResponse)
async def analyze_topic(request: TopicAnalyzeRequest) -> TopicAnalyzeResponse:
    """Analyze a research topic and extract domain information.

    Args:
        request: The topic analysis request.

    Returns:
        Topic analysis results including domain, keywords, and suggested structure.
    """
    if not request.topic or len(request.topic.strip()) < 5:
        raise HTTPException(status_code=400, detail="Topic must be at least 5 characters long.")

    parser = TopicParser()
    analysis = parser.parse(request.topic)

    return TopicAnalyzeResponse(
        domain=analysis.domain,
        sub_domains=analysis.sub_domains,
        keywords=analysis.keywords,
        search_terms=analysis.search_terms,
        suggested_sections=analysis.suggested_sections,
        relevance_hints=analysis.relevance_hints,
    )
