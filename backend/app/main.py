# FastAPI Main Application Entry Point
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import agents, exports, reviews, topics, websocket
from .config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    # Create output directory
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    yield

    # Shutdown
    pass


# Create FastAPI application
app = FastAPI(
    title="学术综述自动生成系统 API",
    description="""
通用学术综述自动生成系统的后端API。

## 功能

- **主题分析**: 解析研究主题，提取领域、关键词、检索词
- **Agent生成**: 根据主题动态生成CrewAI Agent定义
- **综述生成**: 执行完整的综述生成工作流
- **实时进度**: WebSocket支持实时进度更新

## 工作流程

1. 输入研究主题
2. 系统分析主题，提取领域信息
3. 动态生成Agent配置
4. 执行文献检索、筛选、分析
5. 撰写综述并进行多轮审稿修订
6. 输出最终综述文档
    """,
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(topics.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(reviews.router, prefix="/api/v1")
app.include_router(exports.router, prefix="/api/v1")
app.include_router(websocket.router)


@app.get("/", tags=["root"])
async def root() -> dict:
    """Root endpoint returning API information."""
    return {
        "name": "学术综述自动生成系统 API",
        "version": "1.0.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "model": settings.model_name,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
