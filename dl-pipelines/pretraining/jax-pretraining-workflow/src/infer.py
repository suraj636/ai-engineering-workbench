import logging
from pathlib import Path

import jax
import jax.numpy as jnp
import flax.nnx as nnx
from tokenizers import Tokenizer
import gradio as gr

from src.config import *
from src.model import MiniGPT

logger = logging.getLogger(__name__)

def load_trained_model(checkpoint_path=CHECKPOINT_PATH, tokenizer_dir=TOKENIZER_DIR):
    """Rebuild the model architecture and restore weights + tokenizer from disk."""
    tokenizer_path = Path(tokenizer_dir) / "tokenizer.json"
    if not tokenizer_path.exists():
        raise FileNotFoundError(f"Tokenizer not found at {tokenizer_path}")
        
    tok = Tokenizer.from_file(str(tokenizer_path))
    pad_id = tok.token_to_id("<|pad|>")
    eos_id = tok.token_to_id("<|endoftext|>")

    vocab_size = tok.get_vocab_size()
    model = MiniGPT(
        maxlen=MAXLEN, vocab_size=vocab_size, embed_dim=EMBED_DIM,
        num_heads=NUM_HEADS, feed_forward_dim=FF_DIM,
        num_transformer_blocks=NUM_LAYERS, rngs=nnx.Rngs(0),
    )

    import orbax.checkpoint
    from orbax import checkpoint
    from jax.sharding import SingleDeviceSharding

    cpu_sharding = SingleDeviceSharding(jax.devices("cpu")[0])
    restore_args = jax.tree_util.tree_map(
        lambda _: checkpoint.ArrayRestoreArgs(sharding=cpu_sharding), nnx.state(model)
    )
    checkpointer = orbax.checkpoint.PyTreeCheckpointer()
    
    if not Path(checkpoint_path).exists():
        raise FileNotFoundError(f"Checkpoint not found at {checkpoint_path}")
        
    restored_state = checkpointer.restore(
        Path(checkpoint_path), item=nnx.state(model), restore_args=restore_args
    )
    nnx.update(model, restored_state)
    logger.info(f"Restored model from {checkpoint_path} with vocab_size={vocab_size}")
    return model, tok, pad_id, eos_id

def generate_text(model, tok, start_tokens, pad_id, eos_id, rng,
                   max_new_tokens=50, temperature=0.8, top_k=40):
    tokens = list(start_tokens)
    for _ in range(max_new_tokens):
        context = tokens[-model.maxlen:]
        actual_len = len(context)
        if actual_len < model.maxlen:
            context = context + [pad_id] * (model.maxlen - actual_len)
        context_array = jnp.array(context)[None, :]
        logits = model(context_array)
        next_token_logits = logits[0, actual_len - 1, :]

        if temperature <= 0:
            next_token = int(jnp.argmax(next_token_logits))
        else:
            scaled = next_token_logits / temperature
            if top_k is not None and top_k < scaled.shape[-1]:
                top_logits, top_idx = jax.lax.top_k(scaled, top_k)
                rng, subkey = jax.random.split(rng)
                choice = jax.random.categorical(subkey, top_logits)
                next_token = int(top_idx[choice])
            else:
                rng, subkey = jax.random.split(rng)
                next_token = int(jax.random.categorical(subkey, scaled))

        if next_token == eos_id:
            break
        tokens.append(next_token)
    return tok.decode(tokens), rng

def generate_story(model, tok, story_prompt, pad_id, eos_id, rng, temperature=0.8, max_new_tokens=50):
    start_tokens = tok.encode(story_prompt).ids[:model.maxlen]
    text, rng = generate_text(model, tok, start_tokens, pad_id, eos_id, rng,
                               max_new_tokens=max_new_tokens, temperature=temperature)
    return text, rng

def launch_demo():
    logging.basicConfig(level=logging.INFO)
    logger.info("Loading model for demo...")
    model, tok, pad_id, eos_id = load_trained_model()
    
    state = {"rng": jax.random.PRNGKey(0)}

    def create_story(story_prompt, temperature, max_new_tokens):
        text, new_rng = generate_story(
            model, tok, story_prompt, pad_id, eos_id, state["rng"],
            temperature=temperature, max_new_tokens=int(max_new_tokens),
        )
        state["rng"] = new_rng
        return text

    demo = gr.Interface(
        fn=create_story,
        inputs=[
            gr.Textbox(label="Story Prompt", placeholder="Once upon a time..."),
            gr.Slider(minimum=0.1, maximum=1.5, value=0.8, step=0.01, label="Temperature"),
            gr.Slider(minimum=1, maximum=200, value=50, step=1, label="Max Tokens"),
        ],
        outputs=["text"],
        title="MiniGPT Story Generator"
    )
    demo.launch(share=True)

if __name__ == "__main__":
    launch_demo()
