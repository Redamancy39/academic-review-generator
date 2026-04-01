# 通用学术综述自动生成系统

基于 CrewAI 框架的通用学术综述自动生成系统，支持任意研究主题输入，动态生成 Agent 配置和 Prompt 模板。

## 功能特性

- **通用主题支持**: 支持任意研究主题输入，自动解析领域和关键词
- **动态 Agent 生成**: 根据主题动态生成 CrewAI Agent 定义
- **Jinja2 模板系统**: Agent 和 Prompt 均使用模板，易于定制
- **多源文献检索**: 支持 WOS、Crossref、OpenAlex、PubMed
- **多轮审稿修订**: 自动进行多轮审稿和修订
- **实时进度监控**: WebSocket 支持实时进度更新
- **Web 前端界面**: Vue 3 + Element Plus 现代化界面

## 项目结构

```
E:\crew_ai\
├── backend/                          # 后端服务
│   ├── app/
│   │   ├── main.py                   # FastAPI 入口
│   │   ├── config.py                 # 配置管理
│   │   ├── api/v1/                   # API 路由
│   │   │   ├── topics.py             # 主题解析 API
│   │   │   ├── agents.py             # Agent 生成 API
│   │   │   ├── reviews.py            # 综述任务 API
│   │   │   └── websocket.py          # WebSocket
│   │   ├── services/                 # 业务服务
│   │   │   ├── topic_parser.py       # 主题解析
│   │   │   ├── agent_generator.py    # Agent 生成
│   │   │   ├── prompt_renderer.py    # Prompt 渲染
│   │   │   └── workflow_runner.py    # 工作流执行
│   │   ├── core/                     # 核心模块
│   │   │   ├── models.py             # 数据模型
│   │   │   ├── prompts/              # Jinja2 模板
│   │   │   │   ├── agent_templates/  # Agent 模板
│   │   │   │   └── task_templates/   # Prompt 模板
│   │   │   └── retrievers/           # 检索器
│   │   └── utils/
│   ├── requirements.txt
│   └── .env.example
├── frontend/                         # Vue 3 前端
│   ├── src/
│   │   ├── components/
│   │   ├── views/
│   │   ├── api/
│   │   └── stores/
│   ├── package.json
│   └── vite.config.js
├── try.py                            # 原始代码 (参考)
└── README.md
```

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- pnpm / npm / yarn

### 后端安装

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
.\venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Keys
```

### 前端安装

```bash
cd frontend

# 安装依赖
npm install
# 或
pnpm install
```

### 启动服务

```bash
# 启动后端 (在 backend 目录)
python -m app.main
# 或
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 启动前端 (在 frontend 目录)
npm run dev
```

访问 http://localhost:5173 使用系统。

## API 文档

启动后端后访问:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要 API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/v1/topics/analyze` | POST | 分析研究主题 |
| `/api/v1/agents/generate` | POST | 生成 Agent 定义 |
| `/api/v1/reviews/create` | POST | 创建综述任务 |
| `/api/v1/reviews/{task_id}` | GET | 获取任务状态 |
| `/api/v1/reviews/{task_id}/result` | GET | 获取任务结果 |
| `/ws/progress` | WebSocket | 实时进度更新 |

## 配置说明

### 环境变量

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| `DASHSCOPE_API_KEY` | 阿里云 Dashscope API Key | - |
| `WOS_API_KEY` | Web of Science API Key | - |
| `MODEL_NAME` | 模型名称 | `openai/qwen3.5-plus` |
| `MODEL_BASE_URL` | 模型 API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `HOST` | 服务监听地址 | `0.0.0.0` |
| `PORT` | 服务端口 | `8000` |

### 参数配置

| 参数 | 描述 | 默认值 |
|------|------|--------|
| `word_count_min` | 最小字数 | 4000 |
| `word_count_max` | 最大字数 | 6000 |
| `target_refs` | 目标参考文献数 | 40 |
| `year_window` | 年份窗口 | 5 |
| `review_rounds_min` | 最小审稿轮次 | 2 |
| `review_rounds_max` | 最大审稿轮次 | 3 |

## 架构设计

### 工作流程

1. **主题解析**: 输入主题后，系统自动提取领域、关键词、检索词
2. **Agent 生成**: 根据主题动态生成 6 个 Agent (规划者、检索者、筛选者、分析者、撰写者、审稿者)
3. **文献检索**: 从 WOS、Crossref、OpenAlex、PubMed 检索文献
4. **文献筛选**: 根据年份、期刊质量、相关性筛选文献
5. **证据提取**: 分析文献，提取研究问题、方法、结论等
6. **综述撰写**: 基于证据库撰写综述
7. **审稿修订**: 多轮自动审稿和修订
8. **结果输出**: 生成最终的 Markdown 文档

### 核心创新

**动态 Agent 生成**: 使用 Jinja2 模板根据主题动态生成 Agent 定义:

```jinja2
{# agent_templates/planner.j2 #}
role: "综述选题与框架总策划"
goal: |
  规划一篇符合{{ journal_type }}要求的{{ domain }}领域综述，
  主题为"{{ topic }}"。
backstory: |
  你擅长把宽泛研究主题拆解为可执行的系统综述路线，
  当前研究域为：{{ domain }}，关键词包括：{{ keywords }}。
```

## 技术栈

### 后端
- FastAPI - Web 框架
- CrewAI - Agent 编排
- Jinja2 - 模板引擎
- Pydantic - 数据验证
- Uvicorn - ASGI 服务器

### 前端
- Vue 3 - 前端框架
- Vite - 构建工具
- Element Plus - UI 组件库
- Pinia - 状态管理
- Axios - HTTP 客户端
- Marked - Markdown 渲染

## 许可证

MIT License
