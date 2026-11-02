from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from ..quotes import ET, compute_quote

router = APIRouter()


@router.get("/quotes")
async def get_quotes(request: Request, symbols: str | None = Query(
        default=None, description="Comma-separated; default: every tracked symbol")) -> list[dict]:
    state = request.app.state
    tracked: list[str] = state.symbols
    wanted = tracked if not symbols else [s.strip().upper() for s in symbols.split(",") if s.strip()]
    unknown = sorted(set(wanted) - set(tracked))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Untracked symbols: {unknown}")
    if getattr(state, "snapshots", None) is None:
        raise HTTPException(status_code=503, detail="Quotes need ALPACA_API_KEY/SECRET")
    try:
        snaps = await state.snapshots.get(tracked)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Quote source unavailable") from exc
    today = datetime.now(ET).date()
    return [
        compute_quote(s, snaps.get(s, {}), state.feed.last_trades.get(s), today) for s in wanted
    ]
