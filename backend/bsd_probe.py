"""Geçici teşhis: BSD (sports.bzzoiro.com) ücretsiz API'sinde Süper Lig kapsamı ve sakatlık verisi var mı?
Sonucu (anahtar içermez) sportoto_bulletin tablosundaki 'bsd_diag' satırına özet olarak yazar."""
import asyncio
import os
from datetime import datetime, timezone

import httpx

import accuracy

BASE = "https://sports.bzzoiro.com"


async def run_once() -> None:
    key = os.getenv("BSD_API_KEY")
    summary: dict = {"zaman": datetime.now(timezone.utc).isoformat()}
    if not key:
        summary["sonuc"] = "BSD_API_KEY tanımlı değil"
    else:
        try:
            headers = {"Authorization": f"Token {key}"}
            leagues: dict[str, int] = {}
            sample = None
            total = 0
            url = f"{BASE}/api/events/"
            async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
                for _ in range(6):
                    r = await client.get(url)
                    summary["http"] = r.status_code
                    if r.status_code != 200:
                        summary["govde"] = r.text[:200]
                        break
                    body = r.json()
                    rows = body.get("results") or []
                    total += len(rows)
                    for e in rows:
                        lg = e.get("league")
                        name = (lg.get("name") if isinstance(lg, dict) else lg) or e.get("league_name") or "?"
                        leagues[str(name)] = leagues.get(str(name), 0) + 1
                        if sample is None and e.get("unavailable_players"):
                            sample = {"alanlar": sorted(e.keys())[:40], "unavailable_players": str(e["unavailable_players"])[:300]}
                    url = body.get("next")
                    if not url:
                        break
            summary["toplam_mac"] = total
            summary["ligler"] = dict(sorted(leagues.items(), key=lambda kv: -kv[1])[:40])
            summary["turk_ligi_var"] = any(("turk" in k.lower() or "türk" in k.lower() or "süper" in k.lower() or "super lig" in k.lower()) for k in leagues)
            summary["ornek_sakatlik"] = sample
        except Exception as exc:  # noqa: BLE001
            summary["hata"] = str(exc)[:200]
    try:
        await accuracy._db("POST", "sportoto_bulletin", params={"on_conflict": "id"}, prefer=True, json_body={
            "id": "bsd_diag", "season": "", "week": "", "matches": summary, "source": "bsd",
            "updated_at": datetime.now(timezone.utc).isoformat()})
    except Exception:  # noqa: BLE001
        pass
