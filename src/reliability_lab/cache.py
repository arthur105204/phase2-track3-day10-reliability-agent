from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Shared utilities — use these in both ResponseCache and SharedRedisCache
# ---------------------------------------------------------------------------

PRIVACY_PATTERNS = re.compile(
    r"\b(balance|password|credit.card|ssn|social.security|user.\d+|account.\d+)\b",
    re.IGNORECASE,
)


def _is_uncacheable(query: str) -> bool:
    """Return True if query contains privacy-sensitive keywords."""
    return bool(PRIVACY_PATTERNS.search(query))


def _looks_like_false_hit(query: str, cached_key: str) -> bool:
    """Return True if query and cached key contain different 4-digit numbers (years, IDs)."""
    nums_q = set(re.findall(r"\b\d{4}\b", query))
    nums_c = set(re.findall(r"\b\d{4}\b", cached_key))
    return bool(nums_q and nums_c and nums_q != nums_c)


# ---------------------------------------------------------------------------
# In-memory cache (existing)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    key: str
    value: str
    created_at: float
    metadata: dict[str, str]


class ResponseCache:
    """Simple in-memory cache skeleton.

    TODO(student): Add a better semantic similarity function and false-hit guardrails.
    Use the module-level _is_uncacheable() and _looks_like_false_hit() helpers in your
    get() and set() methods.  For production, replace with SharedRedisCache.
    """

    def __init__(self, ttl_seconds: int, similarity_threshold: float):
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._entries: list[CacheEntry] = []
        self.false_hit_log: list[dict[str, object]] = []

    def get(self, query: str) -> tuple[str | None, float]:
        # Return None if query contains privacy-sensitive keywords
        if _is_uncacheable(query):
            return None, 0.0
        
        best_value: str | None = None
        best_score = 0.0
        best_key: str | None = None
        now = time.time()
        self._entries = [e for e in self._entries if now - e.created_at <= self.ttl_seconds]
        for entry in self._entries:
            score = self.similarity(query, entry.key)
            if score > best_score:
                best_score = score
                best_value = entry.value
                best_key = entry.key
        
        if best_score >= self.similarity_threshold:
            # Check for false hits (e.g., different years)
            if best_key and _looks_like_false_hit(query, best_key):
                self.false_hit_log.append({
                    "query": query,
                    "cached_key": best_key,
                    "score": best_score,
                    "reason": "different_4digit_numbers"
                })
                return None, best_score
            return best_value, best_score
        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        # Don't cache privacy-sensitive queries
        if _is_uncacheable(query):
            return
        self._entries.append(CacheEntry(query, value, time.time(), metadata or {}))

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Improved similarity using weighted token overlap and prefix matching.

        Factors:
        - Token overlap: high weight for exact matches
        - Length penalty: penalize very different query lengths
        - Common words penalty: reduce weight of very common words
        """
        left = set(a.lower().split())
        right = set(b.lower().split())
        if not left or not right:
            return 0.0
        
        # Basic Jaccard similarity
        intersection = len(left & right)
        union = len(left | right)
        jaccard = intersection / union if union > 0 else 0.0
        
        # Length penalty: penalize if one query is much shorter/longer than the other
        len_ratio = min(len(a), len(b)) / max(len(a), len(b)) if max(len(a), len(b)) > 0 else 0.0
        
        # Combined score: weight Jaccard heavily but apply length penalty
        combined = jaccard * 0.8 + len_ratio * 0.2
        return combined


# ---------------------------------------------------------------------------
# Redis shared cache (new)
# ---------------------------------------------------------------------------


class SharedRedisCache:
    """Redis-backed shared cache for multi-instance deployments.

    TODO(student): Implement the get() and set() methods using Redis commands
    so that cache state is shared across multiple gateway instances.

    Data model (suggested):
        Key    = "{prefix}{query_hash}"   (Redis String namespace)
        Value  = Redis Hash with fields:  "query", "response"
        TTL    = Redis EXPIRE (automatic cleanup — no manual eviction)

    For similarity lookup: SCAN all keys with self.prefix, HGET each entry's
    "query" field, compute similarity locally via ResponseCache.similarity().

    Provided helpers:
        _is_uncacheable(query)          — True if privacy-sensitive
        _looks_like_false_hit(q, key)   — True if 4-digit numbers differ
        self._query_hash(query)         — deterministic short hash for Redis key
        ResponseCache.similarity(a, b)  — reuse your improved similarity function
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        similarity_threshold: float,
        prefix: str = "rl:cache:",
    ):
        import redis as redis_lib

        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.prefix = prefix
        self.false_hit_log: list[dict[str, object]] = []
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response from Redis.

        TODO(student): Implement cache lookup.  Suggested steps:
        1. Return (None, 0.0) if _is_uncacheable(query)
        2. Build exact-match key: f"{self.prefix}{self._query_hash(query)}"
        3. Try self._redis.hget(key, "response") — if found return (response, 1.0)
        4. Otherwise self._redis.scan_iter(f"{self.prefix}*") to iterate all cached keys
        5. For each key, HGET "query" field and compute
           ResponseCache.similarity(query, cached_query)
        6. Track best match that is >= self.similarity_threshold
        7. Before returning a match, check _looks_like_false_hit(); if true,
           append to self.false_hit_log and return (None, best_score)
        """
        # Return None if query contains privacy-sensitive keywords
        if _is_uncacheable(query):
            return None, 0.0
        
        # Try exact match first
        exact_key = f"{self.prefix}{self._query_hash(query)}"
        try:
            cached_response = self._redis.hget(exact_key, "response")
            if cached_response is not None:
                return cached_response, 1.0
        except Exception:
            pass
        
        # Search for semantic matches across all cached keys
        best_value: str | None = None
        best_score = 0.0
        best_key: str | None = None
        best_cached_query: str | None = None
        
        try:
            for redis_key in self._redis.scan_iter(f"{self.prefix}*"):
                cached_query_data = self._redis.hget(redis_key, "query")
                if cached_query_data is None:
                    continue
                
                score = ResponseCache.similarity(query, cached_query_data)
                if score > best_score:
                    best_score = score
                    best_value = self._redis.hget(redis_key, "response")
                    best_key = redis_key
                    best_cached_query = cached_query_data
        except Exception:
            pass
        
        if best_score >= self.similarity_threshold:
            # Check for false hits (e.g., different years)
            if best_cached_query and _looks_like_false_hit(query, best_cached_query):
                self.false_hit_log.append({
                    "query": query,
                    "cached_query": best_cached_query,
                    "score": best_score,
                    "reason": "different_4digit_numbers"
                })
                return None, best_score
            return best_value, best_score
        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in Redis with TTL.

        TODO(student): Implement cache storage.  Suggested steps:
        1. Return immediately if _is_uncacheable(query)
        2. Build key: f"{self.prefix}{self._query_hash(query)}"
        3. self._redis.hset(key, mapping={"query": query, "response": value})
        4. self._redis.expire(key, self.ttl_seconds)
        """
        # Don't cache privacy-sensitive queries
        if _is_uncacheable(query):
            return
        
        key = f"{self.prefix}{self._query_hash(query)}"
        try:
            self._redis.hset(key, mapping={"query": query, "response": value})
            self._redis.expire(key, self.ttl_seconds)
        except Exception:
            pass

    def flush(self) -> None:
        """Remove all entries with this cache prefix (for testing)."""
        for key in self._redis.scan_iter(f"{self.prefix}*"):
            self._redis.delete(key)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()

    @staticmethod
    def _query_hash(query: str) -> str:
        """Deterministic short hash for a query string."""
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
