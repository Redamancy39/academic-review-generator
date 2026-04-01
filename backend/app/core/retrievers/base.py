# Base retriever interface
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Sequence

from ..models import PaperRecord, RunConfig


class BaseRetriever(ABC):
    """Abstract base class for literature retrievers."""

    def __init__(self, config: RunConfig, session: Any = None) -> None:
        self.config = config
        self.session = session

    @abstractmethod
    def fetch(self, queries: Sequence[Dict[str, Any]]) -> List[PaperRecord]:
        """Fetch papers based on queries.

        Args:
            queries: List of query definitions with 'query', 'intent', 'priority' fields.

        Returns:
            List of PaperRecord objects.
        """
        pass

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the name of this data source."""
        pass
