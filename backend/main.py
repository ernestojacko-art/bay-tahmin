from fastapi import FastAPI

app = FastAPI(title="Bay Tahmin API")


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
