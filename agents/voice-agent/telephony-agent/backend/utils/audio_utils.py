"""Audio codec, resampling and format helpers.

Plivo's bidirectional stream uses 8 kHz mono μ-law (G.711).  AssemblyAI wants
16-bit little-endian PCM, and Inworld can emit either μ-law or PCM.  All
resampling is stateful so streaming chunks stay continuous between calls.
"""

from __future__ import annotations

import base64
import math
from array import array

try:  # Python <= 3.12
    import audioop
except ImportError:  # Python 3.13+
    import audioop_lts as audioop  # type: ignore[no-redef]

TELEPHONY_RATE = 8000
DEFAULT_ASR_RATE = 16000
_PCM16 = 2  # bytes per sample


def mulaw_to_pcm16(data: bytes) -> bytes:
    """Decode 8-bit μ-law to 16-bit signed little-endian PCM."""
    if not data:
        return b""
    return audioop.ulaw2lin(data, _PCM16)


def pcm16_to_mulaw(data: bytes) -> bytes:
    """Encode 16-bit signed little-endian PCM to 8-bit μ-law."""
    if not data:
        return b""
    return audioop.lin2ulaw(data, _PCM16)


def pcm16_rms(data: bytes) -> int:
    if not data:
        return 0
    try:
        return audioop.rms(data, _PCM16)
    except audioop.error:
        return 0


def pcm16_duration_ms(data: bytes, sample_rate: int) -> float:
    if not data or sample_rate <= 0:
        return 0.0
    return (len(data) / _PCM16) / sample_rate * 1000.0


def mulaw_duration_ms(data: bytes, sample_rate: int = TELEPHONY_RATE) -> float:
    if not data or sample_rate <= 0:
        return 0.0
    return len(data) / sample_rate * 1000.0


def pcm16_to_float32(data: bytes) -> list[float]:
    """Convert PCM16 bytes to floats in [-1, 1] (used by tests/tools)."""
    samples = array("h")
    samples.frombytes(data)
    return [max(-1.0, min(1.0, sample / 32768.0)) for sample in samples]


def float32_to_pcm16(samples: list[float]) -> bytes:
    values = array("h", (int(max(-1.0, min(1.0, value)) * 32767) for value in samples))
    return values.tobytes()


class StreamResampler:
    """Stateful mono PCM16 resampler.

    `audioop.ratecv` keeps filter state between chunks, so consecutive chunks
    join without clicks.  One instance must only be used for one audio stream.
    """

    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._state = None

    @property
    def passthrough(self) -> bool:
        return self.in_rate == self.out_rate

    def process(self, pcm16: bytes) -> bytes:
        if not pcm16 or self.passthrough:
            return pcm16
        converted, self._state = audioop.ratecv(
            pcm16,
            _PCM16,
            1,
            self.in_rate,
            self.out_rate,
            self._state,
        )
        return converted

    def reset(self) -> None:
        self._state = None


def mulaw8k_to_pcm16(pcm_rate: int, data: bytes) -> bytes:
    """μ-law 8 kHz -> PCM16 at `pcm_rate` (fresh resampler, one-shot)."""
    pcm = mulaw_to_pcm16(data)
    if pcm_rate == TELEPHONY_RATE:
        return pcm
    return audioop.ratecv(pcm, _PCM16, 1, TELEPHONY_RATE, pcm_rate, None)[0]


def pcm16_to_plivo_mulaw(data: bytes, sample_rate: int) -> bytes:
    """PCM16 at any rate -> μ-law 8 kHz, ready for a Plivo `playAudio`."""
    if sample_rate == TELEPHONY_RATE:
        return pcm16_to_mulaw(data)
    resampled = audioop.ratecv(data, _PCM16, 1, sample_rate, TELEPHONY_RATE, None)[0]
    return pcm16_to_mulaw(resampled)


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(data: str) -> bytes:
    return base64.b64decode(data, validate=False)


def pcm16_seconds(data: bytes, sample_rate: int) -> float:
    return len(data) / (_PCM16 * sample_rate) if sample_rate else 0.0


def linear_resample(samples: list[float], in_rate: int, out_rate: int) -> list[float]:
    """Small pure-python resampler for browser-side tooling/tests."""
    if in_rate == out_rate or not samples:
        return samples
    ratio = out_rate / in_rate
    out_length = max(1, int(math.floor(len(samples) * ratio)))
    result: list[float] = []
    for index in range(out_length):
        position = index / ratio
        left = int(position)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        result.append(samples[left] * (1 - fraction) + samples[right] * fraction)
    return result
