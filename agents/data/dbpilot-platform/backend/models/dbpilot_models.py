from pydantic import BaseModel
from typing import List, Optional, Any, Dict

class DBPilotRequest(BaseModel):
    question: str
    db_path: str

class DBPilotResponse(BaseModel):
    status: str
    question: str
    generated_sql: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None
    answer: Optional[str] = None
    message: Optional[str] = None
    details: Optional[str] = None
