"""Factory for the active text-to-speech provider.

All clients expose the same context/queue interface, so the agent can switch
between Inworld TTS 2, Speechify Simba 3.5 and Sarvam Bulbul v4 Flash at runtime without changing its
turn orchestration.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from backend.config import RuntimeConfig, Settings
from backend.services.inworld_tts import InworldTTS
from backend.services.sarvam_tts import SarvamTTS
from backend.services.speechify_tts import SpeechifyTTS

TTS_PROVIDER_LABELS = {
    "inworld": "Inworld TTS 2",
    "speechify": "Speechify Simba 3.5",
    "sarvam": "Sarvam Bulbul v4 Flash",
}


def provider_label(provider: str) -> str:
    return TTS_PROVIDER_LABELS.get(provider, provider)


def build_tts(
    settings: Settings,
    config: RuntimeConfig,
    *,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> InworldTTS | SpeechifyTTS | SarvamTTS:
    if config.tts_provider == "sarvam":
        return SarvamTTS(
            settings.sarvam_api_key,
            model=config.sarvam_tts_model,
            voice_id=config.voice_id,
            language=settings.sarvam_tts_language,
            pace=settings.sarvam_tts_pace,
            on_event=on_event,
        )
    if config.tts_provider == "speechify":
        return SpeechifyTTS(
            settings.speechify_api_key,
            base_url=settings.speechify_base_url,
            model=config.speechify_model,
            voice_id=config.voice_id,
            language=settings.speechify_language,
            output_format=settings.speechify_output_format,
            sample_rate=8000,
            on_event=on_event,
        )
    return InworldTTS(
        settings.inworld_api_key,
        model=config.inworld_model,
        voice_id=config.voice_id,
        encoding=settings.inworld_audio_encoding,
        sample_rate=settings.inworld_sample_rate,
        speaking_rate=config.speaking_rate,
        delivery_mode=config.delivery_mode,
        flush_per_chunk=settings.inworld_flush_per_chunk,
        on_event=on_event,
    )
