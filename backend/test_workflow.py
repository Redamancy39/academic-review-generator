"""
完整工作流测试脚本
用于测试学术综述生成系统并记录 token 使用情况
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '.')

from app.core.models import RunConfig, to_jsonable
from app.services.topic_parser import TopicParser
from app.services.agent_generator import AgentGenerator
from app.services.prompt_renderer import PromptRenderer
from app.services.workflow_runner import WorkflowRunner


def count_tokens(text: str) -> int:
    """估算文本的 token 数量（中文约1.5字符/token，英文约4字符/token）"""
    chinese_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def estimate_workflow_tokens(config: RunConfig, num_papers: int) -> dict:
    """估算整个工作流的 token 消耗"""
    estimates = {
        "planning": {"input": 800, "output": 2000},
        "retrieval": {"input": 1200, "output": 600},
        "screening": {"input": 15000 + num_papers * 300, "output": 1000},
        "analysis": {"input": num_papers * 500, "output": num_papers * 200},
        "synthesis": {"input": 3000, "output": 1500},
        "writing": {"input": 12000, "output": 5000},
        "review_per_round": {"input": 16000, "output": 1500},
        "revision_per_round": {"input": 16000, "output": 5000},
    }

    review_rounds = config.review_rounds_min
    total_input = (
        estimates["planning"]["input"] +
        estimates["retrieval"]["input"] +
        estimates["screening"]["input"] +
        estimates["analysis"]["input"] +
        estimates["synthesis"]["input"] +
        estimates["writing"]["input"] +
        estimates["review_per_round"]["input"] * review_rounds +
        estimates["revision_per_round"]["input"] * (review_rounds - 1)
    )

    total_output = (
        estimates["planning"]["output"] +
        estimates["retrieval"]["output"] +
        estimates["screening"]["output"] +
        estimates["analysis"]["output"] +
        estimates["synthesis"]["output"] +
        estimates["writing"]["output"] +
        estimates["review_per_round"]["output"] * review_rounds +
        estimates["revision_per_round"]["output"] * (review_rounds - 1)
    )

    return {
        "estimates": estimates,
        "total_input": total_input,
        "total_output": total_output,
        "total_tokens": total_input + total_output,
    }


async def test_workflow(topic: str, target_refs: int = 10):
    """测试完整工作流"""
    print("=" * 60)
    print("学术综述生成系统 - 测试运行")
    print("=" * 60)
    print(f"主题: {topic}")
    print(f"目标文献数: {target_refs}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 创建输出目录
    output_dir = Path('outputs/test_workflow')
    output_dir.mkdir(parents=True, exist_ok=True)

    # 创建配置
    config = RunConfig(
        topic=topic,
        target_refs=target_refs,
        year_window=5,
        review_rounds_min=1,  # 测试时减少审稿轮次
        review_rounds_max=2,
        output_dir=output_dir,
        output_path=output_dir / 'final_review.md',
        word_count_min=2000,  # 测试时减少字数
        word_count_max=4000,
    )

    # 估算 token 消耗
    estimates = estimate_workflow_tokens(config, target_refs)
    print(f"\n预估 Token 消耗:")
    print(f"  输入 Tokens: ~{estimates['total_input']:,}")
    print(f"  输出 Tokens: ~{estimates['total_output']:,}")
    print(f"  总 Tokens: ~{estimates['total_tokens']:,}")
    print(f"  预估费用 (qwen-plus): ~{estimates['total_tokens'] * 0.8 / 1000000:.2f} 元")
    print()

    # 解析主题
    print("[1/7] 解析主题...")
    parser = TopicParser()
    topic_analysis = parser.parse(topic)
    print(f"  领域: {topic_analysis.domain}")
    print(f"  关键词: {', '.join(topic_analysis.keywords[:5])}")

    # 生成 Agent
    print("\n[2/7] 生成 Agent 配置...")
    generator = AgentGenerator()
    agent_configs = {
        "topic": topic,
        "domain": topic_analysis.domain,
        "keywords": topic_analysis.keywords,
    }
    definitions = generator.generate_all(topic_analysis, agent_configs)
    print(f"  生成了 {len(definitions)} 个 Agent")

    # 初始化工作流运行器
    print("\n[3/7] 初始化工作流...")

    token_usage = {"input": 0, "output": 0}

    def progress_callback(stage: str, message: str, progress: float):
        print(f"  [{stage}] {message} ({int(progress * 100)}%)")

    runner = WorkflowRunner(
        config=config,
        topic_analysis=topic_analysis,
        progress_callback=progress_callback,
    )

    # 运行工作流
    print("\n[4/7] 执行工作流...")
    start_time = time.time()

    try:
        results = await runner.run()

        elapsed_time = time.time() - start_time
        print(f"\n[5/7] 工作流完成! 耗时: {elapsed_time:.1f}秒")

        # 显示结果
        print("\n[6/7] 结果摘要:")
        if results.get("final_markdown"):
            word_count = len(results["final_markdown"])
            print(f"  生成长度: {word_count} 字符")

        validation = results.get("validation", {})
        print(f"  验证结果:")
        print(f"    - 字数: {validation.get('word_count', 'N/A')}")
        print(f"    - 引用数: {validation.get('unique_citation_count', 'N/A')}")
        print(f"    - 通过: {validation.get('passes', False)}")

        # 保存结果
        print("\n[7/7] 保存结果...")
        result_file = output_dir / 'workflow_results.json'
        results_to_save = {k: v for k, v in results.items() if k != 'final_markdown'}
        results_to_save['final_markdown_length'] = len(results.get('final_markdown', ''))
        result_file.write_text(json.dumps(results_to_save, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(f"  结果已保存到: {result_file}")

        # 显示最终综述
        if results.get("final_markdown"):
            print("\n" + "=" * 60)
            print("生成的综述预览 (前 500 字):")
            print("=" * 60)
            print(results["final_markdown"][:500])
            print("...")

        return results

    except Exception as e:
        print(f"\n工作流执行失败: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    topic = "大模型在食品安全监管领域的应用"

    # 运行测试
    results = asyncio.run(test_workflow(topic, target_refs=10))

    if results:
        print("\n" + "=" * 60)
        print("测试完成!")
        print("=" * 60)
