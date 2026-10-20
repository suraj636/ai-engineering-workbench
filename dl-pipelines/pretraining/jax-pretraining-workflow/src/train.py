import logging
import random
from pathlib import Path

import jax
import jax.numpy as jnp
import flax.nnx as nnx
import optax
import orbax.checkpoint
import matplotlib.pyplot as plt

from src.config import *
from src.data import load_stories_from_file, train_or_load_tokenizer, make_dataloader
from src.model import MiniGPT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("training.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def make_loss_fn(pad_id):
    def loss_fn(model, batch):
        inputs, targets = batch
        logits = model(inputs)
        per_token_loss = optax.softmax_cross_entropy_with_integer_labels(logits, targets)
        # mask out padding positions so the model isn't trained to predict them
        mask = (targets != pad_id).astype(per_token_loss.dtype)
        loss = (per_token_loss * mask).sum() / jnp.maximum(mask.sum(), 1.0)
        return loss, logits
    return loss_fn

def main():
    print("Starting training setup...")
    
    # ---- data ----
    stories = load_stories_from_file(DATA_PATH)
    rnd = random.Random(SEED)
    stories = stories[:]
    rnd.shuffle(stories)
    n_val = max(1, int(len(stories) * VAL_FRACTION))
    val_stories, train_stories = stories[:n_val], stories[n_val:]
    print(f"Train stories: {len(train_stories)}  |  Val stories: {len(val_stories)}")

    tok, pad_id, eos_id = train_or_load_tokenizer(train_stories, VOCAB_SIZE, TOKENIZER_DIR)
    vocab_size = tok.get_vocab_size()
    print(f"Tokenizer vocab size: {vocab_size}, pad_id={pad_id}, eos_id={eos_id}")

    train_loader, batches_per_epoch = make_dataloader(
        train_stories, MAXLEN, tok, pad_id, eos_id, BATCH_SIZE,
        shuffle=True, num_epochs=NUM_EPOCHS, seed=SEED, drop_remainder=True,
    )
    val_loader_fn = lambda: make_dataloader(
        val_stories, MAXLEN, tok, pad_id, eos_id, BATCH_SIZE,
        shuffle=False, num_epochs=1, seed=SEED, drop_remainder=False,
    )[0]

    # ---- model / optimizer ----
    model = MiniGPT(
        maxlen=MAXLEN, vocab_size=vocab_size, embed_dim=EMBED_DIM,
        num_heads=NUM_HEADS, feed_forward_dim=FF_DIM,
        num_transformer_blocks=NUM_LAYERS, rngs=nnx.Rngs(0),
    )

    total_steps = batches_per_epoch * NUM_EPOCHS
    warmup_steps = max(1, total_steps // 10)
    print(f"Total training steps: {total_steps:,}  |  Warmup steps: {warmup_steps:,}")

    lr_schedule = optax.warmup_cosine_decay_schedule(
        init_value=0.0, peak_value=PEAK_LR, warmup_steps=warmup_steps,
        decay_steps=total_steps, end_value=1e-5,
    )
    tx = optax.chain(
        optax.clip_by_global_norm(GRAD_CLIP_NORM),
        optax.adamw(learning_rate=lr_schedule, weight_decay=WEIGHT_DECAY),
    )
    
    try:
        optimizer = nnx.Optimizer(model, tx, wrt=nnx.Param)
    except TypeError:
        optimizer = nnx.Optimizer(model, tx)
        
    metrics = nnx.MultiMetric(loss=nnx.metrics.Average("loss"))
    loss_fn = make_loss_fn(pad_id)

    def _optimizer_update(optimizer, model, grads):
        try:
            optimizer.update(model, grads)
        except TypeError:
            optimizer.update(grads)

    @nnx.jit
    def train_step(model, optimizer, metrics, batch):
        grad_fn = nnx.value_and_grad(loss_fn, has_aux=True)
        (loss, logits), grads = grad_fn(model, batch)
        metrics.update(loss=loss, logits=logits, labels=batch[1])
        _optimizer_update(optimizer, model, grads)

    @nnx.jit
    def eval_step(model, metrics, batch):
        loss, logits = loss_fn(model, batch)
        metrics.update(loss=loss, logits=logits, labels=batch[1])

    prep_target_batch = jax.vmap(
        lambda tokens: jnp.concatenate((tokens[1:], jnp.array([pad_id])))
    )

    def run_validation():
        val_metrics = nnx.MultiMetric(loss=nnx.metrics.Average("loss"))
        for batch in val_loader_fn():
            inp = jnp.array(batch).astype(jnp.int32)
            tgt = prep_target_batch(inp).astype(jnp.int32)
            eval_step(model, val_metrics, (inp, tgt))
        return float(val_metrics.compute()["loss"])

    history = {"train_loss": [], "val_loss": [], "step": []}
    step = 0
    
    print("Starting training loop...")
    for batch in train_loader:
        inp = jnp.array(batch).astype(jnp.int32)
        tgt = prep_target_batch(inp).astype(jnp.int32)
        train_step(model, optimizer, metrics, (inp, tgt))
        step += 1

        if step % EVAL_EVERY_STEPS == 0:
            train_loss = float(metrics.compute()["loss"])
            metrics.reset()
            val_loss = run_validation()
            history["train_loss"].append(train_loss)
            history["val_loss"].append(val_loss)
            history["step"].append(step)
            current_lr = lr_schedule(step)
            print(f"Step {step}/{total_steps}  train_loss={train_loss:.4f}  "
                  f"val_loss={val_loss:.4f}  lr={current_lr:.2e}")

    # ---- plot ----
    plt.figure(figsize=(7, 5))
    plt.plot(history["step"], history["train_loss"], label="train loss")
    plt.plot(history["step"], history["val_loss"], label="val loss")
    plt.title("Training / Validation Loss")
    plt.xlabel("Step")
    plt.ylabel("Loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(TRAINING_PLOT_PATH)
    print(f"Saved loss curve to {TRAINING_PLOT_PATH}")

    # ---- checkpoint ----
    checkpointer = orbax.checkpoint.PyTreeCheckpointer()
    checkpointer.save(CHECKPOINT_PATH, nnx.state(model), force=True)
    print(f"Model saved to {CHECKPOINT_PATH}")
    print(f"Tokenizer saved to {TOKENIZER_DIR}/tokenizer.json")

    print("Training complete!")

    # ---- quick sanity generation ----
    from src.infer import generate_story
    rng = jax.random.PRNGKey(0)
    sample, rng = generate_story(model, tok, "Once upon a time a big bear",
                                  pad_id, eos_id, rng, temperature=0.7, max_new_tokens=60)
    print("\nSample generation:\n", sample)

if __name__ == "__main__":
    main()
