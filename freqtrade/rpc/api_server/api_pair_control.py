from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from freqtrade.strategy.pair_control import pair_control_store


router = APIRouter()


class PairControlUpdate(BaseModel):
    pre_trade: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)


@router.get("/pair-controls")
def pair_controls():
    return {"version": 1, "pairs": pair_control_store.snapshot()}


@router.get("/pair-controls/{pair:path}")
def pair_control(pair: str):
    if not pair:
        raise HTTPException(status_code=400, detail="Pair is required")
    return {"version": 1, "pair": pair, "settings": pair_control_store.get(pair)}


@router.put("/pair-controls/{pair:path}")
def update_pair_control(pair: str, payload: PairControlUpdate):
    if not pair:
        raise HTTPException(status_code=400, detail="Pair is required")
    settings = pair_control_store.set(pair, payload.model_dump(exclude_unset=True))
    from freqtrade.rpc.api_server.webserver import ApiServer

    if ApiServer._message_stream:
        ApiServer._message_stream.publish(
            {"type": "pair_control", "data": {"version": 1, "pair": pair, "settings": settings}}
        )
    return {"version": 1, "pair": pair, "settings": settings}
