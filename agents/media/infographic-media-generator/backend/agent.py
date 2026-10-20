import os
import datetime
import requests
import PIL.Image
from google import genai
from google.genai import types
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models import Gemini
from google.adk.runners import InMemoryRunner
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Try to import helper, if it's part of the user's environment
try:
    from helper import authenticate
    credentials, project_id = authenticate()
except ImportError:
    print("Warning: helper module not found. Falling back to default auth.")
    credentials = None
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "default_project")

LOG_FILE = "infographic_agent.log"

def log_step(message: str):
    timestamp = datetime.datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(f"[{timestamp}] {message}")

class DLAIGemini(Gemini):
    _client: genai.Client = None  # class-level cache

    @property
    def api_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(
                project=project_id,
                location="global",
                credentials=credentials,
                http_options=types.HttpOptions(
                    base_url=os.getenv("GOOGLE_VERTEX_BASE_URL")
                ) if os.getenv("GOOGLE_VERTEX_BASE_URL") else None
            )
        return self._client

# Create a singleton client for the functions
_global_client = None

def get_client():
    global _global_client
    if _global_client is not None:
        return _global_client
    
    api_key = os.getenv("GEMINI_API_KEY")
    if credentials is None and not api_key:
        raise ValueError("Missing authentication. Please set GEMINI_API_KEY in .env or provide a service account.")
        
    try:
        _global_client = genai.Client(
            project=project_id,
            location="global",
            credentials=credentials,
            http_options=types.HttpOptions(
                 base_url=os.getenv("GOOGLE_VERTEX_BASE_URL")
            ) if os.getenv("GOOGLE_VERTEX_BASE_URL") else None
        )
        return _global_client
    except Exception as e:
        log_step(f"Error initializing client: {e}")
        raise e

def fetch_blog_content(url: str) -> str:
    """Fetches the text content of a blog post."""
    log_step(f"Fetching blog content from: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        content = response.text
        log_step("Successfully fetched blog content.")
        return content[:5000] # Limit content for prompt efficiency
    except Exception as e:
        log_step(f"Error fetching blog content: {e}")
        return f"Error: {e}"

def generate_infographic(blog_content: str, feedback: str = "") -> str:
    """Generates an infographic using Nano Banana."""
    log_step("Generating infographic...")
    prompt = f"Create a professional infographic based on this blog content: {blog_content}."
    if feedback:
        log_step(f"Applying feedback for regeneration: {feedback}")
        prompt += f" Please fix the following issues from the previous attempt: {feedback}"

    try:
        client = get_client()
        response = client.models.generate_content(
            model="gemini-3.1-flash-image-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            )
        )

        image_part = next(p for p in response.candidates[0].content.parts if p.inline_data)
        
        # Ensure static directory exists
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        os.makedirs(static_dir, exist_ok=True)
        
        image_filename = f"infographic_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        image_path = os.path.join(static_dir, image_filename)
        
        with open(image_path, "wb") as f:
            f.write(image_part.inline_data.data)

        log_step(f"Infographic saved as {image_path}")
        return image_path
    except Exception as e:
        log_step(f"Error generating infographic: {e}")
        return f"Error: {e}"

def evaluate_infographic(image_path: str, blog_content: str) -> str:
    """Evaluates the image for factual accuracy, spelling, and aesthetics."""
    log_step(f"Evaluating infographic: {image_path}")
    try:
        img = PIL.Image.open(image_path)

        evaluation_prompt = (
            "Analyze this infographic against the following blog content for:\n"
            "1. Factual Accuracy: Does it correctly represent the information?\n"
            "2. Spelling: Are there any typos or spelling errors in the text?\n"
            "3. Aesthetic Alignment: Does the style match a professional blog?\n\n"
            "If it fails any criteria, provide specific feedback for improvement. "
            "If it passes all criteria, respond ONLY with 'PASS'.\n\n"
            f"Blog Content: {blog_content}"
        )

        client = get_client()
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=[img, evaluation_prompt]
        )

        result = response.text.strip()
        log_step(f"Evaluation result: {result}")
        return result
    except Exception as e:
        log_step(f"Error evaluating infographic: {e}")
        return f"Evaluation Error: {e}"

def infographic_workflow(url: str) -> str:
    """The main workflow for generating and validating the infographic."""
    log_step(f"Starting infographic workflow for URL: {url}")

    content = fetch_blog_content(url)
    if "Error" in content:
        return content

    max_attempts = 3
    feedback = ""
    last_image_path = ""

    for attempt in range(max_attempts):
        log_step(f"Attempt {attempt + 1} of {max_attempts}")
        image_path = generate_infographic(content, feedback)
        last_image_path = image_path

        if "Error" in image_path:
            return image_path

        eval_result = evaluate_infographic(image_path, content)

        if eval_result == "PASS":
            log_step("Infographic passed evaluation.")
            return image_path
        else:
            feedback = eval_result
            log_step(f"Regenerating due to feedback: {feedback}")

    log_step("Reached maximum attempts. Returning last version.")
    return last_image_path

# Define the ADK Agent
root_agent = LlmAgent(
    name="InfographicAgent",
    model=DLAIGemini(model="gemini-3-flash-preview"),
    instruction=(
        "You are an expert at creating infographics from blog posts. "
        "Use the tools provided to fetch content, generate an image, and validate it. "
        "Always log your progress."
    ),
    tools=[infographic_workflow]
)

# Async function to run the agent programmatically from FastAPI
async def run_agent(url: str) -> str:
    """
    Runs the agent for a given URL and returns the generated image filename.
    """
    runner = InMemoryRunner(agent=root_agent, app_name="image_agent")
    session = await runner.session_service.create_session(
        app_name="image_agent", user_id="user"
    )

    user_message = types.Content(
        role="user",
        parts=[types.Part(text=f"Create an infographic from this blog: {url}")]
    )

    result_image_path = ""

    # Run the agent
    async for event in runner.run_async(
        user_id="user",
        session_id=session.id,
        new_message=user_message,
    ):
        # We look for the final string returned that contains the path
        # In our case, infographic_workflow returns the path directly on success or failure.
        # But if the agent wraps it, we might need to parse.
        # For simplicity, since the workflow returns the path, let's just trace logs or assume the agent returns the path.
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text and "static" in part.text and ".png" in part.text:
                    # Extract path from agent's response if possible
                    import re
                    match = re.search(r'([\w\/\\]+\\static\\[\w\.\-]+png)', part.text)
                    if not match:
                        match = re.search(r'([\w\/\\]+/static/[\w\.\-]+png)', part.text)
                    if match:
                        result_image_path = match.group(1)
    
    # If the agent didn't clearly output the path in the final response, 
    # we can just find the most recently created file in static folder
    if not result_image_path:
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        if os.path.exists(static_dir):
            files = [os.path.join(static_dir, f) for f in os.listdir(static_dir) if f.endswith('.png')]
            if files:
                result_image_path = max(files, key=os.path.getmtime)
                
    return result_image_path
