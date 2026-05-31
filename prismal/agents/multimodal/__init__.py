"""Multimodal agents package (Fase F) — vision/audio/video + router + fusion."""

from prismal.agents.multimodal.audio_agent import AudioAgent, AudioResult
from prismal.agents.multimodal.ingestion import cleanup_session_media, ingest_media
from prismal.agents.multimodal.modality_router import (
    Modality,
    ModalityClassification,
    classify_modality,
    make_modality_router_node,
)
from prismal.agents.multimodal.multimodal_fusion import (
    FusionResult,
    ModalContribution,
    MultimodalFusion,
)
from prismal.agents.multimodal.video_agent import (
    FrameDescription,
    VideoAgent,
    VideoResult,
)
from prismal.agents.multimodal.vision_agent import (
    DetectedObject,
    VisionAgent,
    VisionResult,
)

__all__ = [
    "AudioAgent",
    "AudioResult",
    "DetectedObject",
    "FrameDescription",
    "FusionResult",
    "ModalContribution",
    "Modality",
    "ModalityClassification",
    "MultimodalFusion",
    "VideoAgent",
    "VideoResult",
    "VisionAgent",
    "VisionResult",
    "classify_modality",
    "cleanup_session_media",
    "ingest_media",
    "make_modality_router_node",
]
