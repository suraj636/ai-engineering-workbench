# DL Pipelines

Deep-learning and LLM training workflows: pretraining, fine-tuning, and future inference optimization.

| Project | Source | Purpose | Status |
|---|---|---|---|
| [pretraining/jax-pretraining-workflow](pretraining/jax-pretraining-workflow) | [GitHub](https://github.com/suraj636/jax-pretraining-workflow) | JAX/Flax NNX MiniGPT (~3.5M parameters) pretrained from scratch on TinyStories with custom BPE, checkpoints, loss curves, and Gradio inference. | Implemented |
| [fine-tuning/gemma-3-finetuning](fine-tuning/gemma-3-finetuning) | [GitHub](https://github.com/suraj636/gemma-3-finetuning) | Parameter-efficient Gemma 3 fine-tuning with Unsloth, 4-bit loading, and LoRA on the FineTome-100k conversational dataset. | Implemented |

## Subcategories

- `pretraining/` — training models from scratch.
- `fine-tuning/` — adapting pretrained models with PEFT, LoRA, quantization, and instruction tuning.
- `inference/` — reserved for inference optimization and serving projects; no project imported yet.
