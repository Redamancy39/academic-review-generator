# Checkpoint Manager - save and restore workflow state
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, get_type_hints

from ..core.models import (
    EvidenceNote,
    PaperRecord,
    ReviewReport,
    RunConfig,
    TopicAnalysis,
    WorkflowTokenUsage,
    to_jsonable,
)


T = TypeVar("T")


class CheckpointStage:
    """Checkpoint stage identifiers."""
    INIT = "init"
    PLANNING = "planning"
    RETRIEVAL = "retrieval"
    SCREENING = "screening"
    ANALYSIS = "analysis"
    WRITING = "writing"
    REVIEW = "review"
    FINALIZING = "finalizing"
    COMPLETE = "complete"


class CheckpointData:
    """Data structure for a checkpoint."""

    def __init__(
        self,
        stage: str,
        progress: float,
        message: str,
        timestamp: str = "",
        **kwargs,
    ):
        self.stage = stage
        self.progress = progress
        self.message = message
        self.timestamp = timestamp or datetime.now().isoformat()

        # Stage-specific data
        self.config: Optional[Dict[str, Any]] = kwargs.get("config")
        self.topic_analysis: Optional[Dict[str, Any]] = kwargs.get("topic_analysis")
        self.plan: Optional[Dict[str, Any]] = kwargs.get("plan")
        self.raw_records: List[Dict[str, Any]] = kwargs.get("raw_records", [])
        self.selected_records: List[Dict[str, Any]] = kwargs.get("selected_records", [])
        self.evidence_bank: Optional[Dict[str, Any]] = kwargs.get("evidence_bank")
        self.draft: Optional[str] = kwargs.get("draft")
        self.final_draft: Optional[str] = kwargs.get("final_draft")
        self.review_reports: List[Dict[str, Any]] = kwargs.get("review_reports", [])
        self.validation: Optional[Dict[str, Any]] = kwargs.get("validation")
        self.token_usage: Optional[Dict[str, Any]] = kwargs.get("token_usage")
        self.error: Optional[str] = kwargs.get("error")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "timestamp": self.timestamp,
            "config": self.config,
            "topic_analysis": self.topic_analysis,
            "plan": self.plan,
            "raw_records": self.raw_records,
            "selected_records": self.selected_records,
            "evidence_bank": self.evidence_bank,
            "draft": self.draft,
            "final_draft": self.final_draft,
            "review_reports": self.review_reports,
            "validation": self.validation,
            "token_usage": self.token_usage,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointData":
        """Create from dictionary."""
        return cls(**data)


class CheckpointManager:
    """Manager for saving and loading workflow checkpoints."""

    CHECKPOINT_FILE = "checkpoint.json"
    LATEST_LINK = "latest_checkpoint.json"

    def __init__(self, output_dir: Path):
        """Initialize the checkpoint manager.

        Args:
            output_dir: Directory to store checkpoints.
        """
        self.output_dir = output_dir
        self.checkpoint_path = output_dir / self.CHECKPOINT_FILE

    def save(self, checkpoint: CheckpointData) -> None:
        """Save a checkpoint.

        Args:
            checkpoint: Checkpoint data to save.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint.timestamp = datetime.now().isoformat()

        # Convert to dict and ensure all values are JSON-serializable
        data = checkpoint.to_dict()
        data = self._make_jsonable(data)

        # Save main checkpoint
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Also save a timestamped backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.output_dir / f"checkpoint_{checkpoint.stage}_{timestamp}.json"
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"[Checkpoint] Saved at stage '{checkpoint.stage}' with progress {checkpoint.progress:.1%}")

    def _make_jsonable(self, obj: Any) -> Any:
        """Recursively convert objects to JSON-serializable format."""
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, dict):
            return {k: self._make_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._make_jsonable(item) for item in obj]
        return obj

    def load(self) -> Optional[CheckpointData]:
        """Load the latest checkpoint.

        Returns:
            Checkpoint data if exists, None otherwise.
        """
        if not self.checkpoint_path.exists():
            return None

        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            checkpoint = CheckpointData.from_dict(data)
            print(f"[Checkpoint] Loaded from stage '{checkpoint.stage}' with progress {checkpoint.progress:.1%}")
            return checkpoint
        except Exception as e:
            print(f"[Checkpoint] Failed to load: {e}")
            return None

    def exists(self) -> bool:
        """Check if a checkpoint exists."""
        return self.checkpoint_path.exists()

    def get_stage(self) -> Optional[str]:
        """Get the current stage from checkpoint.

        Returns:
            Stage name if checkpoint exists, None otherwise.
        """
        checkpoint = self.load()
        return checkpoint.stage if checkpoint else None

    def clear(self) -> None:
        """Clear the checkpoint."""
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()
            print("[Checkpoint] Cleared")

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all checkpoint backups.

        Returns:
            List of checkpoint info dictionaries.
        """
        checkpoints = []
        for path in sorted(self.output_dir.glob("checkpoint_*.json"), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                checkpoints.append({
                    "path": str(path),
                    "stage": data.get("stage"),
                    "progress": data.get("progress"),
                    "timestamp": data.get("timestamp"),
                    "message": data.get("message"),
                })
            except Exception:
                continue
        return checkpoints

    def load_from_path(self, path: Path) -> Optional[CheckpointData]:
        """Load a specific checkpoint file.

        Args:
            path: Path to the checkpoint file.

        Returns:
            Checkpoint data if valid, None otherwise.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CheckpointData.from_dict(data)
        except Exception as e:
            print(f"[Checkpoint] Failed to load from {path}: {e}")
            return None


class ResumableWorkflowRunner:
    """Workflow runner with checkpoint/resume support."""

    def __init__(
        self,
        checkpoint_manager: CheckpointManager,
        progress_callback: Optional[Any] = None,
    ):
        """Initialize the resumable runner.

        Args:
            checkpoint_manager: Checkpoint manager instance.
            progress_callback: Optional progress callback.
        """
        self.checkpoint_manager = checkpoint_manager
        self.progress_callback = progress_callback

    def should_resume(self) -> bool:
        """Check if there's a checkpoint to resume from."""
        return self.checkpoint_manager.exists()

    def get_resume_stage(self) -> Optional[str]:
        """Get the stage to resume from."""
        checkpoint = self.checkpoint_manager.load()
        return checkpoint.stage if checkpoint else None

    def determine_resume_point(self, checkpoint: CheckpointData) -> str:
        """Determine where to resume based on checkpoint data.

        Args:
            checkpoint: The loaded checkpoint.

        Returns:
            The stage to resume from.
        """
        # Check what data is available to determine resume point
        if checkpoint.final_draft and checkpoint.validation:
            # Everything is done
            return CheckpointStage.COMPLETE
        elif checkpoint.draft and checkpoint.evidence_bank:
            # Can resume from review
            return CheckpointStage.REVIEW
        elif checkpoint.evidence_bank and checkpoint.selected_records:
            # Can resume from writing
            return CheckpointStage.WRITING
        elif checkpoint.selected_records and checkpoint.plan:
            # Can resume from analysis
            return CheckpointStage.ANALYSIS
        elif checkpoint.raw_records and checkpoint.plan:
            # Can resume from screening
            return CheckpointStage.SCREENING
        elif checkpoint.plan:
            # Can resume from retrieval
            return CheckpointStage.RETRIEVAL
        elif checkpoint.config and checkpoint.topic_analysis:
            # Can resume from planning
            return CheckpointStage.PLANNING
        else:
            # Start from beginning
            return CheckpointStage.INIT

    def create_stage_checkpoint(
        self,
        stage: str,
        progress: float,
        message: str = "",
        **kwargs,
    ) -> CheckpointData:
        """Create a checkpoint for a stage.

        Args:
            stage: Current stage.
            progress: Progress percentage.
            message: Status message.
            **kwargs: Additional data.

        Returns:
            Checkpoint data.
        """
        checkpoint = CheckpointData(
            stage=stage,
            progress=progress,
            message=message,
            **kwargs,
        )
        return checkpoint

    def save_checkpoint(self, stage: str, progress: float, message: str = "", **kwargs) -> None:
        """Save a checkpoint.

        Args:
            stage: Current stage.
            progress: Progress percentage.
            message: Status message.
            **kwargs: Additional data to save.
        """
        checkpoint = self.create_stage_checkpoint(stage, progress, message, **kwargs)
        self.checkpoint_manager.save(checkpoint)

        if self.progress_callback:
            self.progress_callback(stage, message, progress)


def restore_records(records_data: List[Dict[str, Any]]) -> List[PaperRecord]:
    """Restore PaperRecord objects from saved data.

    Args:
        records_data: List of record dictionaries.

    Returns:
        List of PaperRecord objects.
    """
    return [PaperRecord(**r) for r in records_data]


def restore_evidence_bank(evidence_data: Dict[str, Any]) -> Dict[str, Any]:
    """Restore evidence bank from saved data.

    Args:
        evidence_data: Evidence bank dictionary.

    Returns:
        Restored evidence bank.
    """
    # Convert selected_records back to PaperRecord objects if needed
    if "selected_records" in evidence_data:
        evidence_data["selected_records"] = [
            PaperRecord(**r) if isinstance(r, dict) else r
            for r in evidence_data["selected_records"]
        ]
    return evidence_data


def restore_config(config_data: Dict[str, Any]) -> RunConfig:
    """Restore RunConfig from saved data.

    Args:
        config_data: Config dictionary.

    Returns:
        RunConfig object.
    """
    # Handle Path conversions
    if "output_path" in config_data:
        config_data["output_path"] = Path(config_data["output_path"])
    if "output_dir" in config_data:
        config_data["output_dir"] = Path(config_data["output_dir"])
    return RunConfig(**config_data)
