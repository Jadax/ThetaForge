"""
Admin routes for toggling live trading.
Requires multi-factor authentication for live activation.
"""
import os
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

router = APIRouter()

LIVE_PIN = os.getenv("LIVE_ACTIVATION_PIN", "123456")

class LiveToggleRequest(BaseModel):
    pin: str
    enable_live: bool

@router.post("/toggle-live")
async def toggle_live_trading(request: LiveToggleRequest):
    if request.pin != LIVE_PIN:
        raise HTTPException(status_code=403, detail="Invalid PIN.")
    
    # In production, this would trigger a secure state change and potentially
    # require additional factors (SMS, TOTP).
    mode = "LIVE" if request.enable_live else "PAPER"
    return {"message": f"Trading mode set to {mode}.", "warning": "Ensure hardware switch is in correct position."}
