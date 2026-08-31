import os
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Bay Tahmin API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

NOSYAPI_BASE_URL = os.getenv("NOSYAPI_BASE_URL", "https://www.nosyapi.com/apiv2/service").rstrip("/")
NOSYAPI_KEY = os.getenv("NOSYAPI_KEY")
FOOTBALL_AI_AGENT_BASE_URL = os.getenv("FOOTBALL_AI_AGENT_BASE_URL", "https://futbol-ajan.vercel.app").rstrip("/")

@app.get("/")
def root():
    return {"status": "online", "agent": "Bay Tahmin", "ai_engine": "Football AI Agent", "message": "Bay Tahmin API çalışıyor."}

@app.get("/health")
def health():
    return {"status": "healthy", "nosyapi_configured": bool(NOSYAPI_KEY), "gemini_configured": False, "football_ai_agent": FOOTBALL_AI_AGENT_BASE_URL}

async def nosy_get(path: str, params: dict):
    if not NOSYAPI_KEY:
        raise HTTPException(status_code=500, detail="NOSYAPI_KEY environment variable bulunamadı.")
    params = {**params, "apiKey": NOSYAPI_KEY}
    url = f"{NOSYAPI_BASE_URL}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"NOSYAPI bağlantı hatası: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"NOSYAPI isteği başarısız oldu ({response.status_code}): {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="NOSYAPI geçerli JSON döndürmedi.") from exc

@app.get("/matches")
async def get_matches(date: str | None = None):
    params = {"type": 1}
    if date:
        params["date"] = date
    return await nosy_get("bettable-matches", params)

@app.get("/mac/{match_id}")
async def get_match_detail(match_id: int):
    return await nosy_get("bettable-matches/details", {"matchID": match_id})

@app.get("/match/{match_id}")
async def get_match_detail_alias(match_id: int):
    return await get_match_detail(match_id)

@app.get("/leagues")
def get_leagues():
    return []

async def football_agent_request(action: str, *, match_id: int | None = None, question: str | None = None, history=None):
    payload = {"action": action}
    if match_id is not None:
        payload["match_id"] = match_id
    # Some deployed Agent revisions validate `question` for every POST action.
    # Sending a harmless analysis prompt keeps old and new Agent deployments compatible.
    if action == "analyze_match" and question is None:
        question = "Bu maçı analiz et ve mevcut futbol verilerine göre kapsamlı analiz ile tahminlerini üret."
    if question is not None:
        payload["question"] = question
    if history:
        payload["history"] = history

    url = f"{FOOTBALL_AI_AGENT_BASE_URL}/api"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(url, params={"action": action}, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Football AI Agent bağlantı hatası: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Football AI Agent isteği başarısız oldu ({response.status_code}): {response.text[:500]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Football AI Agent geçerli JSON döndürmedi.") from exc
    if data.get("status") == "error":
        raise HTTPException(status_code=502, detail=data.get("message", "Football AI Agent hata döndürdü."))
    return data

@app.get("/ai/analyze/{match_id}")
async def analyze_match_with_ai(match_id: int):
    return await football_agent_request("analyze_match", match_id=match_id)

@app.post("/matches/{match_id}/chat")
async def chat_about_match(match_id: int, request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("question") or "").strip()
    history = payload.get("history") or []
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
    return await football_agent_request("chat_match", match_id=match_id, question=message, history=history)
