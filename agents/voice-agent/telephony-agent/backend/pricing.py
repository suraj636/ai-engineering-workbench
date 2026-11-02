"""Static, source-linked pricing used for dashboard estimates.

Official list prices collected on 2026-10-29.  Override any rate through the
matching ``PRICING_*`` environment variable if your contract differs.

Sources
-------
AssemblyAI (Universal-3.5 Pro Realtime, ``universal-3-5-pro``):
    https://www.assemblyai.com/pricing  ->  $0.45 / hour (billed per session)
    (Universal-3.6 Pro Realtime is the same $0.45 / hour.)
Sarvam 105B (``sarvam-105b``):
    https://docs.sarvam.ai/api/getting-started/pricing  ->  INR 60 / 1M output tokens
Sarvam Bulbul v4 Flash (pay as you go, excl. GST):
    https://docs.sarvam.ai/api/getting-started/pricing  ->  INR 10 / 10K characters
Speechify Simba 3.5 (Starter promotional meter):
    https://speechify.ai/pricing  ->  $10 / 1M characters through 2026-11-21
    (list price after the promotion: $20 / 1M characters)

The dashboard prices LLM and TTS from the characters the model generates.
Sarvam quotes output tokens, so the static LLM rate converts tokens to
characters with a documented approximation of 4 characters per token and a
static INR/USD rate.  The resulting USD rate is an estimate, not a provider
invoice.
"""

from __future__ import annotations

PRICING_AS_OF = "2026-10-29"

# Static conversion assumptions for the character-based LLM estimate.
USD_INR = 95.0
LLM_CHARS_PER_TOKEN = 4.0
SARVAM_OUTPUT_INR_PER_1M_TOKENS = 60.0
# Sarvam 105B list prices (excl. GST), checked 2026-10-30. Cache writes are not
# listed on the pricing page, so they are not priced.
SARVAM_INPUT_INR_PER_1M_TOKENS = 15.0
SARVAM_CACHED_INR_PER_1M_TOKENS = 5.0
# Inworld TTS list prices, USD per 1M characters, On-Demand plan (inworld.ai/pricing,
# checked 2026-10-30). Monthly plans are cheaper (TTS-2 $20 to $12.50, Flash $10 to $7).
INWORLD_TTS_2_USD_PER_1M_CHARS = 25.0
INWORLD_TTS_2_FLASH_USD_PER_1M_CHARS = 15.0
SARVAM_TTS_INR_PER_1M_CHARS = 1000.0

STATIC_RATES: dict[str, float] = {
    # AssemblyAI Universal-3.5/3.6 Pro Realtime: $0.45/hour.
    "asr_per_hour": 0.45,
    # Sarvam 105B output: INR 60 / 1M tokens -> USD per 1M generated chars.
    "llm_per_1m_generated_chars": round(
        (SARVAM_OUTPUT_INR_PER_1M_TOKENS / USD_INR) / LLM_CHARS_PER_TOKEN, 6
    ),
    "inworld_tts_2_per_1m_chars": INWORLD_TTS_2_USD_PER_1M_CHARS,
    "inworld_tts_2_flash_per_1m_chars": INWORLD_TTS_2_FLASH_USD_PER_1M_CHARS,
    # Sarvam 105B by token (USD per 1M tokens), converted from INR.
    "llm_input_per_1m_tokens": round(SARVAM_INPUT_INR_PER_1M_TOKENS / USD_INR, 6),
    "llm_cached_per_1m_tokens": round(SARVAM_CACHED_INR_PER_1M_TOKENS / USD_INR, 6),
    "llm_output_per_1m_tokens": round(SARVAM_OUTPUT_INR_PER_1M_TOKENS / USD_INR, 6),
    # Speechify Simba 3.5 Starter promotional rate through 2026-11-21.
    "tts_per_1m_chars": 10.0,
    # Sarvam Bulbul v4 Flash: INR 10 / 10K chars = INR 1,000 / 1M chars.
    "sarvam_tts_per_1m_chars": round(SARVAM_TTS_INR_PER_1M_CHARS / USD_INR, 6),
}

PRICING_ASSUMPTIONS = {
    "currency": "USD",
    "asr_basis": "AssemblyAI session minutes (Universal-3.5/3.6 Pro at $0.45/hour)",
    "llm_basis": "Sarvam 105B tokens: input INR 15, cached input INR 5, output INR 60 per 1M (output falls back to chars/4 when the API reports no tokens)",
    "tts_basis": "Processed characters: Speechify $10/1M (Simba 3.5 Starter promo); Sarvam Bulbul v4 Flash INR 1,000/1M at INR 95/USD; Inworld TTS-2 $25/1M and TTS-2 Flash $15/1M (On-Demand)",
    "usd_inr": USD_INR,
    "llm_chars_per_token": LLM_CHARS_PER_TOKEN,
    "sarvam_output_inr_per_1m_tokens": SARVAM_OUTPUT_INR_PER_1M_TOKENS,
}

PRICING_SOURCES = {
    "asr": "https://www.assemblyai.com/pricing",
    "llm": "https://docs.sarvam.ai/api/getting-started/pricing",
    "tts": "https://speechify.ai/pricing",
}
