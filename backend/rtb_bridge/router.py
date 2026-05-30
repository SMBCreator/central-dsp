from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rtb", tags=["RTB Bridge"])


# ─────────────────────────────────────────
# RTB4FREE Bidder polls this endpoint to get active campaigns & creatives
# Response format must match RTB4FREE campaign schema
# ─────────────────────────────────────────
@router.get("/campaigns")
def get_rtb_campaigns():
    """
    Returns active campaigns in RTB4FREE-compatible format.
    RTB4FREE bidder polls this every N seconds.
    TODO: implement in Phase 1
    """
    return {"campaigns": [], "status": "ok"}


# ─────────────────────────────────────────
# Win notice — bidder calls this when we WIN an auction
# ─────────────────────────────────────────
@router.post("/win-notice")
async def win_notice(request: Request):
    """
    Called by RTB4FREE bidder when we win an auction.
    Records impression event + win_price in auction_logs.
    TODO: implement in Phase 1
    """
    body = await request.json()
    logger.info(f"[RTB WIN] {body}")
    return {"status": "ok"}


# ─────────────────────────────────────────
# Loss notice — bidder calls this when we LOSE an auction
# ─────────────────────────────────────────
@router.post("/loss-notice")
async def loss_notice(request: Request):
    """
    Called by RTB4FREE bidder when we lose an auction.
    Records loss in auction_logs for win rate calculation.
    TODO: implement in Phase 1
    """
    body = await request.json()
    logger.info(f"[RTB LOSS] {body}")
    return {"status": "ok"}


# ─────────────────────────────────────────
# Creatives endpoint — RTB4FREE fetches ad markup
# ─────────────────────────────────────────
@router.get("/creatives/{creative_id}")
def get_creative(creative_id: int):
    """
    Returns creative markup for a given creative_id.
    TODO: implement in Phase 1
    """
    raise HTTPException(status_code=404, detail="Creative not found")
