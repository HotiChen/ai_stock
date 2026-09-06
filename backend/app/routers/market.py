from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.market import MarketSnapshot, IndexQuote, FxQuote

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/snapshot", response_model=MarketSnapshot)
async def snapshot(current_user: User = Depends(get_current_user)) -> MarketSnapshot:
    """狀態列的大盤快照。

    這個端點原本**無條件**回一份寫死的快照（加權 21,845.3、DB 42.5MB、
    LOAD 8.3ms…），完全沒有真實實作。畫面底部那一條狀態列因此從頭到尾
    都是假的，而且看起來完全正常。

    在真的接上市場資料之前，明說沒有——前端會顯示 "—"。
    """
    raise HTTPException(
        status_code=501,
        detail="大盤快照尚未接上真實資料源。",
    )
