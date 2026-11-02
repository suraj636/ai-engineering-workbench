"""Environment-backed configuration for the voice agent.

Every value can be overridden from `.env`.  Secrets are never exposed through
the public API; only presence/absence is reported to the dashboard.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pricing import (
    PRICING_AS_OF,
    PRICING_ASSUMPTIONS,
    PRICING_SOURCES,
    STATIC_RATES,
    USD_INR,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

load_dotenv(ROOT_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


def _int(name: str, default: int) -> int:
    raw = _str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float | None) -> float | None:
    raw = _str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _tts_provider() -> str:
    value = _str("TTS_PROVIDER", "sarvam").lower()
    return value if value in {"inworld", "speechify", "sarvam"} else "sarvam"


DEFAULT_SYSTEM_PROMPT = """\
# Role and Personality

You are Natalie, the AI receptionist for **Hardline Landscape & Construction**, a family-run business based in Rockingham, Western Australia, serving Perth and surrounding areas. Be friendly, professional, practical, and natural. Keep responses short (1–2 sentences), ask one question at a time, and be transparent if asked whether you are an AI.

# Business Services

- **Fencing:** Colorbond, timber pinelap, glass pool, aluminium, blade, perforated, batten, and slat infill.
- **Retaining walls:** Limestone, concrete post-and-panel, raised garden beds, and planter boxes.
- **Paving:** Wet-lay and dry-lay for pathways, outdoor areas, entertaining spaces, and pool surrounds.
- **Turf:** Real and artificial grass installation.
- **Reticulation:** Only as part of new turf installation or a wider landscaping project.

Hardline does not provide limestone fencing or standalone reticulation services.

# Indicative Starting Prices

All prices are estimates, exclude GST, and depend on project requirements and site conditions.

- Colorbond fencing: $135/linear metre
- Timber pinelap fencing: $320/linear metre
- Glass/blade pool fencing: $280/linear metre
- Aluminium pool fencing: $180/linear metre
- Limestone retaining walls: $120/linear metre plus preparation
- Post-and-panel retaining walls: $180/linear metre
- Wet-lay paving: $350/sqm
- Dry-lay paving: $250/sqm
- Real turf: $150/sqm
- Artificial turf: $150/sqm
- Slat infill: $350/sqm
- Raised garden beds/planter boxes: $65/box plus preparation

Paving and turf generally require a minimum of 40 sqm. Smaller projects can still be submitted for review. Never give guaranteed totals. Labour-only work, gates, automation, and technical assessments require a callback from Ricky or the team.

# Quotes and Site Visits

Email quotes based on submitted project details are **free**. Onsite consultations are paid, with fees based on distance from Rockingham:

- Within 10 km: $50
- Within 15 km: $75
- Within 20 km: $100
- Within 30 km: $150
- 30–50 km: $200

Do not suggest site visits by default. Explain the applicable fee and obtain agreement before booking. Ricky, one of the owners, handles site visits. Never invent appointment availability.

# Service Area

Hardline primarily serves Perth and surrounding areas. Ask for the caller's suburb and postcode. Regional enquiries may be considered depending on project scope and travel requirements; collect details for the team to review rather than rejecting them automatically.

# Caller Questions and Quote Collection

Answer questions about services, materials, turf options, paving methods, indicative prices, minimum project sizes, service areas, quote processes, and site visit fees.

For quote enquiries, conversationally collect:
- Name and preferred phone number
- Email address (spell it back character by character and confirm)
- Property address and suburb/postcode
- Service required and project description
- Approximate dimensions and material preferences
- Relevant site conditions

Confirm unclear details rather than guessing. For multiple services, capture each separately; separate quotes may be prepared where appropriate.

# Conversation Rules

1. Greet naturally: “Good morning, Hardline Landscape & Construction, this is Natalie. How can I help?”
2. Ask one question at a time and keep responses concise.
3. Never invent prices, policies, availability, or appointment slots.
4. Never promise callback times, quote turnaround, project start dates, or completion dates.
5. If unsure, explain that the team can follow up.
6. If the caller requests a human, has a complaint, or asks something you cannot resolve, offer to transfer them to Ricky.
7. Stay focused on Hardline's services.
8. End by summarising the next step and thanking the caller.

**Primary goal:** Help callers feel heard, answer accurately, collect the right project details, and guide them toward the next step.

# Voice Output

You are speaking on a live phone call. Reply in plain spoken sentences only: no markdown, asterisks, headings, bullet points, numbered lists, emojis or URLs. Say prices, numbers, dates and email addresses the way a person would say them aloud.
"""

# {salutation} becomes "Good morning/afternoon/evening" at call time (see render_greeting).
DEFAULT_GREETING = (
    "{salutation}, Hardline Landscape & Construction, this is Natalie. How can I help?"
)

# Hardline is in Rockingham, Western Australia: UTC+8, no daylight saving.
_BUSINESS_TZ = timezone(timedelta(hours=8))


def render_greeting(template: str, now: datetime | None = None) -> str:
    """Fill {salutation} using the business's local time of day."""
    hour = (now or datetime.now(_BUSINESS_TZ)).astimezone(_BUSINESS_TZ).hour
    salutation = (
        "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 17 else "Good evening"
    )
    return template.replace("{salutation}", salutation)


@dataclass
class Settings:
    # --- Plivo -------------------------------------------------------------
    plivo_auth_id: str = field(default_factory=lambda: _str("PLIVO_AUTH_ID"))
    plivo_auth_token: str = field(default_factory=lambda: _str("PLIVO_AUTH_TOKEN"))
    plivo_phone_number: str = field(default_factory=lambda: _str("PLIVO_PHONE_NUMBER"))
    public_base_url: str = field(default_factory=lambda: _str("PUBLIC_BASE_URL").rstrip("/"))
    plivo_validate_signature: bool = field(
        default_factory=lambda: _bool("PLIVO_VALIDATE_SIGNATURE", False)
    )

    # --- AssemblyAI --------------------------------------------------------
    assemblyai_api_key: str = field(default_factory=lambda: _str("ASSEMBLYAI_API_KEY"))
    assemblyai_speech_model: str = field(
        default_factory=lambda: _str("ASSEMBLYAI_SPEECH_MODEL", "universal-3-5-pro")
    )
    assemblyai_sample_rate: int = field(
        default_factory=lambda: _int("ASSEMBLYAI_SAMPLE_RATE", 16000)
    )
    assemblyai_mode: str = field(default_factory=lambda: _str("ASSEMBLYAI_MODE"))
    assemblyai_min_turn_silence: int | None = field(
        default_factory=lambda: _int("ASSEMBLYAI_MIN_TURN_SILENCE", 0) or None
    )
    assemblyai_max_turn_silence: int | None = field(
        default_factory=lambda: _int("ASSEMBLYAI_MAX_TURN_SILENCE", 0) or None
    )
    assemblyai_vad_threshold: float | None = field(
        default_factory=lambda: _float("ASSEMBLYAI_VAD_THRESHOLD", None)
    )
    assemblyai_interruption_delay: int | None = field(
        # 150 ms makes barge-in feel immediate; AssemblyAI's default is 400 ms.
        default_factory=lambda: _int("ASSEMBLYAI_INTERRUPTION_DELAY", 150) or None
    )

    # --- Sarvam ------------------------------------------------------------
    sarvam_api_key: str = field(default_factory=lambda: _str("SARVAM_API_KEY"))
    sarvam_model: str = field(default_factory=lambda: _str("SARVAM_MODEL", "sarvam-105b"))
    sarvam_base_url: str = field(
        default_factory=lambda: _str("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/")
    )
    sarvam_temperature: float = field(
        default_factory=lambda: _float("SARVAM_TEMPERATURE", 0.6) or 0.6
    )
    sarvam_max_tokens: int = field(default_factory=lambda: _int("SARVAM_MAX_TOKENS", 300))

    # --- Text-to-speech provider switch ------------------------------------
    tts_provider: str = field(default_factory=_tts_provider)

    # --- Inworld -----------------------------------------------------------
    inworld_api_key: str = field(default_factory=lambda: _str("INWORLD_API_KEY"))
    inworld_tts_model: str = field(
        default_factory=lambda: _str("INWORLD_TTS_MODEL", "inworld-tts-2")
    )
    inworld_voice_id: str = field(default_factory=lambda: _str("INWORLD_VOICE_ID", "Ashley"))
    inworld_audio_encoding: str = field(
        default_factory=lambda: _str("INWORLD_AUDIO_ENCODING", "MULAW").upper()
    )
    inworld_sample_rate: int = field(
        default_factory=lambda: _int("INWORLD_SAMPLE_RATE", 8000)
    )
    inworld_speaking_rate: float = field(
        default_factory=lambda: _float("INWORLD_SPEAKING_RATE", 1.0) or 1.0
    )
    # Manual mode: the agent asks Inworld to speak each chunk as it is sent,
    # as the Sarvam client does. Off keeps Inworld's SENTENCE_BOUNDARY auto mode,
    # which waits for the end of the first sentence before it starts speaking.
    inworld_flush_per_chunk: bool = field(
        default_factory=lambda: _bool("INWORLD_FLUSH_PER_CHUNK", True)
    )
    inworld_delivery_mode: str = field(
        default_factory=lambda: _str("INWORLD_DELIVERY_MODE", "BALANCED").upper()
    )

    # --- Speechify (Simba 3.5) ---------------------------------------------
    speechify_api_key: str = field(default_factory=lambda: _str("SPEECHIFY_API_KEY"))
    speechify_base_url: str = field(
        default_factory=lambda: _str("SPEECHIFY_BASE_URL", "https://api.speechify.ai").rstrip("/")
    )
    speechify_model: str = field(
        default_factory=lambda: _str("SPEECHIFY_MODEL", "simba-3.5")
    )
    speechify_voice_id: str = field(
        default_factory=lambda: _str("SPEECHIFY_VOICE_ID", "mihika")
    )
    speechify_language: str = field(
        default_factory=lambda: _str("SPEECHIFY_LANGUAGE", "en-IN")
    )
    speechify_output_format: str = field(
        default_factory=lambda: _str("SPEECHIFY_OUTPUT_FORMAT", "ulaw_8000").lower()
    )

    # --- Sarvam Bulbul TTS (uses SARVAM_API_KEY) ----------------------------
    sarvam_tts_model: str = field(
        default_factory=lambda: _str("SARVAM_TTS_MODEL", "bulbul:v4-flash")
    )
    sarvam_tts_voice_id: str = field(
        default_factory=lambda: _str("SARVAM_TTS_VOICE_ID", "simran_en_customer")
    )
    sarvam_tts_language: str = field(
        default_factory=lambda: _str("SARVAM_TTS_LANGUAGE", "en-IN")
    )
    sarvam_tts_pace: float = field(
        default_factory=lambda: _float("SARVAM_TTS_PACE", 1.0) or 1.0
    )

    # --- Agent behaviour ---------------------------------------------------
    agent_system_prompt: str = field(
        default_factory=lambda: _str("AGENT_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT
    )
    agent_greeting: str = field(
        default_factory=lambda: _str("AGENT_GREETING", DEFAULT_GREETING)
    )
    agent_max_history_messages: int = field(
        default_factory=lambda: _int("AGENT_MAX_HISTORY_MESSAGES", 20)
    )
    max_call_seconds: int = field(default_factory=lambda: _int("MAX_CALL_SECONDS", 900))
    idle_timeout_seconds: int = field(
        default_factory=lambda: _int("IDLE_TIMEOUT_SECONDS", 45)
    )

    # --- Pricing -----------------------------------------------------------
    # Defaults are official list prices captured in backend/pricing.py.
    # Override from .env only when your contract differs.
    pricing_asr_per_hour: float | None = field(
        default_factory=lambda: _float(
            "PRICING_ASR_PER_HOUR", STATIC_RATES["asr_per_hour"]
        )
    )
    pricing_llm_per_1m_generated_chars: float | None = field(
        default_factory=lambda: _float(
            "PRICING_LLM_PER_1M_GENERATED_CHARS",
            STATIC_RATES["llm_per_1m_generated_chars"],
        )
    )
    pricing_llm_input_per_1m_tokens: float = field(
        default_factory=lambda: _float(
            "PRICING_LLM_INPUT_PER_1M_TOKENS", STATIC_RATES["llm_input_per_1m_tokens"]
        )
    )
    pricing_llm_cached_per_1m_tokens: float = field(
        default_factory=lambda: _float(
            "PRICING_LLM_CACHED_PER_1M_TOKENS", STATIC_RATES["llm_cached_per_1m_tokens"]
        )
    )
    pricing_llm_output_per_1m_tokens: float = field(
        default_factory=lambda: _float(
            "PRICING_LLM_OUTPUT_PER_1M_TOKENS", STATIC_RATES["llm_output_per_1m_tokens"]
        )
    )
    pricing_tts_per_1m_chars: float | None = field(
        default_factory=lambda: _float(
            "PRICING_TTS_PER_1M_CHARS", STATIC_RATES["tts_per_1m_chars"]
        )
    )

    pricing_inworld_tts_2_per_1m_chars: float = field(
        default_factory=lambda: _float(
            "PRICING_INWORLD_TTS_2_PER_1M_CHARS", STATIC_RATES["inworld_tts_2_per_1m_chars"]
        )
    )
    pricing_inworld_tts_2_flash_per_1m_chars: float = field(
        default_factory=lambda: _float(
            "PRICING_INWORLD_TTS_2_FLASH_PER_1M_CHARS",
            STATIC_RATES["inworld_tts_2_flash_per_1m_chars"],
        )
    )
    pricing_sarvam_tts_per_1m_chars: float | None = field(
        default_factory=lambda: _float(
            "PRICING_SARVAM_TTS_PER_1M_CHARS", STATIC_RATES["sarvam_tts_per_1m_chars"]
        )
    )

    # --- Server ------------------------------------------------------------
    host: str = field(default_factory=lambda: _str("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())
    # pretty = one readable line per event; json = one JSON object per line.
    log_format: str = field(
        default_factory=lambda: "json" if _str("LOG_FORMAT", "pretty").lower() == "json" else "pretty"
    )
    # Include (trimmed) caller/agent text in the per-turn log line.
    log_transcripts: bool = field(default_factory=lambda: _bool("LOG_TRANSCRIPTS", False))
    # SQLite file holding finished calls for the Cost & latency page.
    db_path: str = field(
        default_factory=lambda: _str("DB_PATH", str(ROOT_DIR / "data" / "calls.db"))
    )
    warmup_on_start: bool = field(default_factory=lambda: _bool("WARMUP_ON_START", True))
    warmup_timeout_seconds: float = field(
        default_factory=lambda: _float("WARMUP_TIMEOUT_SECONDS", 20.0) or 20.0
    )

    # ----------------------------------------------------------------- API
    @property
    def plivo_configured(self) -> bool:
        return bool(self.plivo_auth_id and self.plivo_auth_token and self.plivo_phone_number)

    @property
    def assemblyai_configured(self) -> bool:
        return bool(self.assemblyai_api_key)

    @property
    def sarvam_configured(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def inworld_configured(self) -> bool:
        return bool(self.inworld_api_key)

    @property
    def speechify_configured(self) -> bool:
        return bool(self.speechify_api_key)

    @property
    def tts_configured(self) -> bool:
        return self.tts_configured_for(self.tts_provider)

    @property
    def missing_credentials(self) -> list[str]:
        missing: list[str] = []
        if not self.plivo_configured:
            missing.append("PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN / PLIVO_PHONE_NUMBER")
        if not self.assemblyai_configured:
            missing.append("ASSEMBLYAI_API_KEY")
        if not self.sarvam_configured:
            missing.append("SARVAM_API_KEY")
        if not self.tts_configured:
            missing.append(self.tts_key_name(self.tts_provider))
        return missing

    @property
    def voice_pipeline_ready(self) -> bool:
        return (
            self.assemblyai_configured
            and self.sarvam_configured
            and self.tts_configured
        )

    def tts_configured_for(self, provider: str) -> bool:
        if provider == "speechify":
            return self.speechify_configured
        if provider == "sarvam":
            return self.sarvam_configured
        return self.inworld_configured

    @staticmethod
    def tts_key_name(provider: str) -> str:
        return {
            "speechify": "SPEECHIFY_API_KEY",
            "sarvam": "SARVAM_API_KEY",
        }.get(provider, "INWORLD_API_KEY")

    def tts_rate(self, provider: str, model: str = "") -> float | None:
        """USD per 1M TTS characters for a provider (and model, for Inworld)."""
        if provider == "sarvam":
            return self.pricing_sarvam_tts_per_1m_chars
        if provider == "inworld":
            if "flash" in (model or "").lower():
                return self.pricing_inworld_tts_2_flash_per_1m_chars
            return self.pricing_inworld_tts_2_per_1m_chars
        return self.pricing_tts_per_1m_chars

    def missing_credentials_for(self, provider: str) -> list[str]:
        missing: list[str] = []
        if not self.plivo_configured:
            missing.append("PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN / PLIVO_PHONE_NUMBER")
        if not self.assemblyai_configured:
            missing.append("ASSEMBLYAI_API_KEY")
        if not self.sarvam_configured:
            missing.append("SARVAM_API_KEY")
        if not self.tts_configured_for(provider):
            missing.append(self.tts_key_name(provider))
        return missing

    def voice_pipeline_ready_for(self, provider: str) -> bool:
        return (
            self.assemblyai_configured
            and self.sarvam_configured
            and self.tts_configured_for(provider)
        )

    def public_base(self, request_base: str | None = None) -> str:
        """Public origin used in Plivo webhooks (falls back to the request)."""
        base = self.public_base_url or (request_base or "").rstrip("/")
        return base

    def ws_base(self, request_base: str | None = None) -> str:
        base = self.public_base(request_base)
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :]
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :]
        return base

    def pricing(self) -> dict[str, Any]:
        asr_hour = self.pricing_asr_per_hour
        return {
            "asr_per_hour": asr_hour,
            "asr_per_minute": (
                round(asr_hour / 60.0, 6) if asr_hour is not None else None
            ),
            "llm_per_1m_generated_chars": self.pricing_llm_per_1m_generated_chars,
            "llm_input_per_1m_tokens": self.pricing_llm_input_per_1m_tokens,
            "llm_cached_per_1m_tokens": self.pricing_llm_cached_per_1m_tokens,
            "llm_output_per_1m_tokens": self.pricing_llm_output_per_1m_tokens,
            "tts_per_1m_chars": self.pricing_tts_per_1m_chars,
            "sarvam_tts_per_1m_chars": self.pricing_sarvam_tts_per_1m_chars,
            "inworld_tts_2_per_1m_chars": self.pricing_inworld_tts_2_per_1m_chars,
            "inworld_tts_2_flash_per_1m_chars": self.pricing_inworld_tts_2_flash_per_1m_chars,
            "currency": "USD",
            "usd_inr": USD_INR,
            "as_of": PRICING_AS_OF,
            "assumptions": PRICING_ASSUMPTIONS,
            "sources": PRICING_SOURCES,
        }

    def pricing_configured(self) -> bool:
        return all(
            rate is not None
            for rate in (
                self.pricing_asr_per_hour,
                self.pricing_llm_per_1m_generated_chars,
                self.pricing_tts_per_1m_chars,
                self.pricing_sarvam_tts_per_1m_chars,
            )
        )


settings = Settings()


# ---------------------------------------------------------------------------
# Runtime-editable model configuration (in memory, applied to new calls)
# ---------------------------------------------------------------------------

SARVAM_MODELS = ["sarvam-105b", "sarvam-105b-conversations"]
INWORLD_MODELS = ["inworld-tts-2", "inworld-tts-2-flash"]
SPEECHIFY_MODELS = ["simba-3.5", "simba-3.2", "simba-3.0"]
SARVAM_TTS_MODELS = ["bulbul:v4-flash"]
TTS_PROVIDERS = ["inworld", "speechify", "sarvam"]
ASR_MODELS = [
    "universal-3-5-pro",
    "universal-3-6-pro",
    "universal-streaming-english",
]
# Inworld English voices.  The Indian-accent set comes from Inworld's own voice
# list (GET /tts/v1/voices, language "en", tagged "indian"); the Hindi voices
# are left out because they speak Hindi, not English.
INWORLD_VOICE_CATALOG: list[dict[str, Any]] = [
    {"id": "Ishaan", "name": "Ishaan", "gender": "Male", "group": "Indian English", "description": "Confident, natural — customer support and corporate."},
    {"id": "Nikhil", "name": "Nikhil", "gender": "Male", "group": "Indian English", "description": "Articulate and warm — customer support and corporate."},
    {"id": "Aditya", "name": "Aditya", "gender": "Male", "group": "Indian English", "description": "Confident, natural — customer support and corporate."},
    {"id": "Kabir", "name": "Kabir", "gender": "Male", "group": "Indian English", "description": "Bright and helpful — customer support and corporate."},
    {"id": "Dhruv", "name": "Dhruv", "gender": "Male", "group": "Indian English", "description": "Professional and measured — customer support and corporate."},
    {"id": "Vikram", "name": "Vikram", "gender": "Male", "group": "Indian English", "description": "Professional and measured — customer support and corporate."},
    {"id": "Yash", "name": "Yash", "gender": "Male", "group": "Indian English", "description": "Articulate and warm — customer support and corporate."},
    {"id": "Arjun", "name": "Arjun", "gender": "Male", "group": "Indian English", "description": "Clear and composed — explainers and training."},
    {"id": "Anjali", "name": "Anjali", "gender": "Female", "group": "Indian English", "description": "Confident and articulate — professional training."},
    {"id": "Saanvi", "name": "Saanvi", "gender": "Female", "group": "Indian English", "description": "Crisp and articulate — dynamic, vibrant delivery."},
    {"id": "Meher", "name": "Meher", "gender": "Female", "group": "Indian (Hindi voices, speak English)", "description": "Warm, friendly, North Indian urban accent — assistant and customer support."},
    {"id": "Seema", "name": "Seema", "gender": "Female", "group": "Indian (Hindi voices, speak English)", "description": "Clear, bright and friendly — conversational and informative."},
    {"id": "Shreya", "name": "Shreya", "gender": "Female", "group": "Indian (Hindi voices, speak English)", "description": "Friendly, helpful and bright — assistant and customer support."},
    {"id": "Riya", "name": "Riya", "gender": "Female", "group": "Indian (Hindi voices, speak English)", "description": "Professional and clean — customer support."},
    {"id": "Aanya", "name": "Aanya", "gender": "Female", "group": "Indian (Hindi voices, speak English)", "description": "Clear, formal and authoritative — news and information."},
    {"id": "Manoj", "name": "Manoj", "gender": "Male", "group": "Indian (Hindi voices, speak English)", "description": "Clear and professional — customer support."},
    {"id": "Aarav", "name": "Aarav", "gender": "Male", "group": "Indian (Hindi voices, speak English)", "description": "Thoughtful and earnest — quieter, motivational style."},
    {"id": "Ashley", "name": "Ashley", "gender": "Female", "group": "Standard", "description": "Default Inworld voice."},
    {"id": "Hana", "name": "Hana", "gender": "Female", "group": "Standard", "description": "Inworld voice."},
    {"id": "Dennis", "name": "Dennis", "gender": "Male", "group": "Standard", "description": "Inworld voice."},
]
INWORLD_VOICES = [voice["id"] for voice in INWORLD_VOICE_CATALOG]
INWORLD_VOICE_LABELS = {
    voice["id"]: f'{voice["name"]} · {voice["gender"]} — {voice["description"]}'
    for voice in INWORLD_VOICE_CATALOG
}
# Indian English voice set for Simba 3.5: two male en-IN voices followed by
# three top-ranked Indian female voices. Speechify has no female en-IN voice,
# so the female picks are Indian-language voices; every request pins
# language="en-IN" so they speak English with their native Indian accent.
SPEECHIFY_VOICE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "ankit",
        "name": "Ankit",
        "gender": "Male",
        "locale": "en-IN",
        "description": "Young adult, neutral and clear — built for customer service and IVR.",
    },
    {
        "id": "arun",
        "name": "Arun",
        "gender": "Male",
        "locale": "en-IN",
        "description": "Middle-aged and deep — a natural, conversational support agent.",
    },
    {
        "id": "mihika",
        "name": "Mihika",
        "gender": "Female",
        "locale": "hi-IN",
        "description": "Warm and natural — Hindi staff pick #1, the best all-round choice.",
    },
    {
        "id": "aadhya",
        "name": "Aadhya",
        "gender": "Female",
        "locale": "te-IN",
        "description": "Bright and friendly — Telugu staff pick #1, clear and upbeat support.",
    },
    {
        "id": "revati",
        "name": "Revati",
        "gender": "Female",
        "locale": "mr-IN",
        "description": "Relaxed and deeper — Marathi staff pick #1, calm customer service and IVR.",
    },
]
SPEECHIFY_VOICES = [voice["id"] for voice in SPEECHIFY_VOICE_CATALOG]
SPEECHIFY_VOICE_LABELS = {
    voice["id"]: f'{voice["name"]} · {voice["gender"]} — {voice["description"]}'
    for voice in SPEECHIFY_VOICE_CATALOG
}
# Bulbul v4-flash English personas (speaker names must match the model; the
# API rejects v3/v2 names).  Pulled from the API's own speaker list/docs.
SARVAM_VOICE_CATALOG: list[dict[str, Any]] = [
    {"id": "simran_en_customer", "name": "Simran", "style": "customer service"},
    {"id": "ishita_en_customer", "name": "Ishita", "style": "customer service"},
    {"id": "dev_en_conversational", "name": "Dev", "style": "conversational"},
    {"id": "deven_en_conversation", "name": "Deven", "style": "conversation"},
    {"id": "aparna_en_companion", "name": "Aparna", "style": "companion"},
]
SARVAM_VOICES = [voice["id"] for voice in SARVAM_VOICE_CATALOG]
SARVAM_VOICE_LABELS = {
    voice["id"]: f'{voice["name"]} — {voice["style"]}' for voice in SARVAM_VOICE_CATALOG
}
# Backwards-compatible alias for the original Inworld-only voice list.
VOICE_LIBRARY = INWORLD_VOICES


def _default_voice(provider: str) -> str:
    return {
        "speechify": settings.speechify_voice_id,
        "sarvam": settings.sarvam_tts_voice_id,
    }.get(provider, settings.inworld_voice_id)


def _initial_voice() -> str:
    return _default_voice(settings.tts_provider)


@dataclass
class RuntimeConfig:
    tts_provider: str = field(default_factory=lambda: settings.tts_provider)
    asr_speech_model: str = field(default_factory=lambda: settings.assemblyai_speech_model)
    sarvam_model: str = field(default_factory=lambda: settings.sarvam_model)
    inworld_model: str = field(default_factory=lambda: settings.inworld_tts_model)
    speechify_model: str = field(default_factory=lambda: settings.speechify_model)
    sarvam_tts_model: str = field(default_factory=lambda: settings.sarvam_tts_model)
    voice_id: str = field(default_factory=_initial_voice)
    temperature: float = field(default_factory=lambda: settings.sarvam_temperature)
    max_tokens: int = field(default_factory=lambda: settings.sarvam_max_tokens)
    system_prompt: str = field(default_factory=lambda: settings.agent_system_prompt)
    greeting: str = field(default_factory=lambda: settings.agent_greeting)
    speaking_rate: float = field(default_factory=lambda: settings.inworld_speaking_rate)
    delivery_mode: str = field(default_factory=lambda: settings.inworld_delivery_mode)
    _voices: dict[str, str] = field(
        default_factory=lambda: {
            "inworld": settings.inworld_voice_id,
            "speechify": settings.speechify_voice_id,
            "sarvam": settings.sarvam_tts_voice_id,
        },
        repr=False,
    )

    @property
    def tts_model(self) -> str:
        if self.tts_provider == "speechify":
            return self.speechify_model
        if self.tts_provider == "sarvam":
            return self.sarvam_tts_model
        return self.inworld_model

    @property
    def missing_credentials(self) -> list[str]:
        return settings.missing_credentials_for(self.tts_provider)

    @property
    def voice_pipeline_ready(self) -> bool:
        return settings.voice_pipeline_ready_for(self.tts_provider)

    def voice_for(self, provider: str) -> str:
        """Voice configured for a provider, including the inactive one."""
        if provider == self.tts_provider:
            return self.voice_id
        return self._voices.get(provider) or _default_voice(provider)

    def public(self) -> dict[str, Any]:
        return {
            "tts_provider": self.tts_provider,
            "tts_model": self.tts_model,
            "asr_speech_model": self.asr_speech_model,
            "sarvam_model": self.sarvam_model,
            "inworld_model": self.inworld_model,
            "speechify_model": self.speechify_model,
            "sarvam_tts_model": self.sarvam_tts_model,
            "voice_id": self.voice_id,
            "inworld_voice_id": self._voices.get("inworld"),
            "speechify_voice_id": self._voices.get("speechify"),
            "sarvam_tts_voice_id": self._voices.get("sarvam"),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "greeting": self.greeting,
            "speaking_rate": self.speaking_rate,
            "delivery_mode": self.delivery_mode,
        }

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply a partial update. Returns the public config."""
        if "tts_provider" in payload:
            value = str(payload["tts_provider"]).strip().lower()
            if value not in TTS_PROVIDERS:
                raise ValueError("tts_provider must be inworld, speechify or sarvam")
            self._voices[self.tts_provider] = self.voice_id
            self.tts_provider = value
            self.voice_id = self._voices.get(value) or _default_voice(value)
        if "asr_speech_model" in payload:
            value = str(payload["asr_speech_model"])
            if value not in ASR_MODELS:
                raise ValueError("Unsupported ASR model")
            self.asr_speech_model = value
        if "sarvam_model" in payload:
            value = str(payload["sarvam_model"])
            if value not in SARVAM_MODELS:
                raise ValueError("Unsupported Sarvam model")
            self.sarvam_model = value
        if "inworld_model" in payload:
            value = str(payload["inworld_model"])
            if value not in INWORLD_MODELS:
                raise ValueError("Unsupported Inworld model")
            self.inworld_model = value
        if "speechify_model" in payload:
            value = str(payload["speechify_model"])
            if value not in SPEECHIFY_MODELS:
                raise ValueError("Unsupported Speechify model")
            self.speechify_model = value
        if "sarvam_tts_model" in payload:
            value = str(payload["sarvam_tts_model"])
            if value not in SARVAM_TTS_MODELS:
                raise ValueError("Unsupported Sarvam TTS model")
            self.sarvam_tts_model = value
        if "voice_id" in payload:
            value = str(payload["voice_id"]).strip()
            if not value or len(value) > 80:
                raise ValueError("Voice ID must be 1-80 characters")
            self.voice_id = value
            self._voices[self.tts_provider] = value
        if "temperature" in payload:
            self.temperature = min(2.0, max(0.0, float(payload["temperature"])))
        if "max_tokens" in payload:
            self.max_tokens = min(2048, max(32, int(payload["max_tokens"])))
        if "greeting" in payload:
            self.greeting = str(payload["greeting"]).strip()[:1000]
        if "speaking_rate" in payload:
            self.speaking_rate = min(1.5, max(0.5, float(payload["speaking_rate"])))
        if "delivery_mode" in payload:
            value = str(payload["delivery_mode"]).upper()
            if value not in {"STABLE", "BALANCED", "CREATIVE"}:
                raise ValueError("delivery_mode must be STABLE, BALANCED or CREATIVE")
            self.delivery_mode = value
        return self.public()


runtime_config = RuntimeConfig()
