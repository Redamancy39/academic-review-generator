#!/usr/bin/env python
"""
Resume workflow from checkpoint - Command line tool.

Usage:
    python resume_task.py <task_id> [--stage <stage>] [--clear]

Examples:
    python resume_task.py f1c8810e-61e3-4b15-ad46-a026dae63231
    python resume_task.py f1c8810e-61e3-4b15-ad46-a026dae63231 --stage review
    python resume_task.py f1c8810e-61e3-4b15-ad46-a026dae63231 --clear
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.core.models import RunConfig
from app.services.checkpoint_manager import CheckpointManager, CheckpointStage
from app.services.topic_parser import TopicParser
from app.services.workflow_runner import WorkflowRunner


def list_available_tasks():
    """List all tasks with checkpoints."""
    output_dir = settings.output_dir
    if not output_dir.exists():
        print("No output directory found.")
        return []

    tasks = []
    for task_dir in output_dir.iterdir():
        if task_dir.is_dir():
            checkpoint_file = task_dir / "checkpoint.json"
            if checkpoint_file.exists():
                try:
                    with open(checkpoint_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    tasks.append({
                        "task_id": task_dir.name,
                        "stage": data.get("stage"),
                        "progress": data.get("progress"),
                        "message": data.get("message"),
                        "timestamp": data.get("timestamp"),
                        "topic": data.get("config", {}).get("topic", ""),
                    })
                except Exception:
                    pass

    return tasks


def show_checkpoint_info(task_id: str):
    """Show checkpoint info for a task."""
    output_dir = settings.output_dir / task_id
    checkpoint_manager = CheckpointManager(output_dir)

    checkpoint = checkpoint_manager.load()
    if not checkpoint:
        print(f"No checkpoint found for task: {task_id}")
        return

    print(f"\n{'='*60}")
    print(f"Checkpoint Info for Task: {task_id}")
    print(f"{'='*60}")
    print(f"Stage:      {checkpoint.stage}")
    print(f"Progress:   {checkpoint.progress:.1%}")
    print(f"Message:    {checkpoint.message}")
    print(f"Timestamp:  {checkpoint.timestamp}")

    if checkpoint.config:
        print(f"\nTopic:      {checkpoint.config.get('topic', 'N/A')}")

    # Show available data
    print(f"\nAvailable Data:")
    data_items = [
        ("Plan", checkpoint.plan),
        ("Raw Records", checkpoint.raw_records),
        ("Selected Records", checkpoint.selected_records),
        ("Evidence Bank", checkpoint.evidence_bank),
        ("Draft", checkpoint.draft),
        ("Final Draft", checkpoint.final_draft),
        ("Validation", checkpoint.validation),
    ]
    for name, data in data_items:
        if data:
            if isinstance(data, list):
                print(f"  ✓ {name}: {len(data)} items")
            elif isinstance(data, dict):
                print(f"  ✓ {name}: dict with keys {list(data.keys())[:5]}...")
            elif isinstance(data, str):
                print(f"  ✓ {name}: {len(data)} chars")
            else:
                print(f"  ✓ {name}: {type(data).__name__}")
        else:
            print(f"  ✗ {name}: not available")

    # List backup checkpoints
    backups = checkpoint_manager.list_checkpoints()
    if backups:
        print(f"\nBackup Checkpoints ({len(backups)}):")
        for backup in backups[:5]:
            print(f"  - {backup['stage']} @ {backup['progress']:.1%} ({backup['timestamp']})")


def clear_checkpoint(task_id: str):
    """Clear checkpoint for a task."""
    output_dir = settings.output_dir / task_id
    checkpoint_manager = CheckpointManager(output_dir)

    if not checkpoint_manager.exists():
        print(f"No checkpoint found for task: {task_id}")
        return

    checkpoint_manager.clear()
    print(f"Checkpoint cleared for task: {task_id}")


async def resume_workflow(task_id: str, stage: str = None):
    """Resume workflow from checkpoint."""
    output_dir = settings.output_dir / task_id

    # Load checkpoint
    checkpoint_manager = CheckpointManager(output_dir)
    checkpoint = checkpoint_manager.load()

    if not checkpoint:
        print(f"No checkpoint found for task: {task_id}")
        return

    # Get config from checkpoint
    if not checkpoint.config:
        print("Error: No config found in checkpoint")
        return

    config = RunConfig(
        topic=checkpoint.config.get("topic", ""),
        user_description=checkpoint.config.get("user_description", ""),
        journal_type=checkpoint.config.get("journal_type", "中文核心期刊"),
        language=checkpoint.config.get("language", "中文"),
        word_count_min=checkpoint.config.get("word_count_min", 4000),
        word_count_max=checkpoint.config.get("word_count_max", 6000),
        target_refs=checkpoint.config.get("target_refs", 40),
        retrieval_pool_size=checkpoint.config.get("retrieval_pool_size", 100),
        year_window=checkpoint.config.get("year_window", 5),
        review_rounds_min=checkpoint.config.get("review_rounds_min", 2),
        review_rounds_max=checkpoint.config.get("review_rounds_max", 3),
        output_dir=output_dir,
        output_path=output_dir / "final_review.md",
    )

    # Parse topic
    parser = TopicParser()
    topic_analysis = parser.parse(config.topic)

    # Progress callback
    def progress_callback(s: str, msg: str, prog: float):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{s}] {msg} ({prog:.0%})")

    # Create runner
    runner = WorkflowRunner(
        config=config,
        topic_analysis=topic_analysis,
        wos_api_key=settings.wos_api_key,
        progress_callback=progress_callback,
        enable_checkpoint=True,
    )

    print(f"\n{'='*60}")
    print(f"Resuming workflow from stage: {stage or checkpoint.stage}")
    print(f"{'='*60}\n")

    try:
        results = await runner.run(resume=True, resume_from_stage=stage)
        print(f"\n{'='*60}")
        print("Workflow completed successfully!")
        print(f"{'='*60}")
        print(f"Final output: {config.output_path}")
        print(f"Word count: {results.get('validation', {}).get('word_count', 'N/A')}")
        print(f"Citations: {results.get('validation', {}).get('unique_citation_count', 'N/A')}")
    except Exception as e:
        print(f"\nError during workflow: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Resume workflow from checkpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available stages:
  init, planning, retrieval, screening, analysis, writing, review, finalizing

Examples:
  python resume_task.py f1c8810e-61e3-4b15-ad46-a026dae63231
  python resume_task.py f1c8810e-61e3-4b15-ad46-a026dae63231 --stage review
  python resume_task.py f1c8810e-61e3-4b15-ad46-a026dae63231 --info
  python resume_task.py f1c8810e-61e3-4b15-ad46-a026dae63231 --clear
  python resume_task.py --list
        """,
    )

    parser.add_argument("task_id", nargs="?", help="Task ID to resume")
    parser.add_argument("--stage", "-s", help="Stage to resume from (overrides checkpoint)")
    parser.add_argument("--info", "-i", action="store_true", help="Show checkpoint info")
    parser.add_argument("--clear", "-c", action="store_true", help="Clear checkpoint")
    parser.add_argument("--list", "-l", action="store_true", help="List all tasks with checkpoints")

    args = parser.parse_args()

    if args.list:
        tasks = list_available_tasks()
        if not tasks:
            print("No tasks with checkpoints found.")
            return

        print(f"\n{'='*60}")
        print("Tasks with Checkpoints")
        print(f"{'='*60}")
        for task in tasks:
            print(f"\nTask ID:    {task['task_id']}")
            print(f"Stage:      {task['stage']}")
            print(f"Progress:   {task['progress']:.1%}")
            print(f"Message:    {task['message']}")
            print(f"Timestamp:  {task['timestamp']}")
            if task['topic']:
                print(f"Topic:      {task['topic'][:50]}...")
        return

    if not args.task_id:
        parser.print_help()
        return

    if args.clear:
        clear_checkpoint(args.task_id)
        return

    if args.info:
        show_checkpoint_info(args.task_id)
        return

    # Resume workflow
    asyncio.run(resume_workflow(args.task_id, args.stage))


if __name__ == "__main__":
    main()
