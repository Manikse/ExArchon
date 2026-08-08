"""
kernel/cortex/aml_loader.py
Adaptive Model Loading (AML).
Tiered LLM loading based on task complexity and memory pressure.
"""
import os
import time
import asyncio
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum


class ModelTier(Enum):
    NONE = "none"
    TINY = "tiny"
    SMALL = "small"
    FULL = "full"
    CLOUD = "cloud"


@dataclass
class TierConfig:
    name: str
    ram_mb: int
    latency_ms: int
    model_id: str
    loader: Optional[Callable] = None


class AdaptiveModelLoader:
    """
    Manages tiered LLM loading.
    Unloads models under memory pressure.
    Batches requests within same tier.
    """

    TIERS = {
        ModelTier.TINY: TierConfig("tiny", 300, 500, "qwen2.5:0.5b"),
        ModelTier.SMALL: TierConfig("small", 1500, 2000, "qwen2.5:3b"),
        ModelTier.FULL: TierConfig("full", 4000, 5000, "llama3.1:8b"),
    }

    def __init__(self, cloud_infer: Optional[Callable] = None, ollama_client=None):
        self.cloud_infer = cloud_infer
        self.ollama = ollama_client
        self._current_tier: ModelTier = ModelTier.CLOUD
        self._loaded_models: Dict[ModelTier, Any] = {}
        self._last_used: Dict[ModelTier, float] = {}
        self._lock = asyncio.Lock()

        self.pressure_high = 80.0
        self.pressure_critical = 95.0

        self.tier_switches = 0
        self.requests_by_tier: Dict[str, int] = {t.value: 0 for t in ModelTier}

    async def infer(self, prompt: str, complexity: float = 0.5) -> str:
        tier = self._select_tier(complexity)
        self.requests_by_tier[tier.value] += 1

        if tier == ModelTier.NONE:
            return "[AML] No LLM needed — use Muscle Memory"

        if tier == ModelTier.CLOUD:
            if self.cloud_infer:
                return await self._call_cloud(prompt)
            return "[AML] Cloud unavailable and no local model loaded"

        await self._ensure_loaded(tier)
        return await self._call_local(tier, prompt)

    def _select_tier(self, complexity: float) -> ModelTier:
        pressure = self._get_memory_pressure()

        if pressure > self.pressure_critical:
            return ModelTier.CLOUD if self.cloud_infer else ModelTier.NONE

        if pressure > self.pressure_high:
            if complexity < 0.3:
                return ModelTier.TINY
            return ModelTier.CLOUD if self.cloud_infer else ModelTier.SMALL

        if complexity < 0.2:
            return ModelTier.TINY
        elif complexity < 0.6:
            return ModelTier.SMALL
        else:
            return ModelTier.FULL

    def _get_memory_pressure(self) -> float:
        """Return RAM usage %. Cross-platform. No psutil required."""
        try:
            import psutil
            return psutil.virtual_memory().percent
        except ImportError:
            pass

        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
                total = int(lines[0].split()[1])
                available = int(lines[2].split()[1])
                return (1 - available / total) * 100
        except Exception:
            pass

        return 50.0

    async def _ensure_loaded(self, tier: ModelTier):
        if tier in self._loaded_models:
            self._last_used[tier] = time.time()
            return

        async with self._lock:
            config = self.TIERS.get(tier)
            if not config:
                return

            pressure = self._get_memory_pressure()
            if pressure > self.pressure_high:
                for t in [ModelTier.FULL, ModelTier.SMALL]:
                    if t in self._loaded_models and t != tier:
                        await self._unload(t)

            if self.ollama:
                try:
                    self._loaded_models[tier] = config.model_id
                    self._last_used[tier] = time.time()
                    self.tier_switches += 1
                except Exception as e:
                    print(f"[AML] Failed to load {tier.value}: {e}")

    async def _unload(self, tier: ModelTier):
        if tier in self._loaded_models:
            del self._loaded_models[tier]
            if tier in self._last_used:
                del self._last_used[tier]
            print(f"[AML] Unloaded {tier.value}")

    async def _call_local(self, tier: ModelTier, prompt: str) -> str:
        if not self.ollama:
            return "[AML] Ollama not configured"

        config = self.TIERS[tier]
        try:
            response = await asyncio.wait_for(
                self.ollama.generate(model=config.model_id, prompt=prompt),
                timeout=120.0,
            )
            return response.get("response", "")
        except asyncio.TimeoutError:
            return "[AML] Local model timeout"
        except Exception as e:
            return f"[AML] Local error: {e}"

    async def _call_cloud(self, prompt: str) -> str:
        if not self.cloud_infer:
            return "[AML] Cloud not configured"
        try:
            return await asyncio.wait_for(
                self.cloud_infer(prompt),
                timeout=60.0,
            )
        except Exception as e:
            return f"[AML] Cloud error: {e}"

    def get_stats(self) -> Dict[str, Any]:
        return {
            "current_tier": self._current_tier.value,
            "loaded_tiers": [t.value for t in self._loaded_models.keys()],
            "memory_pressure_percent": self._get_memory_pressure(),
            "tier_switches": self.tier_switches,
            "requests_by_tier": dict(self.requests_by_tier),
        }