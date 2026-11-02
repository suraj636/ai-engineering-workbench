"""Lazy loaders for the three on-device models.

Nothing is imported at module load: the first use pays for the download, and
the app can start a server before the models are ready.
"""
import os
import threading
from pathlib import Path

NOMIC_MODEL = "nomic-ai/nomic-embed-text-v1.5"
NOMIC_DIM = 768
CLIP_VISION_MODEL = "Qdrant/clip-ViT-B-32-vision"
CLIP_DIM = 512
WHISPER_MODEL = "whisper-base"

# Leave CPU capacity for the camera and the browser.
ENCODER_THREADS = 2
ASR_THREADS = 4

# FastEmbed defaults to /tmp, which some systems prune. Keep models durable.
CACHE_DIR = (os.environ.get("FASTEMBED_CACHE_PATH")
             or str(Path.home() / ".cache" / "fastembed"))

_load_lock = threading.Lock()
_text = _clip = _asr = None


def _sess_options():
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = ASR_THREADS
    opts.inter_op_num_threads = ASR_THREADS
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return opts


def _text_model():
    global _text
    if _text is None:
        with _load_lock:
            if _text is None:
                from fastembed import TextEmbedding
                _text = TextEmbedding(NOMIC_MODEL, threads=ENCODER_THREADS,
                                      cache_dir=CACHE_DIR)
    return _text


def _clip_vision():
    global _clip
    if _clip is None:
        with _load_lock:
            if _clip is None:
                from fastembed import ImageEmbedding
                _clip = ImageEmbedding(CLIP_VISION_MODEL,
                                       threads=ENCODER_THREADS,
                                       cache_dir=CACHE_DIR)
    return _clip


def _asr_model():
    global _asr
    if _asr is None:
        with _load_lock:
            if _asr is None:
                import onnx_asr
                _asr = onnx_asr.load_model(
                    WHISPER_MODEL, sess_options=_sess_options(),
                    providers=["CPUExecutionProvider"])
    return _asr


def embed_text(text):
    """Embed one transcript for storage. Nomic, 768-d."""
    return next(_text_model().embed([text])).tolist()


def embed_query(text):
    """Embed one question. Nomic's query prefix matters for retrieval."""
    return next(_text_model().query_embed([text])).tolist()


def embed_crop(bgr):
    """Embed one OpenCV BGR crop with CLIP's vision tower. 512-d."""
    from PIL import Image
    img = Image.fromarray(bgr[:, :, ::-1])
    return next(_clip_vision().embed([img])).tolist()


# Set another Whisper language code, or None for auto-detection at the cost of
# a second inference pass.
LANGUAGE = "en"


def transcribe(wav_path):
    """Local Whisper speech-to-text on one 16 kHz mono WAV."""
    kwargs = {"language": LANGUAGE} if LANGUAGE else {}
    out = _asr_model().recognize(wav_path, **kwargs)
    if isinstance(out, list):          # defensive: batch-shaped result
        out = " ".join(str(part) for part in out)
    return out.strip()


def warm(progress=lambda name: None):
    """Load the encoders the live loop needs, in the order it needs them."""
    import numpy as np
    progress("CLIP vision encoder")
    embed_crop(np.zeros((32, 32, 3), dtype=np.uint8))
    progress("Nomic text encoder")
    embed_text("warm")
