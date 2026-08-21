import os

import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Bay Tahmin API")

API_FOOTBALL_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")


@app.get("/")
def root():
    return {
        "status": "online",
        "agent": "Bay Tahmin",
        "message": "Bay Tahmin API çalışıyor."
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.get("/matches")
async def get_matches(date: str | None = None):
    if not API_FOOTBALL_KEY:
        raise HTTPException(
            status_code=500,
            detail="API_FOOTBALL_KEY environment variable bulunamadı."
        )

    params = {}
    if date:
        params["date"] = date

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{API_FOOTBALL_URL}/fixtures",
            headers=headers,
            params=params
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="API-Football isteği başarısız oldu."
        )

    return response.json()
