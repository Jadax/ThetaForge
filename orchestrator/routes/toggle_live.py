"""Paper-mode guardrail for the hosted Advisor API.

ThetaForge currently supports local, confirmed paper orders only. A hosted
endpoint must never be able to flip an execution mode, regardless of a PIN.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class LiveToggleRequest(BaseModel):
    pin: str
    enable_live: bool

@router.post("/toggle-live")
async def toggle_live_trading(request: LiveToggleRequest):
    if request.enable_live:
        raise HTTPException(
            status_code=403,
            detail="Live trading is disabled. ThetaForge currently supports confirmed paper orders only.",
        )
    return {"mode": "PAPER", "message": "Paper-only mode is enforced."}
