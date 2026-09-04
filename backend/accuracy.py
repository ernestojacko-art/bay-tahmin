import os
import re
from datetime import datetime, timedelta, timezone
import hashlib
import httpx
from fastapi import HTTPException

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def _headers(prefer=False):
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        raise HTTPException(status_code=500, detail="Supabase servis erişimi yapılandırılmamış.")
    h={"apikey":SUPABASE_SERVICE_ROLE_KEY,"Authorization":f"Bearer {SUPABASE_SERVICE_ROLE_KEY}","Content-Type":"application/json"}
    if prefer:h["Prefer"]="resolution=merge-duplicates,return=minimal"
    return h

async def _db(method,path,*,params=None,json_body=None,prefer=False):
    async with httpx.AsyncClient(timeout=8.0) as client:
        response=await client.request(method,f"{SUPABASE_URL}/rest/v1/{path.lstrip('/')}",headers=_headers(prefer),params=params,json=json_body)
    if response.status_code>=300: raise HTTPException(status_code=502,detail=f"Supabase isteği başarısız ({response.status_code}): {response.text[:400]}")
    return response.json() if response.content else []

async def save_prediction(candidate):
    kickoff=candidate.get("kickoff")
    if not kickoff:return False
    key="|".join([str(candidate.get("match_id") or ""),str(candidate.get("market_type") or candidate.get("market") or ""),str(candidate.get("selection") or ""),str(kickoff)])
    body={"prediction_key":hashlib.md5(key.encode("utf-8")).hexdigest(),"external_match_id":int(candidate["match_id"]),"kickoff_at":kickoff,"competition_name":candidate.get("competition") or "","home_team":candidate.get("home_team") or "","away_team":candidate.get("away_team") or "","prediction_type":candidate.get("market_type") or "unknown","prediction_value":candidate.get("selection") or "","odds":candidate.get("odd"),"confidence":candidate.get("market_probability"),"model_version":"football-agent-orchestrator-v1","source":"bay_tahmin","predicted_at":datetime.now(timezone.utc).isoformat(),"result_status":"pending","raw_prediction":candidate}
    await _db("POST","prediction_tracking",params={"on_conflict":"prediction_key"},json_body=body,prefer=True)
    return True

def _outcome_from_fixture(prediction,fixture):
    goals=fixture.get("goals") or {}; h,a=goals.get("home"),goals.get("away"); hh,ha=goals.get("half_home"),goals.get("half_away")
    if h is None or a is None:return None,None
    ptype=str(prediction.get("prediction_type") or "").lower(); value=str(prediction.get("prediction_value") or "").strip(); compact=re.sub(r"\s+","",value.lower())
    if ptype=="1x2":
        actual="1" if h>a else "X" if h==a else "2"; return ("won" if compact.upper()==actual else "lost"),f"MS {actual} ({h}-{a})"
    if ptype=="btts":
        actual="var" if h>0 and a>0 else "yok"; return ("won" if compact==actual else "lost"),f"KG {actual.title()} ({h}-{a})"
    if ptype=="1x2_half":
        if hh is None or ha is None:return None,None
        actual="1" if hh>ha else "X" if hh==ha else "2"; return ("won" if compact.upper()==actual else "lost"),f"İY {actual} ({hh}-{ha})"
    if ptype in {"goal_line","goal_line_half"}:
        if ptype=="goal_line_half":
            if hh is None or ha is None:return None,None
            total=hh+ha
        else: total=h+a
        m=re.search(r"(üst|alt)\s*([0-9]+(?:[.,][0-9]+)?)",value.lower())
        if not m:return None,None
        side=m.group(1); line=float(m.group(2).replace(",","."))
        if total==line:return "void",f"Gol çizgisi eşit ({total})"
        actual="üst" if total>line else "alt"; return ("won" if side==actual else "lost"),f"Toplam gol {total}"
    return None,None

async def _fetch_fixture(external_id):
    base=os.getenv("FIVE_DOLLAR_BASE_URL","https://api.5dollarfootballapi.com/v1").rstrip("/"); key=os.getenv("FIVE_DOLLAR_API_KEY")
    if not key:raise HTTPException(status_code=500,detail="FIVE_DOLLAR_API_KEY environment variable bulunamadı.")
    async with httpx.AsyncClient(timeout=8.0) as client:
        response=await client.get(f"{base}/fixtures/{external_id}",headers={"Authorization":f"Bearer {key}","Accept":"application/json"},params={"lang":"en"})
    if response.status_code!=200:return None
    payload=response.json(); return payload.get("data") if payload.get("success")==1 else None

async def resolve_pending(limit=8):
    rows=await _db("GET","prediction_tracking",params={"result_status":"eq.pending","select":"*","order":"kickoff_at.asc","limit":str(limit)})
    resolved=0
    for row in rows:
        try:
            if row.get("kickoff_at") and datetime.fromisoformat(row["kickoff_at"].replace("Z","+00:00"))>datetime.now(timezone.utc):continue
        except ValueError:pass
        fixture=await _fetch_fixture(row.get("external_match_id"))
        if not fixture or str(fixture.get("status","")).lower() not in {"finished","ft","aet","pen"}:continue
        status,actual=_outcome_from_fixture(row,fixture)
        if not status:continue
        await _db("PATCH","prediction_tracking",params={"id":f"eq.{row['id']}"},json_body={"result_status":status,"actual_result":actual,"resolved_at":datetime.now(timezone.utc).isoformat()})
        resolved+=1
    return resolved

async def accuracy_snapshot(days=30):
    since=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    rows=await _db("GET","prediction_tracking",params={"select":"result_status,prediction_type,kickoff_at,odds,confidence","kickoff_at":f"gte.{since}","order":"kickoff_at.desc","limit":"5000"})
    wins=sum(1 for r in rows if r.get("result_status")=="won"); losses=sum(1 for r in rows if r.get("result_status")=="lost"); pending=sum(1 for r in rows if r.get("result_status")=="pending"); void=sum(1 for r in rows if r.get("result_status")=="void"); resolved=wins+losses; accuracy=round(wins*100/resolved,2) if resolved else None
    return {"target_percent":70,"accuracy_percent":accuracy,"wins":wins,"losses":losses,"resolved":resolved,"pending":pending,"void":void,"sample_ready":resolved>=100,"commercial_threshold_reached":bool(accuracy is not None and accuracy>=70 and resolved>=100),"window_days":days}
