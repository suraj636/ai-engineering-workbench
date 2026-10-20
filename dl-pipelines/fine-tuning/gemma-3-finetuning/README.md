## 🦥 Gemma 3 Fine-Tuning Playground (Unsloth + LoRA)

A small, personal project for practicing parameter-efficient fine-tuning of Google's Gemma 3
Instruct models with [Unsloth](https://unsloth.ai/), using 4-bit loading + LoRA. Built to be
short, readable, and runnable end-to-end on a single CUDA GPU.

- **Models**: 270M, 1B, 4B, 12B, 27B
- **Dataset**: [FineTome-100k](https://huggingface.co/datasets/mlabonne/FineTome-100k) (ShareGPT-style multi-turn chats)
- **Method**: Parameter-efficient LoRA (not full fine-tuning)

### Project structure

```
.
├── finetune_gemma3.py            # main training script
├── requirements.txt               # runtime dependencies
├── requirements-dev.txt           # + pytest, for running tests
├── data/
│   └── sample_conversations.jsonl # a handful of ShareGPT-format examples for previewing the schema
└── tests/
    ├── test_sample_data.py        # validates data/sample_conversations.jsonl (no GPU/heavy deps needed)
    └── test_finetune_config.py    # sanity-checks script config (skips if torch/unsloth aren't installed)
```

### Install

```bash
pip install -r requirements.txt
# or latest Unsloth per their guidance
pip install --upgrade --force-reinstall --no-cache-dir unsloth unsloth_zoo
```

### Run

```bash
python finetune_gemma3.py
```

Outputs are saved to `finetuned_model/`.

### What the script does

1. Loads Gemma 3 with 4-bit quantization via Unsloth's `FastModel`.
2. Attaches LoRA adapters to attention/MLP projections.
3. Prepares FineTome-100k by applying the Gemma 3 chat template.
4. Trains with TRL's `SFTTrainer` for a few demo steps.
5. Saves the finetuned weights.

### Before vs. After Fine-Tuning

This fine-tuning process changes the way the base model structures and delivers its responses:

* **Original Capabilities (Gemma 3 270M Instruct)**:
  * **General Abilities:** Capable of general multi-turn dialogue, question answering, and basic programming tasks.
  * **Limitations:** Being extremely lightweight (270M parameters), the base model is prone to losing context in multi-turn dialogues, hallucinating information, generating repetitive descriptions, and ignoring stylistic formatting instructions.
* **Post Fine-Tuning Capabilities**:
  * **Structured & Educational Persona:** Learns to adopt an organized, helpful, and highly descriptive tone using markdown structure (e.g. bolded sections, headers, and bulleted lists).
  * **Better Context Retention:** Becomes more adept at keeping track of state and dependencies in multi-turn follow-up queries.
  * **Alignment:** Adapts the model's behavior to the high-quality, filtered datasets of `FineTome-100k` via LoRA weights without overwriting the model's pre-trained general knowledge.


### Change model or settings

Edit the top of `finetune_gemma3.py`:

- `MODEL_NAME` (e.g., `unsloth/gemma-3-270m-it`, `unsloth/gemma-3-1b-it`)
- `MAX_SEQ_LEN`, `LOAD_IN_4BIT`, `FULL_FINETUNING`

Note: 4-bit/8-bit loading requires a CUDA GPU. On Mac (M1/M2), run on CPU/MPS without quantization or use a GPU machine.

### Sample data

`data/sample_conversations.jsonl` holds a few short, hand-written conversations in the same
`conversations: [{from, value}, ...]` schema used by FineTome-100k. It's meant as a quick,
offline way to see the expected format before pulling the full dataset from the Hub.

### Testing

Install dev dependencies and run pytest:

```bash
pip install -r requirements-dev.txt
pytest
```

- `test_sample_data.py` checks that the sample JSONL file is well-formed and that turns
  alternate correctly between `human` and `gpt` — runs anywhere, no GPU or ML libraries required.
- `test_finetune_config.py` imports `finetune_gemma3.py` and checks its config values and
  function signatures. It auto-skips if `torch`/`unsloth` aren't installed in the current
  environment.

### Acknowledgements

- Built while following [Unsloth's Gemma 3 guide](https://unsloth.ai/blog/gemma3).
- Dataset: [FineTome-100k](https://huggingface.co/datasets/mlabonne/FineTome-100k) by mlabonne.
