import logging
from pathlib import Path
import grain.python as pygrain
from tokenizers import ByteLevelBPETokenizer, Tokenizer

logger = logging.getLogger(__name__)

def load_stories_from_file(file_path, max_stories=None):
    """Load stories from a text file. Each story ends with <|endoftext|>."""
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error(f"Data file not found: {file_path}")
        raise FileNotFoundError(f"Data file not found: {file_path}")

    text = file_path.read_text(encoding="utf-8", errors="replace")
    stories = [s.strip() for s in text.split("<|endoftext|>") if s.strip()]
    if max_stories is not None:
        stories = stories[:max_stories]

    logger.info(f"Loaded {len(stories):,} stories from {file_path}")
    return stories

def train_or_load_tokenizer(stories, vocab_size, save_dir):
    save_dir = Path(save_dir)
    tokenizer_file = save_dir / "tokenizer.json"

    if tokenizer_file.exists():
        logger.info(f"Loading existing tokenizer from {tokenizer_file}")
        tok = Tokenizer.from_file(str(tokenizer_file))
    else:
        logger.info(f"Training a new BPE tokenizer (vocab_size={vocab_size}) on this dataset...")
        tok = ByteLevelBPETokenizer()
        tok.train_from_iterator(
            stories,
            vocab_size=vocab_size,
            min_frequency=2,
            special_tokens=["<|pad|>", "<|endoftext|>"],
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        tok.save(str(tokenizer_file))
        logger.info(f"Saved tokenizer to {tokenizer_file}")

    pad_id = tok.token_to_id("<|pad|>")
    eos_id = tok.token_to_id("<|endoftext|>")
    return tok, pad_id, eos_id

class StoryDataset:
    def __init__(self, stories, maxlen, tok, pad_id, eos_id):
        self.stories = stories
        self.maxlen = maxlen
        self.tok = tok
        self.pad_id = pad_id
        self.eos_id = eos_id

    def __len__(self):
        return len(self.stories)

    def __getitem__(self, idx):
        ids = self.tok.encode(self.stories[idx]).ids + [self.eos_id]
        if len(ids) > self.maxlen:
            ids = ids[: self.maxlen]
        ids = ids + [self.pad_id] * (self.maxlen - len(ids))
        return ids

def make_dataloader(stories, maxlen, tok, pad_id, eos_id, batch_size,
                     shuffle, num_epochs, seed, drop_remainder):
    dataset = StoryDataset(stories, maxlen, tok, pad_id, eos_id)
    sampler = pygrain.IndexSampler(
        num_records=len(dataset),
        shuffle=shuffle,
        seed=seed,
        shard_options=pygrain.NoSharding(),
        num_epochs=num_epochs,
    )
    dataloader = pygrain.DataLoader(
        data_source=dataset,
        sampler=sampler,
        operations=[pygrain.Batch(batch_size=batch_size, drop_remainder=drop_remainder)],
    )
    batches_per_epoch = len(dataset) // batch_size if drop_remainder else -(-len(dataset) // batch_size)
    return dataloader, batches_per_epoch
