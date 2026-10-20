import os
from pathlib import Path

# Project root directory
ROOT_DIR = Path(__file__).resolve().parent.parent

# Paths
DATA_PATH = ROOT_DIR / "TinyStories-1000.txt"
TOKENIZER_DIR = ROOT_DIR / "story_tokenizer"
CHECKPOINT_PATH = ROOT_DIR / "small_checkpoint.orbax"
TRAINING_PLOT_PATH = ROOT_DIR / "training_loss.png"

# Hyperparameters
VOCAB_SIZE = 4096
MAXLEN = 128
EMBED_DIM = 192
NUM_HEADS = 6
NUM_LAYERS = 6
FF_DIM = 4 * EMBED_DIM

# Training
BATCH_SIZE = 32
NUM_EPOCHS = 60
VAL_FRACTION = 0.1
PEAK_LR = 3e-4
WEIGHT_DECAY = 0.01
GRAD_CLIP_NORM = 1.0
EVAL_EVERY_STEPS = 20
SEED = 42
