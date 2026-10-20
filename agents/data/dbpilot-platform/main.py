from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = FastAPI(
    title="DBPilot Platform API",
    description="Backend API for the Natural Language Query Pipeline",
    version="1.0.0"
)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Configure CORS for the frontend (allowing all for local development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.api.endpoints import router as dbpilot_router

app.include_router(dbpilot_router, prefix="/api")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Serve the static frontend files directly at the root.
# (This must be after the API routes so it doesn't swallow /api requests)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
