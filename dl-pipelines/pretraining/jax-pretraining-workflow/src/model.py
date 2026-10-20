import jax
import jax.numpy as jnp
import flax.nnx as nnx

class TokenAndPositionEmbedding(nnx.Module):
    def __init__(self, maxlen, vocab_size, embed_dim, *, rngs):
        self.token_emb = nnx.Embed(vocab_size, embed_dim, rngs=rngs)
        self.pos_emb = nnx.Embed(maxlen, embed_dim, rngs=rngs)

    def __call__(self, x):
        seq_len = x.shape[1]
        positions = jnp.arange(seq_len)[None, :]
        return self.token_emb(x) + self.pos_emb(positions)


class FeedForward(nnx.Module):
    """The missing piece from the original TransformerBlock."""
    def __init__(self, embed_dim, ff_dim, *, rngs):
        self.fc1 = nnx.Linear(embed_dim, ff_dim, rngs=rngs)
        self.fc2 = nnx.Linear(ff_dim, embed_dim, rngs=rngs)

    def __call__(self, x):
        return self.fc2(nnx.gelu(self.fc1(x)))


class TransformerBlock(nnx.Module):
    def __init__(self, embed_dim, num_heads, ff_dim, *, rngs):
        self.ln1 = nnx.LayerNorm(embed_dim, rngs=rngs)
        self.attention = nnx.MultiHeadAttention(
            num_heads=num_heads,
            in_features=embed_dim,
            qkv_features=embed_dim,
            out_features=embed_dim,
            decode=False,
            rngs=rngs,
        )
        self.ln2 = nnx.LayerNorm(embed_dim, rngs=rngs)
        self.ff = FeedForward(embed_dim, ff_dim, rngs=rngs)

    def __call__(self, x, mask=None):
        # pre-norm residual attention
        x = x + self.attention(self.ln1(x), mask=mask)
        # pre-norm residual MLP
        x = x + self.ff(self.ln2(x))
        return x


class MiniGPT(nnx.Module):
    def __init__(self, maxlen, vocab_size, embed_dim, num_heads,
                 feed_forward_dim, num_transformer_blocks, *, rngs):
        self.maxlen = maxlen
        self.num_transformer_blocks = num_transformer_blocks
        self.embedding = TokenAndPositionEmbedding(maxlen, vocab_size, embed_dim, rngs=rngs)
        
        for i in range(num_transformer_blocks):
            setattr(self, f"block_{i}", TransformerBlock(embed_dim, num_heads, feed_forward_dim, rngs=rngs))
            
        self.final_norm = nnx.LayerNorm(embed_dim, rngs=rngs)
        self.output_layer = nnx.Linear(embed_dim, vocab_size, use_bias=False, rngs=rngs)

    def causal_attention_mask(self, seq_len):
        return jnp.tril(jnp.ones((seq_len, seq_len), dtype=bool))

    def __call__(self, token_ids):
        seq_len = token_ids.shape[1]
        mask = self.causal_attention_mask(seq_len)
        x = self.embedding(token_ids)
        for i in range(self.num_transformer_blocks):
            block = getattr(self, f"block_{i}")
            x = block(x, mask=mask)
        x = self.final_norm(x)
        logits = self.output_layer(x)
        return logits
