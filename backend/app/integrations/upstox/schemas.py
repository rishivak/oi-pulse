"""Pydantic models for Upstox API responses."""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class UpstoxMarketData(BaseModel):
    ltp: float | None = None
    volume: int | None = None
    oi: int | None = None
    close_price: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    prev_oi: int | None = None


class UpstoxOptionGreeks(BaseModel):
    vega: float | None = None
    theta: float | None = None
    gamma: float | None = None
    delta: float | None = None
    iv: float | None = None


class UpstoxOptionLeg(BaseModel):
    instrument_key: str | None = None
    market_data: UpstoxMarketData | None = None
    option_greeks: UpstoxOptionGreeks | None = None


class UpstoxOptionChainRow(BaseModel):
    expiry: str
    pcr: float | None = None
    strike_price: float
    underlying_key: str | None = None
    underlying_spot_price: float | None = None
    call_options: UpstoxOptionLeg | None = None
    put_options: UpstoxOptionLeg | None = None


class UpstoxOptionChainResponse(BaseModel):
    status: str
    data: list[UpstoxOptionChainRow] = Field(default_factory=list)


class UpstoxTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    # Upstox v2 doesn't always return a refresh_token in OAuth flow
    extended_token: str | None = None

    class Config:
        extra = "allow"


class UpstoxProfileResponse(BaseModel):
    status: str
    data: dict[str, Any] = Field(default_factory=dict)
