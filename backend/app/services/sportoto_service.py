"""
Spor Toto: haftalık bülten (15 maç) analizi ve kupon (Ekonomik / Normal / Yüksek / bütçeli) üretimi.

- Bülten Supabase `sportoto_bulletin` tablosundaki `current` satırından okunur (elle girilir veya
  ileride resmî servisten çekilir).
- Her bülten maçı, gerçek fikstürle (takım adı benzerliği + tarih) eşleştirilir ve normal analiz motoruyla
  (istatistik + bahis oranı karışımı) 1/X/2 olasılığı alınır. Eşleşmeyen / verisi olmayan maç UYDURULMAZ:
  "veri yok" işaretlenir ve kuponda en belirsiz maç gibi (geniş kapsamla) ele alınır.
- Kupon: 15 maçın her biri için 1, 2 veya 3 seçenek seçilir; toplam kolon sayısı sınırı içinde, 15 maçın
  hepsini bilme olasılığı en yüksek olacak şekilde (açgözlü, oran/kolon maliyeti) seçenek eklenir.
"""
from __future__ import annotations

import asyncio
import difflib
import os
import logging
import math
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import accuracy
import httpx
from app.services.analysis_service import AnalysisService

logger = logging.getLogger("sportoto")
ISTANBUL = ZoneInfo("Europe/Istanbul")

OUTCOMES = ("1", "X", "2")
DEFAULT_COLUMN_PRICE = 10.0  # Kolon bedeli 10 TL (kullanıcı doğruladı).
# Kupon türü -> en fazla kolon sayısı
TIERS: dict[str, dict[str, Any]] = {
    "ekonomik": {"label": "Ekonomik", "max_columns": 12,
                 "note": "Az kolon, düşük maliyet: yalnızca en belirsiz birkaç maçta iki seçenek."},
    "normal": {"label": "Normal", "max_columns": 48,
               "note": "Dengeli maliyet: belirsiz maçlarda iki, çok belirsizlerde üç seçenek."},
    "yuksek": {"label": "Yüksek", "max_columns": 192,
               "note": "Geniş kapsam, yüksek maliyet: sürpriz ihtimali olan maçlar korunur."},
}
ANALYSIS_ROW = "analysis"
STALE_SECONDS = 12 * 60 * 60

_NOISE = {"fk", "fc", "sk", "as", "ac", "sc", "cf", "afc", "spor", "sportif", "faaliyetler", "kulubu", "futbol",
          "corendon", "tumosan", "caykur", "arca", "trendyol", "1907", "1903", "1905", "club", "de", "st", "saint"}
_ALIASES = {"paris st germain": "paris saint germain", "psg": "paris saint germain",
            "olympique lyon": "lyon", "olympique lyonnais": "lyon", "borussia dortmund": "dortmund",
            "eintracht frankfurt": "frankfurt", "tottenham hotspur": "tottenham", "spurs": "tottenham",
            "istanbul basaksehir": "basaksehir", "genclerbirligi": "genclerbirligi",
            "ac monaco": "monaco", "as monaco": "monaco", "ssc napoli": "napoli", "as roma": "roma"}


def _fold(text: str) -> str:
    text = (text or "").replace("İ", "i").replace("I", "i").replace("ı", "i")
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def _key(name: str) -> str:
    folded = re.sub(r"\s+", " ", _fold(name))
    folded = _ALIASES.get(folded, folded)
    tokens = [t for t in folded.split() if t not in _NOISE]
    return " ".join(tokens) or folded


def _name_score(a: str, b: str) -> float:
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ta, tb = set(ka.split()), set(kb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return 0.92
    return difflib.SequenceMatcher(None, ka, kb).ratio()


# --------------------------------------------------------------------------- kupon

def _log_ratio(new: float, old: float) -> float:
    return math.log(new) - math.log(old)


def build_coupon(probs: list[list[float]], max_columns: int) -> dict[str, Any]:
    """probs: her maç için [p1, pX, p2]. Dönen: seçimler, kolon sayısı ve 15-bilme olasılığı."""
    n = len(probs)
    order = [sorted(range(3), key=lambda i: p[i], reverse=True) for p in probs]
    chosen = [1] * n  # her maçta seçilen seçenek sayısı (en olasıdan başlayarak)
    columns = 1

    def covered(i: int) -> float:
        return sum(probs[i][o] for o in order[i][:chosen[i]])

    while True:
        best, best_gain = None, 0.0
        for i in range(n):
            k = chosen[i]
            if k >= 3:
                continue
            new_columns = columns // k * (k + 1)
            if new_columns > max_columns:
                continue
            added = probs[i][order[i][k]]
            gain = _log_ratio(covered(i) + added, covered(i)) / _log_ratio(k + 1, k)
            if gain > best_gain:
                best, best_gain = i, gain
        if best is None:
            break
        columns = columns // chosen[best] * (chosen[best] + 1)
        chosen[best] += 1

    picks = []
    hit_all = 1.0
    for i in range(n):
        sel = sorted(order[i][:chosen[i]])
        picks.append({"selection": "-".join(OUTCOMES[o] for o in sel), "coverage": round(covered(i), 4)})
        hit_all *= covered(i)
    return {"picks": picks, "columns": columns, "hit_all_probability": hit_all}


# --------------------------------------------------------------------------- servis

class SportotoService:
    _analysis_cache: dict[str, Any] = {"ts": 0.0, "data": None}
    _task: asyncio.Task | None = None

    def __init__(self, analysis_service: AnalysisService):
        self._analysis = analysis_service

    # -- bülten
    @staticmethod
    async def load_bulletin() -> dict[str, Any] | None:
        rows = await accuracy._db("GET", "sportoto_bulletin", params={"id": "eq.current", "select": "season,week,matches,source,updated_at"})
        return rows[0] if rows else None

    @staticmethod
    async def save_bulletin(season: str, week: str, matches: list[dict[str, Any]], source: str = "elle giriş") -> None:
        await accuracy._db("POST", "sportoto_bulletin", params={"on_conflict": "id"}, prefer=True, json_body={
            "id": "current", "season": season, "week": week, "matches": matches, "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat()})
        SportotoService._analysis_cache = {"ts": 0.0, "data": None}


    # -- NosyAPI ile haftalık bülten (otomatik)
    NOSY_URL = "https://www.nosyapi.com/apiv2/service/bettable-matches/sporToto"

    @staticmethod
    def _parse_nosy(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        """NosyAPI yanıtını bülten maçlarına çevirir. Beklenen biçime uymayan veri KAYDEDİLMEZ."""
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) != 15:
            return None
        out: list[dict[str, Any]] = []
        for i, r in enumerate(rows, start=1):
            home, away = str(r.get("Team1") or "").strip(), str(r.get("Team2") or "").strip()
            raw_dt = str(r.get("DateTime") or "").strip()
            if not home or not away or not raw_dt:
                return None
            try:
                dt = datetime.fromisoformat(raw_dt.replace(" ", "T"))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ISTANBUL)
            out.append({"n": i, "home": home, "away": away, "kickoff": dt.isoformat()})
        return out

    async def refresh_bulletin(self) -> str:
        """Güncel bülten bitmişse NosyAPI'den yeni haftanın programını çeker. Dönüş: durum açıklaması."""
        key = os.getenv("NOSYAPI_KEY")
        if not key:
            return "NOSYAPI_KEY tanımlı değil"
        current = await self.load_bulletin()
        if current:
            last = max(datetime.fromisoformat(m["kickoff"]) for m in current["matches"])
            if last + timedelta(hours=4) > datetime.now(timezone.utc):
                return "güncel bülten henüz bitmedi"
        # Kredi tasarrufu (her çağrı 15 kredi): son çağrıdan 12 saat geçmeden tekrar sorma (yeniden başlatmada da korunur).
        try:
            rows = await accuracy._db("GET", "sportoto_bulletin", params={"id": "eq.nosy_check", "select": "updated_at"})
            if rows and datetime.now(timezone.utc) - datetime.fromisoformat(str(rows[0]["updated_at"]).replace("Z", "+00:00")) < timedelta(hours=12):
                return "NosyAPI son 12 saatte sorgulandı"
            await accuracy._db("POST", "sportoto_bulletin", params={"on_conflict": "id"}, prefer=True, json_body={
                "id": "nosy_check", "season": "", "week": "", "matches": [], "source": "nosyapi",
                "updated_at": datetime.now(timezone.utc).isoformat()})
        except Exception as exc:  # noqa: BLE001
            logger.warning("sportoto: nosy_check okunamadı: %s", exc)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(self.NOSY_URL, headers={"X-NSYP": key})
        if resp.status_code != 200:
            return f"NosyAPI yanıtı {resp.status_code}"
        matches = self._parse_nosy(resp.json())
        if matches is None:
            return "NosyAPI yanıtı beklenen 15 maçlık biçimde değil"
        if current:
            same = {(m["home"], m["away"]) for m in current["matches"]} == {(m["home"], m["away"]) for m in matches}
            if same:
                return "NosyAPI hâlâ eski haftayı veriyor"
            try:
                week = str(int(current["week"]) + 1)
            except (TypeError, ValueError):
                week = ""
            season = current["season"]
        else:
            week, season = "", ""
        await self.save_bulletin(season, week, matches, source="NosyAPI (otomatik)")
        self._kick_background()
        logger.info("sportoto: yeni bülten NosyAPI'den alındı (hafta %s)", week)
        return "yeni bülten kaydedildi"

    async def bulletin_loop(self) -> None:
        from app.core import api_gate
        api_gate.background.set(True)
        await asyncio.sleep(45)
        while True:
            try:
                logger.info("sportoto: bülten kontrolü: %s", await self.refresh_bulletin())
            except Exception as exc:  # noqa: BLE001
                logger.warning("sportoto: bülten kontrolü başarısız: %s", exc)
            await asyncio.sleep(6 * 3600)

    # -- eşleştirme + analiz
    async def _fixtures_for(self, matches: list[dict[str, Any]]) -> list:
        provider = self._analysis._provider
        days: set[date] = set()
        for m in matches:
            k = datetime.fromisoformat(m["kickoff"]).astimezone(ISTANBUL).date()
            days.add(k)
        fixtures: list = []
        for day in sorted(days):
            try:
                fixtures += await provider.get_fixtures(day)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sportoto: %s fikstürü alınamadı: %s", day, exc)
        return fixtures

    @staticmethod
    def _match_fixture(match: dict[str, Any], fixtures: list):
        kickoff = datetime.fromisoformat(match["kickoff"])
        best, best_score = None, 0.0
        for f in fixtures:
            if abs((f.kickoff - kickoff).total_seconds()) > 36 * 3600:
                continue
            score = (_name_score(match["home"], f.home_team.name) + _name_score(match["away"], f.away_team.name)) / 2
            if score > best_score:
                best, best_score = f, score
        return (best, best_score) if best is not None and best_score >= 0.74 else (None, best_score)

    async def _analyze_one(self, match: dict[str, Any], fixture, sem: asyncio.Semaphore) -> dict[str, Any]:
        out: dict[str, Any] = {"n": match["n"], "home": match["home"], "away": match["away"], "kickoff": match["kickoff"],
                               "available": False, "probs": None, "rationale": None, "league": None,
                               "market": None, "confidence": None, "note": None,
                               "match_id": getattr(fixture, "match_id", None)}
        if fixture is None:
            out["note"] = "Bu maç için veri kaynağında eşleşen bir fikstür bulunamadı; analiz yapılamadı."
            return out
        out["league"] = fixture.league_name
        try:
            async with sem:
                pred = await self._analysis.analyze_match(fixture.match_id, include_evidence=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sportoto: %s analiz edilemedi: %s", match["home"], exc)
            out["note"] = "Bu maçın analizi şu an alınamadı (veri kaynağı sınırı olabilir)."
            return out
        ox = pred.one_x_two
        out["probs"] = [ox.home_win, ox.draw, ox.away_win]
        out["available"] = True
        out["rationale"] = pred.analysis_rationale
        out["confidence"] = pred.confidence.confidence
        mi = pred.market_comparison.market_implied if pred.market_comparison.market_available else None
        out["market"] = [mi.home_win, mi.draw, mi.away_win] if mi else None
        out["data_quality"] = pred.data_quality.value
        return out

    async def compute_analysis(self) -> dict[str, Any]:
        bulletin = await self.load_bulletin()
        if not bulletin:
            return {"status": "no_bulletin", "matches": []}
        matches = bulletin["matches"]
        fixtures = await self._fixtures_for(matches)
        sem = asyncio.Semaphore(3)
        tasks = []
        for m in matches:
            fx, _score = self._match_fixture(m, fixtures)
            tasks.append(self._analyze_one(m, fx, sem))
        results = await asyncio.gather(*tasks)
        data = {"status": "ready", "season": bulletin["season"], "week": bulletin["week"],
                "bulletin_updated_at": bulletin.get("updated_at"), "source": bulletin.get("source"),
                "generated_at": datetime.now(timezone.utc).isoformat(), "matches": results}
        try:
            await accuracy._db("POST", "sportoto_bulletin", params={"on_conflict": "id"}, prefer=True, json_body={
                "id": ANALYSIS_ROW, "season": bulletin["season"], "week": bulletin["week"], "matches": data,
                "source": "analiz", "updated_at": data["generated_at"]})
        except Exception as exc:  # noqa: BLE001
            logger.warning("sportoto: analiz kaydedilemedi: %s", exc)
        return data

    @staticmethod
    async def record_tracking(analysis: dict[str, Any]) -> int:
        """Bülten maçlarının tek seçimlik (en olası 1/X/2) tahminini takibe kaydeder; sonuçlar otomatik işlenir."""
        saved = 0
        for m in analysis.get("matches", []):
            probs, mid = m.get("probs"), m.get("match_id")
            if not probs or not mid:
                continue
            best = max(range(3), key=lambda i: probs[i])
            try:
                ok = await accuracy.save_prediction({
                    "match_id": mid, "kickoff": m["kickoff"], "competition": m.get("league"),
                    "home_team": m["home"], "away_team": m["away"], "market_type": "1x2",
                    "selection": OUTCOMES[best], "market_probability": round(probs[best], 4), "odd": None,
                    "card": "sportoto", "week": f"{analysis.get('season')} / {analysis.get('week')}", "n": m["n"]})
                saved += 1 if ok else 0
            except Exception as exc:  # noqa: BLE001
                logger.warning("sportoto: tahmin kaydedilemedi (%s): %s", m.get("home"), exc)
        return saved

    async def _load_saved_analysis(self) -> dict[str, Any] | None:
        try:
            rows = await accuracy._db("GET", "sportoto_bulletin", params={"id": f"eq.{ANALYSIS_ROW}", "select": "matches,updated_at,week"})
            bulletin = await self.load_bulletin()
        except Exception:  # noqa: BLE001
            return None
        if not rows or not bulletin:
            return None
        data = rows[0]["matches"]
        if not isinstance(data, dict) or data.get("week") != bulletin["week"]:
            return None
        if data.get("bulletin_updated_at") != bulletin.get("updated_at"):
            return None  # bülten değişmiş: yeniden analiz
        return data

    async def get_analysis(self) -> dict[str, Any]:
        """Hazır analizi hemen döner; yoksa arka planda hesaplatır ve 'hazırlanıyor' bilgisi verir."""
        cache = SportotoService._analysis_cache
        if cache["data"] is not None and time.time() - cache["ts"] < STALE_SECONDS:
            return cache["data"]
        saved = await self._load_saved_analysis()
        if saved is not None:
            age = time.time() - datetime.fromisoformat(saved["generated_at"]).timestamp()
            SportotoService._analysis_cache = {"ts": time.time() - min(age, STALE_SECONDS - 60), "data": saved}
            if age > STALE_SECONDS:
                self._kick_background()
            return saved
        self._kick_background()
        bulletin = await self.load_bulletin()
        if not bulletin:
            return {"status": "no_bulletin", "matches": []}
        return {"status": "preparing", "season": bulletin["season"], "week": bulletin["week"],
                "matches": [{"n": m["n"], "home": m["home"], "away": m["away"], "kickoff": m["kickoff"],
                             "available": False, "probs": None, "note": "Analiz hazırlanıyor."} for m in bulletin["matches"]]}

    def _kick_background(self) -> None:
        if SportotoService._task is not None and not SportotoService._task.done():
            return

        async def run():
            from app.core import api_gate
            api_gate.background.set(True)
            try:
                data = await self.compute_analysis()
                if data.get("status") == "ready":
                    SportotoService._analysis_cache = {"ts": time.time(), "data": data}
            except Exception as exc:  # noqa: BLE001
                logger.warning("sportoto: arka plan analizi başarısız: %s", exc)

        SportotoService._task = asyncio.create_task(run())

    # -- kuponlar
    @staticmethod
    def _coupon_payload(analysis: dict[str, Any], max_columns: int, price: float, label: str, note: str) -> dict[str, Any]:
        matches = analysis["matches"]
        # Verisi olmayan maç: en belirsiz kabul edilir (geniş kapsam), uydurma sayı gösterilmez.
        neutral = [0.36, 0.30, 0.34]
        probs = [m["probs"] if m.get("probs") else neutral for m in matches]
        built = build_coupon(probs, max_columns)
        rows = []
        for m, p, pick in zip(matches, probs, built["picks"]):
            sel = pick["selection"]
            if not m.get("probs"):
                reason = "Bu maç için veri bulunamadı; en belirsiz maç sayıldı."
                if len(sel.split("-")) < 2:
                    reason = "Bu maç için veri bulunamadı; seçim isteğe göre değiştirilmelidir."
            else:
                best = OUTCOMES[max(range(3), key=lambda i: p[i])]
                names = {"1": m["home"], "X": "Beraberlik", "2": m["away"]}
                if len(sel.split("-")) == 1:
                    reason = f"{names[best]} açık farkla öne çıkıyor (%{max(p)*100:.0f})."
                else:
                    parts = " ve ".join(names[s] if s == "X" else names[s] for s in sel.split("-"))
                    reason = f"Maç dengeli olduğu için {sel} birlikte seçildi: {parts} toplam %{pick['coverage']*100:.0f}."
            rows.append({"n": m["n"], "home": m["home"], "away": m["away"], "selection": sel,
                         "coverage": pick["coverage"], "reason": reason, "has_data": bool(m.get("probs"))})
        return {"label": label, "note": note, "columns": built["columns"], "price_per_column": price,
                "total_cost": round(built["columns"] * price, 2),
                "hit_all_probability": built["hit_all_probability"], "picks": rows}

    async def coupons(self, price: float = DEFAULT_COLUMN_PRICE) -> dict[str, Any]:
        analysis = await self.get_analysis()
        if analysis.get("status") != "ready":
            return {"status": analysis.get("status"), "coupons": {}}
        return {"status": "ready", "week": analysis["week"], "season": analysis["season"],
                "coupons": {k: self._coupon_payload(analysis, t["max_columns"], price, t["label"], t["note"])
                            for k, t in TIERS.items()}}

    async def budget_coupon(self, budget: float, price: float = DEFAULT_COLUMN_PRICE) -> dict[str, Any]:
        analysis = await self.get_analysis()
        if analysis.get("status") != "ready":
            return {"status": analysis.get("status")}
        columns = max(1, int(budget // max(price, 0.01)))
        columns = min(columns, 3 ** 15)
        payload = self._coupon_payload(analysis, columns, price, f"{budget:g} TL'lik kupon",
                                       "Bütçene sığacak en fazla kolonla, 15 maçı bilme olasılığını en yükseğe çıkaracak şekilde kuruldu.")
        payload.update({"status": "ready", "budget": budget})
        return payload
