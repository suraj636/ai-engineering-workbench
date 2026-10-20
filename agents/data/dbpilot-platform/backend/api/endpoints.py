from fastapi import APIRouter, HTTPException
from backend.models.dbpilot_models import DBPilotRequest, DBPilotResponse
from backend.pipeline.agents import run_dbpilot_pipeline
import os

router = APIRouter()

@router.post("/query", response_model=DBPilotResponse)
async def query_database(request: DBPilotRequest):
    """
    Main endpoint for the DBPilot Pipeline.
    Receives a natural language question and a database path/URL,
    runs the LangChain pipeline, and returns the SQL, Data, and natural language Answer.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set.")
        
    result = run_dbpilot_pipeline(request.question, request.db_path)
    
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result)
        
    return DBPilotResponse(**result)
