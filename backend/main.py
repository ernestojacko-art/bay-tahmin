import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Bay Tahmin API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

NOSYAPI_BASE_URL = os.getenv(
    "NOSYAPI_BASE_URL",
    "https://www.nosyapi.com/apiv2/service"
)

NOSYAPI_KEY = os.getenv("NOSYAPI_KEY")


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

    if not NOSYAPI_KEY:
        raise HTTPException(
            status_code=500,
            detail="NOSYAPI_KEY environment variable bulunamadı."
        )

    params = {
        "apiKey": NOSYAPI_KEY,
        "type": 1
    }

    if date:
        params["date"] = date

    url = f"{NOSYAPI_BASE_URL}/bettable-matches"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            params=params
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"NosyAPI isteği başarısız oldu: {response.text}"
        )

    return response.json()
