from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import uvicorn

# Import the agent logic from the backend module
from backend.agent import run_agent

app = FastAPI(title="Media Agent API")

# Enable CORS for the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup directories relative to the root where main.py now lives
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "backend", "static")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# Ensure static directory exists
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount the static images directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class GenerateRequest(BaseModel):
    url: str

@app.post("/api/generate")
async def generate_infographic(request: GenerateRequest):
    if not request.url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    try:
        # Run the agent workflow
        image_path = await run_agent(request.url)
        
        if not image_path or "Error" in image_path:
            raise HTTPException(status_code=500, detail=f"Failed to generate infographic: {image_path}")
            
        # Return the filename or relative URL path
        filename = os.path.basename(image_path)
        return {"status": "success", "image_url": f"/static/{filename}"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mount frontend files at root
@app.get("/")
async def serve_frontend_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
