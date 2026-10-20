import logging
import jax

from src.infer import load_trained_model, generate_story

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

SAMPLE_PROMPTS = [
    "Once upon a time there was a big bear",
    "A little girl named Lily went to the park",
    "The magic tree was very tall",
    "One day, a friendly dog found a bone",
    "Tom wanted to build a spaceship",
    "The sun was shining brightly when",
    "A tiny mouse peeked out of its hole",
    "In the deep dark forest, a mystery",
    "The old man had a secret box",
    "A shiny red car zoomed past"
]

def main():
    print("Loading model for testing...")
    try:
        model, tok, pad_id, eos_id = load_trained_model()
    except FileNotFoundError as e:
        print(f"Could not load model: {e}")
        print("Please run the training script first: python -m src.train")
        return

    print("Model loaded successfully. Generating 10 sample stories...\n")
    
    rng = jax.random.PRNGKey(42)
    
    for i, prompt in enumerate(SAMPLE_PROMPTS, 1):
        print(f"--- Query {i} ---")
        print(f"Prompt: {prompt}")
        
        # Generate with temperature 0.8
        text, rng = generate_story(
            model, tok, prompt, pad_id, eos_id, rng, 
            temperature=0.8, max_new_tokens=60
        )
        
        print(f"Generated text: \n{text}\n")

if __name__ == "__main__":
    main()
