"""Shared data shapes used across the service layer and the API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class HistoricalTxn(BaseModel):
    description: str
    payee: Optional[str] = None
    category: str


class CompanyContext(BaseModel):
    company_id: str
    industry: str
    chart_of_accounts: List[str]
    historical_transactions: List[HistoricalTxn] = Field(default_factory=list)


class Transaction(BaseModel):
    description: str
    payee: Optional[str] = None


class LLMSuggestion(BaseModel):
    """Raw shape we expect back from the LLM (before validation/heuristics)."""
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: Optional[str] = None


class CategorizationResult(BaseModel):
    """Final response shape returned by the API."""
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    is_valid_category: bool
    model: str
