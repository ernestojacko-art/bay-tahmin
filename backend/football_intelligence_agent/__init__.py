"""Production runtime facade for BAY TAHMIN Football Intelligence Engine."""
from __future__ import annotations
import asyncio,importlib.util,re,unicodedata
from datetime import timedelta
from pathlib import Path
P=Path(__file__).resolve().parent.parent/"football_intelligence_agent_v6.py";s=importlib.util.spec_from_file_location("_bay_tahmin_engine_impl",P)
if s is None or s.loader is None:raise ImportError(P)
_impl=importlib.util.module_from_spec(s);s.loader.exec_module(_impl)
ENGINE,VERSION=_impl.ENGINE,_impl.VERSION
dates,num,isiy,issur=_impl.dates,_impl.num,_impl.isiy,_impl.issur
market,day,cand=_impl.market,_impl.day,_impl.cand
model,analyze_match,match_answer=_impl.model,_impl.analyze_match,_impl.match_answer
resolve_finished_match=_impl.resolve_finished_match;performance_summary=_impl.performance_summary

def _norm(v):
 t=unicodedata.normalize("NFKD",str(v or "").lower().strip());t="".join(x for x in t if not unicodedata.combining(x));return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9]+"," ",t.translate(str.maketrans({"ı":"i","ş":"s","ğ":"g","ü":"u","ö":"o","ç":"c"})))).strip()
def _team_mentioned(team,text):
 n=_norm(team)
 if not n:return False
 if n in text:return True
 q=[x for x in n.split() if len(x)>=3];return bool(q) and all(x in text for x in q)
def _explicit_match_requested(message,row):
 t=_norm(message);return _team_mentioned(row.get("Team1"),t) and _team_mentioned(row.get("Team2"),t)
def _is_iyms_request(message):return bool(re.search(r"iy/?ms|ilkyari/?macsonucu|ilkyarimacsonucu",_norm(message).replace(" ","")))
def _is_surprise_request(message):return "surpriz" in _norm(message)

async def _model_only_iyms_surprises(message,rows):
 count=num(message);candidates=[]
 for row in rows:
  try:
   r=await cand(row);p=((r.get("model") or {}).get("iyms") or {}).get("probabilities") or {}
   for k,v in p.items():
    if k not in {"1/1","X/X","2/2"}:candidates.append((float(v),r,k))
  except Exception:continue
 candidates.sort(key=lambda x:x[0],reverse=True);seen=set();selected=[]
 for p,r,k in candidates:
  mid=str((r.get("match") or {}).get("MatchID") or "")
  if not mid or mid in seen:continue
  seen.add(mid);selected.append((p,r,k))
  if len(selected)>=count:break
 if not selected:return {"reply":"İY/MS sürpriz analizi için doğrulanabilir maç verisi bulunamadı; veri yokken tahmin uydurmuyorum.","engine":ENGINE,"engine_version":VERSION,"analyzed_count":0,"source":"5DollarFootballAPI + transparent statistical ensemble"}
 lines=[f"{ENGINE} — bağımsız İY/MS sürpriz analizi\n"]
 for i,(p,r,k) in enumerate(selected,1):
  m=r.get("match") or {};name=m.get("Teams") or f"{m.get('Team1','')} - {m.get('Team2','')}";kick=m.get("KickoffUTC") or m.get("DateTime") or "";lines.append(f"{i}. {name} — İY/MS {k} — model olasılığı %{p:.2f}"+(f" — {kick}" if kick else ""))
 lines.append("\nNot: 1/1, X/X ve 2/2 düz sonuçlar sürpriz adayına alınmadı. Piyasa yalnızca çapraz kontroldür.")
 return {"reply":"\n".join(lines),"engine":ENGINE,"engine_version":VERSION,"dates":[d.isoformat() for d in dates(message)],"match_count":len(rows),"analyzed_count":len(selected),"source":"5DollarFootballAPI + transparent statistical ensemble"}

async def _rows_for_match_lookup(message):
 requested=dates(message);raw=_norm(message);explicit=any(x in raw for x in ("bugun","yarin","cumartesi","pazar")) or bool(re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b",raw))
 if not explicit:
  base=dates("")[0];requested=[base+timedelta(days=i) for i in range(7)]
 groups=await asyncio.gather(*(day(d) for d in requested));return [r for g in groups for r in g]

async def answer(main,message,history=None):
 rows=await _rows_for_match_lookup(message)
 # Only explicit HT/FT surprise questions enter the special path.
 if _is_iyms_request(message) and _is_surprise_request(message):return await _model_only_iyms_surprises(message,rows)
 candidates=[r for r in rows if _explicit_match_requested(message,r)]
 if len(candidates)==1:return await match_answer(main,int(candidates[0]["MatchID"]),message,history or [])
 if len(candidates)>1:return {"reply":"Aynı takım eşleşmesi için birden fazla gerçek maç bulundu. Tarihi veya organizasyonu belirtirsen doğru karşılaşmayı analiz edebilirim.","engine":ENGINE,"engine_version":VERSION,"match_count":len(candidates),"analyzed_count":0,"source":"5DollarFootballAPI"}
 return await _impl.answer(main,message,history or [])

def patch_main(main):
 from fastapi import HTTPException,Request
 app=main.app;target={"/chat","/matches/{match_id}/chat","/ai/analyze/{match_id}","/match/{match_id}","/mac/{match_id}","/ai/performance","/ai/resolve/{match_id}","/ai/backtest"};app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in target]
 @app.post("/chat")
 async def intelligence_general_chat(request:Request):
  try:p=await request.json()
  except Exception:p={}
  msg=str(p.get("message") or p.get("question") or "").strip()
  if not msg:raise HTTPException(400,"Mesaj boş olamaz.")
  return await answer(main,msg,p.get("history") or [])
 @app.post("/matches/{match_id}/chat")
 async def intelligence_match_chat(match_id:int,request:Request):
  try:p=await request.json()
  except Exception:p={}
  msg=str(p.get("message") or p.get("question") or "").strip()
  if not msg:raise HTTPException(400,"Mesaj boş olamaz.")
  return await match_answer(main,match_id,msg,p.get("history") or [])
 @app.get("/ai/analyze/{match_id}")
 async def intelligence_analyze_match(match_id:int):return await analyze_match(main,match_id)
 @app.get("/ai/performance")
 async def intelligence_performance():return performance_summary()
 @app.post("/ai/resolve/{match_id}")
 async def intelligence_resolve(match_id:int):return {"resolved":await resolve_finished_match(await main.get_match_detail(match_id)),"match_id":match_id}
 @app.post("/ai/backtest")
 async def intelligence_backtest(request:Request):
  try:p=await request.json()
  except Exception:p={}
  try:lid=int(p.get("league_id"))
  except (TypeError,ValueError):raise HTTPException(400,"league_id zorunlu ve sayısal olmalı.")
  from backtest_engine import run_historical_backtest
  return await run_historical_backtest(lid,season=p.get("season"),start_time=p.get("start_time"),end_time=p.get("end_time"),limit=max(1,min(int(p.get("limit") or 50),250)))
 @app.get("/match/{match_id}")
 @app.get("/mac/{match_id}")
 async def intelligence_match_detail(match_id:int):
  detail=await main.get_match_detail(match_id);r=await analyze_match(main,match_id);detail["analysis"]=r.get("analysis");detail["prediction"]=r.get("analysis");detail["intelligence_engine"]={"name":ENGINE,"version":VERSION};detail["prediction_cache"]="intelligence_engine";return detail
 return app
__all__=["ENGINE","VERSION","answer","analyze_match","match_answer","patch_main","performance_summary"]
