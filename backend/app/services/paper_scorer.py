# Paper Scorer Service - semantic similarity scoring
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from ..core.models import PaperRecord, RunConfig


class PaperScorer:
    """Service for computing enhanced paper relevance scores using semantic similarity."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_model: str = "text-embedding-v3",
    ) -> None:
        """Initialize the paper scorer.

        Args:
            api_key: DashScope API key. If not provided, will read from environment.
            base_url: DashScope API base URL.
            embedding_model: Embedding model to use.
        """
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = base_url
        self.embedding_model = embedding_model
        self._client = None

    def _get_client(self) -> Any:
        """Get or create the OpenAI client for DashScope."""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            except ImportError:
                raise RuntimeError("openai not installed. Run: pip install openai")
        return self._client

    def get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding vector for text.

        Args:
            text: Text to embed.

        Returns:
            Numpy array of embedding vector, or None if failed.
        """
        if not text or not text.strip():
            return None

        try:
            client = self._get_client()
            response = client.embeddings.create(
                model=self.embedding_model,
                input=text[:8000],  # Limit text length
            )
            embedding = response.data[0].embedding
            return np.array(embedding, dtype=np.float32)
        except Exception as e:
            print(f"Warning: Failed to get embedding: {e}")
            return None

    def get_batch_embeddings(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """Get embeddings for multiple texts.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors (or None for failed items).
        """
        if not texts:
            return []

        # Filter out empty texts
        valid_texts = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
        if not valid_texts:
            return [None] * len(texts)

        results: List[Optional[np.ndarray]] = [None] * len(texts)

        try:
            client = self._get_client()
            # Process in batches of 20 to avoid rate limits
            batch_size = 20
            for batch_start in range(0, len(valid_texts), batch_size):
                batch = valid_texts[batch_start:batch_start + batch_size]
                batch_texts = [t[:8000] for _, t in batch]

                try:
                    response = client.embeddings.create(
                        model=self.embedding_model,
                        input=batch_texts,
                    )
                    for j, (orig_idx, _) in enumerate(batch):
                        embedding = response.data[j].embedding
                        results[orig_idx] = np.array(embedding, dtype=np.float32)
                except Exception as e:
                    print(f"Warning: Batch embedding failed: {e}")
                    # Fall back to individual requests
                    for orig_idx, text in batch:
                        results[orig_idx] = self.get_embedding(text)

        except Exception as e:
            print(f"Warning: Batch embedding failed: {e}")

        return results

    def cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two vectors.

        Args:
            vec1: First vector.
            vec2: Second vector.

        Returns:
            Cosine similarity score.
        """
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    def compute_topic_embedding(self, topic: str, keywords: List[str]) -> Optional[np.ndarray]:
        """Compute a combined embedding for topic and keywords.

        Args:
            topic: Main topic string.
            keywords: List of related keywords.

        Returns:
            Combined embedding vector.
        """
        # Combine topic with top keywords
        combined_text = f"{topic}. {' '.join(keywords[:10])}"
        return self.get_embedding(combined_text)

    def enhanced_score(
        self,
        record: PaperRecord,
        topic_embedding: np.ndarray,
        config: RunConfig,
        keyword_score: float = 0.0,
        semantic_weight: float = 0.4,
    ) -> float:
        """Compute enhanced relevance score combining keyword matching and semantic similarity.

        Args:
            record: Paper record to score.
            topic_embedding: Pre-computed topic embedding.
            config: Run configuration.
            keyword_score: Existing keyword-based score.
            semantic_weight: Weight for semantic similarity (0-1).

        Returns:
            Combined relevance score.
        """
        # Get paper embedding
        paper_text = f"{record.title} {(record.abstract or '')[:500]}"
        paper_embedding = self.get_embedding(paper_text)

        if paper_embedding is None:
            # Fall back to keyword score
            return keyword_score

        # Compute semantic similarity
        semantic_score = self.cosine_similarity(topic_embedding, paper_embedding)

        # Normalize to 0-10 scale
        semantic_score_normalized = semantic_score * 10

        # Combine scores
        keyword_weight = 1 - semantic_weight
        combined_score = (
            keyword_weight * keyword_score +
            semantic_weight * semantic_score_normalized
        )

        # Apply bonuses/penalties
        # Recent paper bonus
        from datetime import datetime
        current_year = datetime.now().year
        if record.year and record.year >= current_year - 2:
            combined_score *= 1.1  # 10% bonus for very recent papers

        # High-tier journal bonus
        if record.is_high_tier:
            combined_score *= 1.05  # 5% bonus

        # High citation bonus
        if record.times_cited >= 100:
            combined_score *= 1.1
        elif record.times_cited >= 50:
            combined_score *= 1.05

        return round(combined_score, 2)

    def batch_enhanced_scores(
        self,
        records: Sequence[PaperRecord],
        topic: str,
        keywords: List[str],
        config: RunConfig,
        keyword_scores: Optional[List[float]] = None,
    ) -> List[float]:
        """Compute enhanced scores for multiple papers efficiently.

        Args:
            records: List of paper records.
            topic: Main topic.
            keywords: List of keywords.
            config: Run configuration.
            keyword_scores: Optional pre-computed keyword scores.

        Returns:
            List of enhanced relevance scores.
        """
        if not records:
            return []

        # Compute topic embedding once
        topic_embedding = self.compute_topic_embedding(topic, keywords)
        if topic_embedding is None:
            # Fall back to keyword scores
            return keyword_scores or [0.0] * len(records)

        # Compute paper embeddings in batch
        paper_texts = [
            f"{r.title} {(r.abstract or '')[:500]}"
            for r in records
        ]
        paper_embeddings = self.get_batch_embeddings(paper_texts)

        # Compute scores
        scores = []
        for i, record in enumerate(records):
            if keyword_scores and i < len(keyword_scores):
                kw_score = keyword_scores[i]
            else:
                kw_score = record.relevance_score

            if paper_embeddings[i] is not None:
                semantic_score = self.cosine_similarity(topic_embedding, paper_embeddings[i])
                semantic_score_normalized = semantic_score * 10

                # Combine
                semantic_weight = 0.4
                combined_score = (1 - semantic_weight) * kw_score + semantic_weight * semantic_score_normalized

                # Apply bonuses
                from datetime import datetime
                current_year = datetime.now().year
                if record.year and record.year >= current_year - 2:
                    combined_score *= 1.1
                if record.is_high_tier:
                    combined_score *= 1.05
                if record.times_cited >= 100:
                    combined_score *= 1.1
                elif record.times_cited >= 50:
                    combined_score *= 1.05

                scores.append(round(combined_score, 2))
            else:
                scores.append(kw_score)

        return scores


def enhance_paper_scores(
    records: List[PaperRecord],
    topic: str,
    keywords: List[str],
    config: RunConfig,
    api_key: Optional[str] = None,
) -> List[PaperRecord]:
    """Enhance paper scores with semantic similarity.

    This is a convenience function that updates records in place.

    Args:
        records: List of paper records to enhance.
        topic: Main topic.
        keywords: List of keywords.
        config: Run configuration.
        api_key: Optional API key.

    Returns:
        The same list of records with updated relevance scores.
    """
    if not records:
        return records

    try:
        scorer = PaperScorer(api_key=api_key)

        # Get existing scores
        existing_scores = [r.relevance_score for r in records]

        # Compute enhanced scores
        enhanced_scores = scorer.batch_enhanced_scores(
            records=records,
            topic=topic,
            keywords=keywords,
            config=config,
            keyword_scores=existing_scores,
        )

        # Update records
        for record, score in zip(records, enhanced_scores):
            record.relevance_score = score

        # Re-sort
        records.sort(key=lambda r: r.relevance_score, reverse=True)

    except Exception as e:
        print(f"Warning: Failed to enhance scores: {e}")

    return records
