"""BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE runtime agent."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import five_dollar_bridge as five
from football_intelligence_data import build_match_context

ISTANBUL = ZoneInfo("Europe/Istanbul")
ENGINE_NAME = "BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE"


def norm(v: Any) -> str: return re.sub(r"\s+", " ", str(v or "").strip().lower())
def requested_count(message: str) -> int:
    m=re.search(r"\b(\d{1,2})\s*(?:maç|adet|tane)\b",norm(message)); return max(1,min(int(m.group(1)),20)) if m else 5
def wants_iyms(message: str) -> bool: return bool(re.search(r"iy\s*/?\s*ms|ilk\s*yari\s*/?\s*mac\s*sonucu|ilk\s*yari.*mac\s*sonucu",norm(message)))
def wants_surprise(message: str) -> bool: return bool(re.search(r"sürpriz|surpriz",norm(message)))


def resolve_dates(message: str) -> List[date]:
    text=norm(message); today=datetime.now(ISTANBUL).date(); dates=[]
    if re.search(r"\b(yarın|yarin)\b",text): dates.append(today+timedelta(days=1))
    if re.search(r"\b(bugün|bugun)\b",text): dates.append(today)
    for name,wd in (("cumartesi",5),("pazar",6)):
        if re.search(rf"\b{name}(?:\s+günü|\s+gunu)?\b",text): dates.append(today+timedelta(days=(wd-today.weekday())%7))
    if re.search(r"\b(öbür gün|obur gun)\b",text): dates.append(today+timedelta(days=2))
    m=re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b",text)
    if m:
        y=int(m.group(3) or today.year); y+=2000 if y<100 else 0
        try: dates.append(date(y,int(m.group(2)),int(m.group(1))))
        except ValueError: pass
    return sorted(set(dates or [today]))


def _day_window(target: date):
    s=datetime.combine(target,datetime.min.time(),tzinfo=ISTANBUL).astimezone(timezone.utc); e=s+timedelta(days=1)
    return int(s.timestamp()),int(e.timestamp())


async def fixtures_for_date(target: date) -> List[Dict[str,Any]]:
    start,end=_day_window(target)
    payload=await five._get("fixtures",{"start_time":start,"end_time":end,"status":"all","lang":"en","per_page":50,"include":"odds,stats"})
    rows=[]
    for fixture in payload.get("data") or []:
        row=five._fixture_row(fixture)
        try: local_day=datetime.fromisoformat(str(fixture.get("kickoff_utc") or "").replace("Z","+00:00")).astimezone(ISTANBUL).date()
        except ValueError: continue
        if local_day!=target: continue
        row["_raw_fixture"]=fixture
        row["_markets"]=five._markets_from_odds({"data":{"odds":fixture.get("odds") or {}}},live=False)
        row["_stats"]=fixture.get("statistics") or fixture.get("stats") or {}
        rows.append(row)
    return rows


def market_probabilities(markets: List[Dict[str,Any]]) -> Dict[str,float]:
    for market in markets:
        mt=norm(market.get("type") or market.get("gameName"))
        if "1x2" not in mt and "maç sonucu" not in mt and "match" not in mt: continue
        raw={}
        for odd in market.get("odds",[]):
            value=str(odd.get("value") or "").strip()
            try: price=float(odd.get("odd"))
            except (TypeError,ValueError): continue
            if value and price>1: raw[value]=1/price
        total=sum(raw.values())
        if total: return {k:round(v/total*100,2) for k,v in raw.items()}
    return {}


def football_model(context: Dict[str,Any]) -> Dict[str,Any]:
    h,a=context.get("home",{}),context.get("away",{}); hf,af=h.get("recent_form",{}),a.get("recent_form",{}); hs,ass=h.get("standing",{}),a.get("standing",{}); signals=[]
    def add(name,edge,weight,evidence): signals.append({"name":name,"edge":round(max(-1,min(1,edge)),4),"weight":weight,"evidence":evidence})
    hpp,app=hf.get("points_per_game"),af.get("points_per_game")
    if hpp is not None and app is not None: add("Son 10 maç formu",(hpp-app)/3,.30,f"{hf.get('form','')} vs {af.get('form','')}")
    hgf,agf=hf.get("goals_for_avg"),af.get("goals_for_avg")
    if hgf is not None and agf is not None: add("Gol üretimi",(hgf-agf)/(hgf+agf+2),.20,f"{hgf} vs {agf} gol/maç")
    hga,aga=hf.get("goals_against_avg"),af.get("goals_against_avg")
    if hga is not None and aga is not None: add("Savunma",(aga-hga)/(hga+aga+2),.18,f"Yenen gol {hga} vs {aga}")
    hp,ap=hs.get("position"),ass.get("position")
    if hp and ap: add("Lig sıralaması",(float(ap)-float(hp))/20,.17,f"{hp}. sıra vs {ap}. sıra")
    hpts,apts=hs.get("points"),ass.get("points")
    if hpts is not None and apts is not None: add("Lig puan gücü",(float(hpts)-float(apts))/40,.15,f"{hpts} vs {apts} puan")
    tw=sum(x["weight"] for x in signals); edge=sum(x["edge"]*x["weight"] for x in signals)/tw if tw else 0
    hp=.333+.36*edge; ap=.333-.36*edge; dp=max(.05,1-hp-ap); p={"1":hp,"X":dp,"2":ap}; total=sum(p.values()); p={k:v/total*100 for k,v in p.items()}
    return {"probabilities":{k:round(v,2) for k,v in p.items()},"edge":round(edge,4),"signals":signals,"data_depth":len(signals),"method":"football-first ensemble: form + goals + defence + standings"}


def iyms_projection(model: Dict[str,Any],surprise: bool)->List[Dict[str,Any]]:
    fp={k:v/100 for k,v in model["probabilities"].items()}; edge=model.get("edge",0); hp={"1":.31+.12*edge,"X":.38,"2":.31-.12*edge}; out=[]
    for h in hp:
        for f in fp:
            value=f"{h}/{f}"
            if surprise and value in {"1/1","X/X","2/2"}: continue
            q=hp[h]*fp[f]; out.append({"selection":value,"probability":round(q*100,2),"model_odd":round(1/q,2)})
    return sorted(out,key=lambda x:x["probability"],reverse=True)


async def build_candidate(row):
    context=await build_match_context(row)
    if row.get("_stats"): context["current_fixture_statistics"]=row["_stats"]
    return {"match":row,"context":context,"model":football_model(context),"markets":row.get("_markets") or []}


async def analyze_pool(rows):
    sem=asyncio.Semaphore(3)
    async def one(r):
        async with sem: return await build_candidate(r)
    return await asyncio.gather(*(one(r) for r in rows))


def rank(candidates,message):
    surprise,iyms=wants_surprise(message),wants_iyms(message); ranked=[]
    for c in candidates:
        p=c["model"]["probabilities"]
        if iyms:
            for x in iyms_projection(c["model"],surprise): ranked.append({**c,"selection":x["selection"],"model_probability":x["probability"],"market":"İY / MS Football Intelligence Projection"})
        else:
            choice=max(p,key=p.get); score=p[choice]
            if surprise:
                mp=market_probabilities(c["markets"]); fav=max(mp,key=mp.get) if mp else None; score += 12 if fav and choice!=fav else -8
            ranked.append({**c,"selection":choice,"model_probability":round(score,2),"market":"Football Intelligence 1X2"})
    ranked.sort(key=lambda x:(x["model_probability"],x["model"].get("data_depth",0)),reverse=True); out=[]; used=set(); count=requested_count(message)
    for c in ranked:
        mid=str(c["match"].get("MatchID"))
        if mid in used: continue
        used.add(mid); out.append(c)
        if len(out)>=count: break
    return out


def dossier(selected):
    out=[]
    for c in selected:
        m=c["match"]; h,a=c["context"].get("home",{}),c["context"].get("away",{})
        out.append({"match_id":m.get("MatchID"),"match":m.get("Teams"),"date":m.get("KickoffUTC") or m.get("Date"),"selection":c["selection"],"model_probability":c["model_probability"],"football_evidence":{"home_form":h.get("recent_form"),"away_form":a.get("recent_form"),"home_standing":h.get("standing"),"away_standing":a.get("standing"),"signals":c["model"]["signals"],"data_depth":c["model"]["data_depth"]},"market_cross_check":market_probabilities(c["markets"]),"market":c["market"]})
    return out


async def answer(main_module,message,history=None):
    dates=resolve_dates(message); groups=await asyncio.gather(*(fixtures_for_date(d) for d in dates)); rows=[r for g in groups for r in g]
    if not rows: return {"reply":"İstenen tarihlerde 5DollarFootballAPI'den doğrulanmış gerçek futbol maçı bulunamadı.","engine":ENGINE_NAME,"dates":[d.isoformat() for d in dates]}
    candidates=await analyze_pool(rows); selected=rank(candidates,message)
    if not selected: return {"reply":"Bu istek için yeterli gerçek futbol verisi bulunamadı; veri uydurmuyorum.","engine":ENGINE_NAME}
    data=dossier(selected)
    prompt=f"""Sen {ENGINE_NAME} olarak çalışan futbol uzmanısın.
Tahminin ana kaynağı gerçek futbol verilerinden hesaplanan modeldir. Piyasa yalnızca yardımcı çapraz kontroldür. Eksik veriyi uydurma.
Kullanıcı isteği: {message}
Tarihler: {', '.join(d.strftime('%d.%m.%Y') for d in dates)}
GERÇEK VERİ + MODEL DOSSIER:
{json.dumps(data,ensure_ascii=False)}
Her seçimi form, gol üretimi/savunma, lig gücü/sıralama ve model sinyalleriyle açıkla. İY/MS ise Football Intelligence model projeksiyonu olduğunu belirt. Oranı yalnızca gerçek market_cross_check içinde varsa yaz. Kupon oluşturma. Türkçe, net ve analitik ol.
Önceki sohbet: {json.dumps(history or [],ensure_ascii=False)[:8000]}"""
    return {"reply":await main_module.gemini_generate(prompt),"engine":ENGINE_NAME,"engine_version":"0.1.0","dates":[d.isoformat() for d in dates],"match_count":len(rows),"analyzed_count":len(candidates),"source":"5DollarFootballAPI + football intelligence"}


async def match_answer(main_module,match_id,message,history=None):
    payload=await five._get(f"fixtures/{int(match_id)}",{"lang":"en","include":"events,stats"}); fixture=payload.get("data") or {}
    if not fixture: return {"reply":"Maç bulunamadı.","engine":ENGINE_NAME}
    row=five._fixture_row(fixture); row["_markets"]=five._markets_from_odds({"data":{"odds":fixture.get("odds") or {}}},live=row.get("Status")=="live"); row["_stats"]=fixture.get("statistics") or {}
    c=await build_candidate(row); choice=max(c["model"]["probabilities"],key=c["model"]["probabilities"].get); data=dossier([{**c,"selection":choice,"model_probability":c["model"]["probabilities"][choice],"market":"Football Intelligence Match Analysis"}])
    prompt=f"""Sen {ENGINE_NAME} maç özel uzmanısın. Gerçek maç verisi ve futbol modelini kullan.
MAÇ DOSSIER: {json.dumps(data,ensure_ascii=False)}
KULLANICI: {message}
Piyasa varsa yalnızca yardımcı kontrol olarak kullan. Eksik veri uydurma. Türkçe, profesyonel ve analitik yanıt ver. Kupon oluşturma."""
    return {"reply":await main_module.gemini_generate(prompt),"match_id":str(match_id),"engine":ENGINE_NAME,"engine_version":"0.1.0","source":"5DollarFootballAPI + football intelligence"}


def patch_main(m):
    from fastapi import Request,HTTPException
    from fastapi.routing import APIRoute
    async def general(request:Request):
        try: payload=await request.json()
        except Exception: payload={}
        message=str(payload.get("message") or payload.get("question") or "").strip()
        if not message: raise HTTPException(status_code=400,detail="Mesaj boş olamaz.")
        return await answer(m,message,payload.get("history") or [])
    async def match(request:Request,match_id:int):
        try: payload=await request.json()
        except Exception: payload={}
        message=str(payload.get("message") or payload.get("question") or "").strip()
        if not message: raise HTTPException(status_code=400,detail="Mesaj boş olamaz.")
        return await match_answer(m,match_id,message,payload.get("history") or [])
    m.app.router.routes=[r for r in m.app.router.routes if not(isinstance(r,APIRoute) and r.path in {"/chat","/matches/{match_id}/chat"} and "POST" in(r.methods or set()))]
    m.app.add_api_route("/chat",general,methods=["POST"]); m.app.add_api_route("/matches/{match_id}/chat",match,methods=["POST"])
    m.get_matches=five.get_matches; m.get_match_detail=five.get_match_detail; m.get_match_detail_alias=five.get_match_detail
