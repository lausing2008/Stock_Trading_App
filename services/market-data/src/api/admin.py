"""Admin endpoints: trigger ingestion + seed universe."""
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services.ingestion import ingest_symbol, ingest_universe
from ..services.seed_universe import seed

router = APIRouter(prefix="/admin", tags=["admin"])


class IngestRequest(BaseModel):
    symbols: list[str]
    timeframe: str = "1d"


@router.post("/seed")
def run_seed():
    count = seed()
    return {"status": "ok", "inserted": count}


@router.post("/ingest")
def run_ingest(req: IngestRequest, tasks: BackgroundTasks):
    if len(req.symbols) == 1:
        return ingest_symbol(req.symbols[0], timeframe=req.timeframe)
    tasks.add_task(ingest_universe, req.symbols, req.timeframe)
    return {"status": "scheduled", "symbols": len(req.symbols)}
