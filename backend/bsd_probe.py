"""Geçici teşhis: BSD v2 uçlarında gol türü / takım istatistiği verisi var mı? Sonuç bsd_diag satırına yazılır (anahtar içermez)."""
import os
from datetime import datetime, timezone

import httpx

import accuracy

BASE = "https://sports.bzzoiro.com"


def _shape(v, depth=0):
    if isinstance(v, dict):
        return {k: (_shape(x, depth + 1) if depth < 2 else type(x).__name__) for k, x in list(v.items())[:30]}
    if isinstance(v, list):
        return [_shape(v[0], depth + 1)] if v else []
    return v if isinstance(v, (int, float, bool)) or v is None else str(v)[:40]


async def run_once() -> None:
    key = os.getenv("BSD_API_KEY")
    out: dict = {"zaman": datetime.now(timezone.utc).isoformat()}
    try:
        async with httpx.AsyncClient(timeout=25.0, headers={"Authorization": f"Token {key}"}) as c:
            async def get(path, **params):
                r = await c.get(f"{BASE}{path}", params=params)
                return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])

            code, body = await get("/api/v2/events/", status="upcoming", team_name="Galatasaray", limit=2)
            out["upcoming"] = [code, _shape(body)]
            tid = None
            rows = (body.get("results") if isinstance(body, dict) else None) or []
            if rows:
                h = rows[0].get("home_team_obj") or rows[0].get("home_team")
                tid = (h or {}).get("id") if isinstance(h, dict) else None
                out["event_keys"] = sorted(rows[0].keys())[:60]
            if tid:
                code, form = await get(f"/api/v2/teams/{tid}/form/", last=10)
                out["form"] = [code, _shape(form)]
                code, fin = await get("/api/v2/events/", team_id=tid, status="finished", limit=3)
                out["finished_n"] = len((fin.get("results") if isinstance(fin, dict) else []) or [])
                sit, body_types, sample = {}, {}, None
                for ev in ((fin.get("results") if isinstance(fin, dict) else []) or [])[:3]:
                    eid = ev.get("id")
                    code, st = await get(f"/api/v2/events/{eid}/stats/")
                    out.setdefault("stats_codes", []).append(code)
                    if isinstance(st, dict):
                        out["stats_shape"] = _shape(st)
                        for s in (st.get("shotmap") or []) if isinstance(st.get("shotmap"), list) else []:
                            sit[str(s.get("sit"))] = sit.get(str(s.get("sit")), 0) + 1
                            body_types[str(s.get("body"))] = body_types.get(str(s.get("body")), 0) + 1
                            sample = sample or s
                    code, inc = await get(f"/api/v2/events/{eid}/incidents/")
                    out.setdefault("incident_codes", []).append(code)
                    if "incidents_shape" not in out:
                        out["incidents_shape"] = _shape(inc)
                    code, ps = await get(f"/api/v2/events/{eid}/player-stats/")
                    if "player_stats_shape" not in out:
                        out["player_stats_shape"] = [code, _shape(ps)]
                out["sit"], out["body"], out["shot_sample"] = sit, body_types, _shape(sample)
    except Exception as exc:  # noqa: BLE001
        out["hata"] = str(exc)[:200]
    try:
        await accuracy._db("POST", "sportoto_bulletin", params={"on_conflict": "id"}, prefer=True, json_body={
            "id": "bsd_diag", "season": "", "week": "", "matches": out, "source": "bsd", "updated_at": datetime.now(timezone.utc).isoformat()})
    except Exception:  # noqa: BLE001
        pass
