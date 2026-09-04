import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException, Request
import five_dollar_bridge as five

ISTANBUL = ZoneInfo("Europe/Istanbul")
MONTHS = {"ocak":1,"şubat":2,"subat":2,"mart":3,"nisan":4,"mayıs":5,"mayis":5,"haziran":6,"temmuz":7,"ağustos":8,"agustos":8,"eylül":9,"eylul":9,"ekim":10,"kasım":11,"kasim":11,"aralık":12,"aralik":12}

def today_local(): return datetime.now(ISTANBUL).date()
def resolve_requested_date(message):
    text=re.sub(r"\s+"," ",str(message or "").strip().lower()); today=today_local()
    if re.search(r"\b(öbür gün|obur gun|öbürgun|oburgun)\b",text): return today+timedelta(days=2),"öbür gün"
    if re.search(r"\b(yarın|yarin)\b",text): return today+timedelta(days=1),"yarın"
    if re.search(r"\b(bugün|bugun|bugünkü|bugunku)\b",text): return today,"bugün"
    m=re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b",text)
    if m:
        d,mo=int(m.group(1)),int(m.group(2)); y=int(m.group(3)) if m.group(3) else today.year
        if y<100:y+=2000
        try:return date(y,mo,d),m.group(0)
        except ValueError:pass
    for n,mo in MONTHS.items():
        m=re.search(rf"\b(\d{{1,2}})\s+{re.escape(n)}(?:\s+(\d{{4}}))?\b",text)
        if m:
            y=int(m.group(2)) if m.group(2) else today.year
            try:return date(y,mo,int(m.group(1))),m.group(0)
            except ValueError:pass
    return today,"bugün"

def local_day_window(target):
    s=datetime.combine(target,datetime.min.time(),tzinfo=ISTANBUL); e=s+timedelta(days=1)
    return int(s.astimezone(timezone.utc).timestamp()),int(e.astimezone(timezone.utc).timestamp())

async def _fixture_rows(target):
    start,end=local_day_window(target)
    payload=await five._get("fixtures",{"start_time":start,"end_time":end,"status":"all","lang":"en","per_page":100})
    out=[]; seen=set()
    for fixture in payload.get("data") or []:
        row=five._fixture_row(fixture); mid=str(row.get("MatchID") or ""); kickoff=row.get("KickoffUTC") or row.get("Date") or ""
        try: local=datetime.fromisoformat(str(kickoff).replace("Z","+00:00")).astimezone(ISTANBUL).date()
        except ValueError: continue
        if local!=target or not mid or mid in seen: continue
        seen.add(mid); out.append((row,fixture))
    return out

async def get_matches_for_local_date(target):
    pairs=await _fixture_rows(target)
    if not pairs: return []

    # Free/Community plans cannot expand odds on the list endpoint, while the
    # single-fixture endpoint includes the same Bet365 odds. Fetch those details
    # concurrently and cache them. This removes the old 3.2s/request bottleneck.
    sem=asyncio.Semaphore(10)
    rows=[p[0] for p in pairs[:19]]  # keep the 20 req/min ceiling: 1 list + 19 details

    async def load(row):
        async with sem:
            try:
                detail=await five._get(f"fixtures/{row['MatchID']}",{"lang":"en"})
                fixture=detail.get("data") or {}
                row["_markets"]=five._markets_from_odds({"data":{"odds":fixture.get("odds") or {}}},live=row.get("Status")=="live")
            except Exception:
                row["_markets"]=[]
            return row

    return await asyncio.gather(*(load(r) for r in rows))

def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip().lower())
def requested_count(m):
    x=re.search(r"\b(\d{1,2})\s*(?:maç|adet|tane)\b",_norm(m)); return max(1,min(int(x.group(1)),20)) if x else 5
def wants_surprise(m): return bool(re.search(r"\b(sürpriz|surpriz)\b",_norm(m)))
def wants_iyms(m): return bool(re.search(r"iy\s*/?\s*ms|ilk\s*yari\s*/?\s*mac\s*sonucu|ilk\s*yari.*mac\s*sonucu",_norm(m)))

def market_probability(odds):
    raw={x["value"]:1/x["odd"] for x in odds if x.get("odd",0)>0}; total=sum(raw.values()); return {k:v/total for k,v in raw.items()} if total else {}
def rank_market(market):
    p=market_probability(market.get("odds",[])); return sorted(((o["value"],o["odd"],p.get(o["value"],0)) for o in market.get("odds",[])),key=lambda x:x[2],reverse=True)
def market_score(market,message):
    r=rank_market(market)
    if not r:return None
    name=_norm(market.get("gameName"))
    if wants_iyms(message) and "ilk yarı maç sonucu" not in name and "iy/ms" not in name:return None
    if wants_surprise(message):
        a=[x for x in r if _norm(x[0]) not in {"1","x","2"}]; return a[0][2]*100 if a else None
    bonus=8 if "maç sonucu" in name or "1x2" in name else 6 if "karşılıklı gol" in name or "kg" in name else 5 if "alt/üst gol" in name else 0
    return r[0][2]*100+bonus

def choose_best(pool,message,count):
    candidates=[]
    for item in pool:
        best=None
        for market in item.get("markets",[]):
            score=market_score(market,message)
            if score is None:continue
            r=rank_market(market)
            if not r:continue
            if wants_surprise(message):
                a=[x for x in r if _norm(x[0]) not in {"1","x","2"}]
                if not a:continue
                sel,odd,prob=a[0]
            else:sel,odd,prob=r[0]
            c={"match_id":str(item["match"]["MatchID"]),"match":item["match"].get("Teams") or "Maç","kickoff":item["match"].get("KickoffUTC") or item["match"].get("Date"),"market":market.get("gameName"),"selection":sel,"odd":odd,"market_probability":round(prob*100,2),"score":round(score,2)}
            if best is None or c["score"]>best["score"]:best=c
        if best:candidates.append(best)
    candidates.sort(key=lambda x:(x["score"],x["market_probability"]),reverse=True); out=[]; used=set()
    for c in candidates:
        if c["match_id"] in used:continue
        out.append(c);used.add(c["match_id"])
        if len(out)>=count:break
    return out

def build_prompt(message,target,label,selections,pool_size):
    return f"""Sen Bay Tahmin'sin ve gelişmiş Football AI Agent mantığıyla çalışıyorsun.\n\nKATI VERİ KURALI:\n- Veri kaynağı yalnızca 5DollarFootballAPI'dir.\n- İstenen gün: {target.isoformat()} ({label})\n- Analiz havuzu yalnızca bu tarihin {pool_size} gerçek maçından oluşuyor.\n- Başka tarihten maç eklemek kesinlikle yasaktır.\n- Bir market/seçim veri içinde yoksa üretme.\n- Güven puanı garanti değildir; açık market ve piyasa olasılığına dayalı değerlendirmedir.\n\nKULLANICI İSTEĞİ:\n{message}\n\nÖN SIRALAMADAN GEÇEN ADAYLAR:\n{selections}\n\nAdayları profesyonelce açıkla, sıralamayı koru, Türkçe cevap ver ve tarihi belirt."""

async def general_chat(request,main_module):
    try: payload=await request.json()
    except Exception: payload={}
    message=str(payload.get("message") or payload.get("question") or "").strip()
    if not message:raise HTTPException(status_code=400,detail="Mesaj boş olamaz.")
    history=payload.get("history") or []
    target,label=resolve_requested_date(message)
    rows=await asyncio.wait_for(get_matches_for_local_date(target),timeout=12.0)
    if not rows:return {"reply":f"{target.strftime('%d.%m.%Y')} tarihinde 5DollarFootballAPI'den doğrulanmış maç bulunamadı.","date":target.isoformat(),"source":"5dollarfootballapi"}
    pool=[{"match":r,"markets":r.get("_markets") or [],"detail":None} for r in rows]
    selections=choose_best(pool,message,requested_count(message))
    if not selections:return {"reply":f"{target.strftime('%d.%m.%Y')} tarihindeki {len(rows)} maç içinde isteğini karşılayan doğrulanmış açık market bulunamadı.","date":target.isoformat(),"source":"5dollarfootballapi","match_count":len(rows),"analyzed_count":len(pool)}
    prompt=build_prompt(message,target,label,selections,len(rows))
    if history:prompt+=f"\nÖNCEKİ SOHBET BAĞLAMI:\n{main_module.compact_data(history,8000)}"
    reply=await asyncio.wait_for(main_module.gemini_generate(prompt),timeout=10.0)
    return {"reply":reply,"date":target.isoformat(),"date_label":label,"match_count":len(rows),"analyzed_count":len(pool),"source":"5dollarfootballapi","agent":"FootballAgent / FootballAgentOrchestrator / FootballChatAgent"}

def patch_main(m):
    from fastapi.routing import APIRoute
    m.app.router.routes=[r for r in m.app.router.routes if not(isinstance(r,APIRoute) and r.path=="/chat" and "POST" in(r.methods or set()))]
    async def route(request: Request):
        return await general_chat(request,m)
    m.app.add_api_route("/chat",route,methods=["POST"])
    m.get_matches=five.get_matches;m.get_match_detail=five.get_match_detail;m.get_match_detail_alias=five.get_match_detail
