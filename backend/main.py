import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai

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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
@app.get("/mac/{match_id}")
async def get_match_detail(match_id: int):
    if not NOSYAPI_KEY:
        raise HTTPException(
            status_code=500,
            detail="NOSYAPI_KEY environment variable bulunamadı."
        )

    params = {
        "apiKey": NOSYAPI_KEY,
        "matchID": match_id
    }

    url = f"{NOSYAPI_BASE_URL}/bettable-matches/details"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            params=params
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"NOSYAPI maç detay isteği başarısız oldu: {response.text}"
        )

    return response.json()
@app.get("/match/{match_id}")
async def get_match_detail_alias(match_id: int):
    return await get_match_detail(match_id)

@app.get("/ai/analyze/{match_id}")
async def analyze_match_with_ai(match_id: int):
    if not GEMINI_API_KEY or not gemini_client:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY environment variable bulunamadı."
        )

    # Önce gerçek maç verisini NOSYAPI'den al
    match_data = await get_match_detail(match_id)

    prompt = f"""
Sen "Bay Tahmin AI" adlı profesyonel bir futbol maç analiz asistanısın.

Aşağıdaki veriler gerçek maç verileridir. 
Verilerde olmayan hiçbir bilgiyi uydurma.

MAÇ VERİSİ:
{match_data}

Bu maçı detaylı şekilde analiz et.

Şu başlıkları mutlaka oluştur:

1. MAÇ ÖZETİ
2. TAKIMLARIN DURUMU
3. MAÇIN OLASI SENARYOSU
4. MS TAHMİNİ
5. KG TAHMİNİ
6. ALT / ÜST TAHMİNİ
7. İLK YARI TAHMİNİ
8. HT/FT TAHMİNİ
9. SÜRPRİZ İHTİMALİ
10. EN GÜVENİLİR TAHMİNLER
11. RİSK SEVİYESİ
12. TAHMİN GEREKÇESİ

Her tahmin için kısa ama mantıklı bir gerekçe ver.

Kesin sonuç garantisi verme.
Tahminleri olasılık ve risk mantığıyla değerlendir.

Yanıtı Türkçe ver.
"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        return {
            "status": "success",
            "matchID": match_id,
            "analysis": response.text
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Gemini analiz hatası: {str(e)}"
        )
