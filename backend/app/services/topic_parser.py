# Topic Parser Service - analyzes input topics and extracts domain information
import json
import re
import time
from typing import Any, Dict, List, Optional

from ..core.models import TopicAnalysis


def extract_json_from_llm_response(content: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks.

    Args:
        content: Raw LLM response content.

    Returns:
        Parsed JSON dict, or empty dict if parsing fails.
    """
    if not content:
        return {}

    cleaned = content.strip()

    # Try to extract JSON from markdown code blocks
    # Match ```json ... ``` or ``` ... ``` containing JSON
    fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fenced_match:
        cleaned = fenced_match.group(1).strip()

    # If no code block, try to extract JSON object
    if not cleaned.startswith("{"):
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace >= 0 and last_brace >= first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {}


class TopicParser:
    """Service for parsing and analyzing research topics."""

    # Domain keyword mappings
    DOMAIN_KEYWORDS = {
        "人工智能": ["人工智能", "AI", "机器学习", "深度学习", "神经网络", "大模型", "LLM", "GPT", "自然语言处理", "NLP", "计算机视觉", "CV"],
        "食品安全": ["食品安全", "食品质量", "食品检测", "食品溯源", "食品监管", "食品风险", "食品加工", "食品添加剂", "药食同源"],
        "生物医学": ["生物医学", "医学影像", "疾病诊断", "药物研发", "基因", "蛋白质", "细胞", "临床"],
        "环境科学": ["环境", "污染", "气候变化", "生态", "可持续发展", "碳排放", "环保"],
        "材料科学": ["材料", "纳米", "复合材料", "高分子", "金属材料", "陶瓷"],
        "能源": ["能源", "太阳能", "风能", "电池", "储能", "核能", "可再生能源"],
        "计算机科学": ["算法", "数据结构", "分布式", "云计算", "区块链", "物联网", "网络安全"],
        "化学": ["化学", "催化", "合成", "反应", "分子", "有机", "无机"],
        "物理学": ["物理", "量子", "光学", "凝聚态", "粒子", "超导"],
        "经济学": ["经济", "金融", "市场", "投资", "贸易", "货币"],
    }

    # Common sub-domain patterns
    SUB_DOMAIN_PATTERNS = {
        "人工智能": [
            "大语言模型", "机器学习", "深度学习", "自然语言处理", "计算机视觉",
            "知识图谱", "推荐系统", "强化学习", "联邦学习", "多模态"
        ],
        "食品安全": [
            "风险评估", "检测技术", "溯源系统", "监管合规", "质量控制",
            "添加剂检测", "微生物检测", "农药残留", "重金属检测", "药食同源"
        ],
    }

    def __init__(self, llm_client: Optional[Any] = None, llm_model: str = "", llm_base_url: str = "") -> None:
        """Initialize the topic parser.

        Args:
            llm_client: Optional LLM client for advanced analysis.
            llm_model: LLM model name.
            llm_base_url: LLM API base URL.
        """
        self.llm_client = llm_client
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url

    def parse(self, topic: str, user_description: str = "") -> TopicAnalysis:
        """Parse and analyze a research topic.

        Args:
            topic: The research topic string.
            user_description: Optional user description for better keyword extraction.

        Returns:
            TopicAnalysis object with extracted information.
        """
        # Extract primary domain
        domain = self._extract_domain(topic)

        # Extract keywords
        keywords = self._extract_keywords(topic, domain)

        # Generate search terms
        search_terms = self._generate_search_terms(topic, domain, keywords)

        # Extract sub-domains
        sub_domains = self._extract_sub_domains(topic, domain)

        # Generate suggested sections
        suggested_sections = self._suggest_sections(topic, domain, keywords)

        # Generate relevance hints
        relevance_hints = self._generate_relevance_hints(topic, domain, keywords)

        return TopicAnalysis(
            domain=domain,
            sub_domains=sub_domains,
            keywords=keywords,
            search_terms=search_terms,
            suggested_sections=suggested_sections,
            relevance_hints=relevance_hints,
        )

    def _extract_domain(self, topic: str) -> str:
        """Extract the primary research domain from topic."""
        topic_lower = topic.lower()
        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            for keyword in keywords:
                if keyword.lower() in topic_lower:
                    return domain
        return "综合学科"

    def _extract_keywords(self, topic: str, domain: str) -> List[str]:
        """Extract relevant keywords from topic.

        改进：使用更智能的关键词提取策略，避免提取无意义的字符片段。
        """
        keywords = []

        # 1. Add domain-specific keywords (优先级最高)
        if domain in self.DOMAIN_KEYWORDS:
            for keyword in self.DOMAIN_KEYWORDS[domain]:
                if keyword in topic:
                    keywords.append(keyword)

        # 2. 尝试使用 jieba 分词（如果可用）
        try:
            import jieba
            import jieba.analyse

            # 使用 TF-IDF 提取关键词
            extracted = jieba.analyse.extract_tags(topic, topK=10, withWeight=False)
            # 过滤掉单字和常见停用词
            stopwords = {'的', '在', '和', '与', '了', '是', '有', '对', '中', '为', '及', '等', '到', '从', '被', '将', '把'}
            for word in extracted:
                if len(word) >= 2 and word not in stopwords and word not in keywords:
                    keywords.append(word)
        except ImportError:
            # jieba 不可用，使用改进的正则方法
            # 提取完整的中文词语（使用词边界）
            # 匹配常见的学术术语模式
            patterns = [
                # 匹配引号中的词
                r'["\']([^"\']+)["\']',
                # 匹配书名号中的词
                r'《([^》]+)》',
                # 匹配带有顿号分隔的并列词组
                r'([^\s、，。！？]+)(?:、|，|和)',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, topic)
                for m in matches:
                    if len(m) >= 2 and len(m) <= 10 and m not in keywords:
                        keywords.append(m)

            # 提取英文术语（更精确的匹配）
            english_pattern = r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|[A-Z]{2,}|(?<![a-zA-Z])[a-z]{3,}(?![a-zA-Z])'
            english_matches = re.findall(english_pattern, topic)
            keywords.extend([m for m in english_matches if m not in keywords and len(m) > 2])

        # 3. 去重并限制数量
        seen = set()
        unique_keywords = []
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique_keywords.append(kw)

        return unique_keywords[:15]

    def _generate_search_terms(self, topic: str, domain: str, keywords: List[str]) -> List[str]:
        """Generate search terms for literature retrieval."""
        search_terms = []

        # Add topic as-is
        search_terms.append(topic)

        # Add key phrases
        if keywords:
            # Combine main keywords
            main_keywords = keywords[:5]
            search_terms.append(" ".join(main_keywords))

        # Add domain-specific search patterns
        if domain == "人工智能":
            search_terms.extend([
                "large language model",
                "deep learning",
                "machine learning",
                "neural network",
                "artificial intelligence",
            ])
        elif domain == "食品安全":
            search_terms.extend([
                "food safety",
                "food quality",
                "food inspection",
                "food traceability",
                "food regulation",
            ])

        return list(set(search_terms))[:10]

    def _extract_sub_domains(self, topic: str, domain: str) -> List[str]:
        """Extract relevant sub-domains."""
        sub_domains = []
        if domain in self.SUB_DOMAIN_PATTERNS:
            for sub_domain in self.SUB_DOMAIN_PATTERNS[domain]:
                if sub_domain in topic:
                    sub_domains.append(sub_domain)
        return sub_domains

    def _suggest_sections(self, topic: str, domain: str, keywords: List[str]) -> List[Dict[str, Any]]:
        """Suggest review section structure."""
        # Default sections for most reviews
        sections = [
            {
                "title": "摘要",
                "goal": "概括研究背景、主要脉络、关键结论与未来方向。",
                "target_words": 350,
                "key_questions": ["为什么该主题重要？", "综述的主线和贡献是什么？"],
                "must_cover": ["研究背景", "主要发现", "结论与展望"],
            },
            {
                "title": "引言",
                "goal": f"说明{domain}领域的研究背景与本综述的现实意义。",
                "target_words": 700,
                "key_questions": ["该领域的研究现状如何？", "为何需要这篇综述？"],
                "must_cover": ["研究背景", "研究意义", "综述范围"],
            },
            {
                "title": "研究方法与技术路线",
                "goal": "系统梳理该领域的主要研究方法和技术路线。",
                "target_words": 1200,
                "key_questions": ["主要研究方法有哪些？", "不同方法的优缺点是什么？"],
                "must_cover": ["方法分类", "技术比较", "发展趋势"],
            },
            {
                "title": "主要研究进展",
                "goal": "归纳该领域的代表性研究成果和核心发现。",
                "target_words": 1500,
                "key_questions": ["主要研究成果有哪些？", "形成了哪些共识？"],
                "must_cover": ["代表性研究", "核心发现", "研究趋势"],
            },
            {
                "title": "存在的问题与挑战",
                "goal": "批判性分析现有研究的不足和面临的挑战。",
                "target_words": 800,
                "key_questions": ["现有研究有哪些不足？", "面临哪些挑战？"],
                "must_cover": ["研究局限", "技术瓶颈", "实际应用问题"],
            },
            {
                "title": "未来发展方向",
                "goal": "基于现有研究提出未来研究方向和发展建议。",
                "target_words": 700,
                "key_questions": ["未来研究重点是什么？", "有哪些潜在突破方向？"],
                "must_cover": ["研究空白", "技术方向", "应用前景"],
            },
            {
                "title": "结论",
                "goal": "凝练全文主张和总体判断。",
                "target_words": 300,
                "key_questions": ["最终结论是什么？"],
                "must_cover": ["总体评价", "研究趋势"],
            },
        ]
        return sections

    def _generate_relevance_hints(self, topic: str, domain: str, keywords: List[str]) -> List[str]:
        """Generate hints for relevance assessment."""
        hints = []
        for keyword in keywords[:5]:
            hints.append(f"文献应与'{keyword}'相关")
        hints.append(f"优先选择{domain}领域的高质量期刊")
        hints.append("优先选择近5年的研究成果")
        return hints

    def parse_with_llm_sync(self, topic: str, user_description: str = "") -> TopicAnalysis:
        """Parse topic using LLM for advanced keyword extraction (synchronous version).

        Args:
            topic: The research topic string.
            user_description: Optional user description for better keyword extraction.

        Returns:
            TopicAnalysis object with LLM-enhanced information.
        """
        # 检查所有必要的 LLM 配置
        if not self.llm_model or not self.llm_base_url or not self.llm_client:
            print("[TopicParser] LLM 配置不完整，回退到规则解析")
            return self.parse(topic, user_description)

        prompt = f"""请分析以下学术综述主题，提取用于文献检索的核心信息。

## 综述主题
{topic}

## 用户写作期望（如有）
{user_description if user_description else "无"}

## 任务要求

请基于主题和用户期望，提炼出用于文献检索的核心信息：

1. **keywords（5-8个核心关键词）**：
   - 必须是能够在学术数据库中检索到的专业术语
   - 中英文混合，英文优先（因为大多数学术文献是英文）
   - 避免过于宽泛的词（如"研究"、"应用"）
   - 必须能准确概括研究主题

2. **search_terms（5个精确检索词）**：
   - 用于在 WOS、PubMed、OpenAlex 等数据库检索
   - 使用布尔运算符组合关键词
   - 格式如："food quality" AND "deep learning"
   - 每个检索词应针对不同的研究方向

3. **domain（主要研究领域）**：
   - 从以下选项中选择：人工智能、食品安全、生物医学、环境科学、材料科学、能源、计算机科学、化学、物理学、经济学、综合学科

4. **sub_domains（2-3个子领域）**：
   - 该主题涉及的具体研究方向

## 输出格式（严格 JSON）

```json
{{
  "domain": "主要研究领域",
  "sub_domains": ["子领域1", "子领域2"],
  "keywords": ["关键词1", "keyword2", "关键词3", "keyword4", "关键词5"],
  "search_terms": ["检索词1", "检索词2", "检索词3", "检索词4", "检索词5"],
  "relevance_hints": ["相关性提示1", "相关性提示2"]
}}
```

请直接输出 JSON，不要包含任何解释。
"""

        import requests

        # 重试配置
        max_retries = 3
        base_timeout = 60  # 增加到 60 秒
        retry_delays = [2, 5, 10]  # 指数退避

        # 模型名称映射（处理不同的命名格式）
        model_name = self.llm_model
        # 移除常见前缀
        for prefix in ["openai/", "anthropic/", "azure/"]:
            if model_name.startswith(prefix):
                model_name = model_name[len(prefix):]
                break

        # 阿里云模型名称映射
        model_mapping = {
            "qwen3.5-plus": "qwen-plus",
            "qwen3.5-turbo": "qwen-turbo",
            "qwen3.5-max": "qwen-max",
            "qwen3-plus": "qwen-plus",
            "qwen3-turbo": "qwen-turbo",
            "qwen3-max": "qwen-max",
        }
        if model_name in model_mapping:
            model_name = model_mapping[model_name]
            print(f"[TopicParser] 模型名称映射: {self.llm_model} -> {model_name}")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.llm_client}",
        }
        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1000,
        }

        last_error = None
        for attempt in range(max_retries):
            try:
                print(f"[TopicParser] LLM 解析尝试 {attempt + 1}/{max_retries}...")
                response = requests.post(
                    f"{self.llm_base_url}/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=base_timeout,
                )

                if response.status_code == 200:
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                    # 使用增强的 JSON 提取函数
                    parsed = extract_json_from_llm_response(content)

                    if not parsed:
                        print(f"[TopicParser] LLM 返回内容无法解析为 JSON")
                        print(f"[TopicParser] LLM 原始响应前 500 字符: {content[:500]}")
                        if attempt < max_retries - 1:
                            print(f"[TopicParser] 等待 {retry_delays[attempt]} 秒后重试...")
                            time.sleep(retry_delays[attempt])
                            continue
                        else:
                            raise ValueError("JSON 解析失败")

                    print(f"[TopicParser] LLM 解析成功！领域: {parsed.get('domain')}, 关键词: {parsed.get('keywords', [])[:3]}...")

                    return TopicAnalysis(
                        domain=parsed.get("domain", "综合学科"),
                        sub_domains=parsed.get("sub_domains", []),
                        keywords=parsed.get("keywords", [])[:15],
                        search_terms=parsed.get("search_terms", [])[:10],
                        suggested_sections=self._suggest_sections(topic, parsed.get("domain", "综合学科"), parsed.get("keywords", [])),
                        relevance_hints=parsed.get("relevance_hints", []),
                    )
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    print(f"[TopicParser] LLM API 调用失败: {last_error}")

                    # 对于 5xx 错误和 429 (Too Many Requests)，进行重试
                    should_retry = (
                        response.status_code >= 500
                        or response.status_code == 429
                        or response.status_code == 408  # Request Timeout
                    )
                    if should_retry and attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        # 429 错误时，如果有 Retry-After 头，使用它
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    wait_time = max(wait_time, int(retry_after))
                                except ValueError:
                                    pass
                        print(f"[TopicParser] 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        break

            except requests.exceptions.Timeout as e:
                last_error = str(e)
                print(f"[TopicParser] LLM 请求超时 (timeout={base_timeout}s): {e}")
                if attempt < max_retries - 1:
                    print(f"[TopicParser] 等待 {retry_delays[attempt]} 秒后重试...")
                    time.sleep(retry_delays[attempt])
                    continue

            except requests.exceptions.RequestException as e:
                last_error = str(e)
                print(f"[TopicParser] LLM 请求异常: {e}")
                if attempt < max_retries - 1:
                    print(f"[TopicParser] 等待 {retry_delays[attempt]} 秒后重试...")
                    time.sleep(retry_delays[attempt])
                    continue

            except Exception as e:
                last_error = str(e)
                print(f"[TopicParser] LLM 解析异常: {e}")
                break

        print(f"[TopicParser] LLM 解析最终失败 ({max_retries} 次重试后)，回退到规则解析。最后错误: {last_error}")
        return self.parse(topic, user_description)

    async def parse_with_llm(self, topic: str, user_description: str = "") -> TopicAnalysis:
        """Parse topic using LLM for advanced analysis (async version).

        Args:
            topic: The research topic string.
            user_description: Optional user description for better keyword extraction.

        Returns:
            TopicAnalysis object with LLM-enhanced information.
        """
        # 直接调用同步版本
        return self.parse_with_llm_sync(topic, user_description)
