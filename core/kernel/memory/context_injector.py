"""
kernel/memory/context_injector.py
Context-Augmented Generation (CAG) for ExArchon.
Injects exact facts from HNMA into LLM prompts before generation.
Eliminates "lost in the middle" and hallucinations by providing
verified facts instead of relying on LLM memory.
"""
import time
import hashlib
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class InjectedContext:
    facts: List[str]
    summary: str
    latency_ms: float
    cached_at: float = 0.0


class ContextInjector:
    """
    CAG Engine: searches HNMA L2/L3 for relevant facts and
    prepends them to the LLM prompt as verified context.
    """

    def __init__(self, hnma_controller):
        self.hnma = hnma_controller
        self._cache: Dict[str, InjectedContext] = {}
        self._cache_ttl = 60.0

    def inject(self, user_prompt: str, max_facts: int = 5) -> str:
        """
        Build augmented prompt with verified facts from HNMA.
        Returns the original prompt if no facts found.
        """
        start = time.perf_counter()
        cache_key = hashlib.sha256(user_prompt.encode()).hexdigest()[:16]

        # Check cache — use cached_at timestamp, not latency_ms
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached.cached_at) < self._cache_ttl:
            return self._build_prompt(user_prompt, cached.facts)

        facts = []

        # 1. Search L2 Working RAM (LRU cache) — O(1) per entry
        try:
            for key_hex, entry in list(self.hnma.l2_cache.items())[-100:]:
                score = self._relevance_score(user_prompt, entry.name, entry.description)
                if score >= 0.6:
                    facts.append(f"skill:{entry.name}={entry.description[:120]}")
                if len(facts) >= max_facts:
                    break
        except Exception:
            pass

        # 2. Search L3 Episodic (SQLite) — O(log N) via FTS
        if len(facts) < max_facts:
            try:
                rows = self.hnma.query_episodic(user_prompt, limit=max_facts - len(facts))
                for r in rows:
                    facts.append(f"memory:{r.get('summary', '')[:120]}")
            except Exception:
                pass

        latency_ms = (time.perf_counter() - start) * 1000
        self._cache[cache_key] = InjectedContext(facts, "", latency_ms, time.time())

        return self._build_prompt(user_prompt, facts)

    def _relevance_score(self, query: str, name: str, description: str) -> float:
        """Simple keyword overlap scoring."""
        q = query.lower()
        n = (name or "").lower()
        d = (description or "").lower()

        if q == n:
            return 2.0
        if q in n or q in d:
            return 1.0

        q_words = set(q.split())
        n_words = set(n.split())
        d_words = set(d.split())
        overlap = len(q_words & n_words) + len(q_words & d_words) * 0.5
        if overlap > 0:
            return overlap / len(q_words)
        return 0.0

    def _build_prompt(self, user_prompt: str, facts: List[str]) -> str:
        if not facts:
            return user_prompt
        context_block = "\n".join(f"- {f}" for f in facts)
        return (
            f"[SYSTEM CONTEXT — verified facts from ExArchon memory]\n"
            f"{context_block}\n"
            f"[/SYSTEM CONTEXT]\n\n"
            f"{user_prompt}"
        )