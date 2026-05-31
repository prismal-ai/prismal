"""
Prismal providers package — LiteLLM-backed LLM registry + multimodal wrappers.

Public API::

    from prismal.providers import ProviderRegistry, ModelInfo, TokenUsage
    from prismal.providers import get_stt, get_tts, get_vision_llm
    from prismal.providers import get_multimodal_llm, get_cross_modal_embeddings
"""

from prismal.providers.cross_modal_embeddings import (
    CLIPEmbeddings,
    get_cross_modal_embeddings,
)
from prismal.providers.multimodal import get_multimodal_llm
from prismal.providers.registry import ModelInfo, ProviderRegistry, TokenUsage
from prismal.providers.stt import (
    STTClient,
    STTProvider,
    STTResult,
    STTSegment,
    get_stt,
)
from prismal.providers.tts import TTSClient, TTSProvider, TTSResult, get_tts
from prismal.providers.vision import get_vision_llm

__all__ = [
    "CLIPEmbeddings",
    "ModelInfo",
    "ProviderRegistry",
    "STTClient",
    "STTProvider",
    "STTResult",
    "STTSegment",
    "TTSClient",
    "TTSProvider",
    "TTSResult",
    "TokenUsage",
    "get_cross_modal_embeddings",
    "get_multimodal_llm",
    "get_stt",
    "get_tts",
    "get_vision_llm",
]
