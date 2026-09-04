import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException, Request
import five_dollar_bridge as five
from accuracy import save_prediction

ISTANBUL = ZoneInfo("Europe/Istanbul")
MONTHS = {"ocak":1,"şubat":2,"subat":2,"mart":3,"nisan":4,"mayıs":5,"mayis":5,"haziran":6,"temmuz":7,"ağustos":8,"agustos":8,"eylül":9,"eylul":9,"ekim":10,"kasım":11,"kasim":11,"aralık":12,"aralik":12}
WEEKDAYS = {"pazartesi":0,"salı":1,"sali":1,"çarşamba":2,"carsamba":2,"perşembe":3,"persembe":3,"cuma":4,"cumartesi":5,"pazar":6}

def today_local(): return datetime.now(ISTANBUL).date()
def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip().lower())

def resolve_requested_dates(message):
    text=_norm(message); today=today_local(); dates=[]
    if re.search(r"\b(öbür gün|obur gun|öbürgun|oburgun)\b",text): dates.append(today+timedelta(days=2))
    if re.search(r"\b(yarın|yarin)\b",text): dates.append(today+timedelta(days=1))
    if re.search(r"\b(bugün|bugun|bugünkü|bugunku)\b",text): dates.append(today)
    for name,wd in WEEKDAYS.items():
        if re.search(rf"\b{name}(?:\s+günü|\s+gunu)?\b",text): dates.append(today+timedelta(days=(wd-today.weekday())%7))
    m=re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b",text)
    if m:
        d,mo=int(m.group(1)),int(m.group(2)); y=int(m.group(3)) if m.group(3) else today.year; y=y+2000 if y<100 else y
        try: dates.append(date(y,mo,d))
        except ValueError: pass
    for n,mo in MONTHS.items():
        m=re.search(rf"\b(\d{{1,2}})\s+{re.escape(n)}(?:\s+(\d{{4}}))?\b",text)
        if m:
            try: dates.append(date(int(m.group(2)) if m.group(2) else today.year,mo,int(m.group(1))))
            except ValueError: pass
    return sorted(set(dates or [today]))

def local_day_window(target):
    s=datetime.combine(target,datetime.min.time(),tzinfo=ISTANBUL); e=s+timedelta(days=1)
    return int(s.astimezone(timezone.utc).timestamp()),int(e.astimezone(timezone.utc).timestamp())

async def _fixture_rows(target):
    start,end=local_day_window(target)
    payload=await five._get("fixtures",{"start_time":start,"end_time":end,"status":"all","lang":"en","per_page":50,"include":"odds"})
    out=[]; seen=set()
    for fixture in payload.get("data") or []:
        row=five._fixture_row(fixture); mid=str(row.get("MatchID") or ""); kickoff=row.get("KickoffUTC") or row.get("Date") or ""
        try: local=datetime.fromisoformat(str(kickoff).replace("Z","+00:00")).astimezone(ISTANBUL).date()
        except ValueError: continue
        if local!=target or not mid or mid in seen: continue
        seen.add(mid); row["_markets"]=five._markets_from_odds({"data":{"odds":fixture.get("odds") or {}}},live=row.get("Status")=="live"); out.append(row)
    return out

async def get_matches_for_local_date(target): return await _fixture_rows(target)
def requested_count(m):
    x=re.search(r"\b(\d{1,2})\s*(?:maç|adet|tane)\b",_norm(m)); return max(1,min(int(x.group(1)),20)) if x else 5
def wants_surprise(m): return bool(re.search(r"\b(sürpriz|surpriz)\b",_norm(m)))
def wants_iyms(m): return bool(re.search(r"iy\s*/?\s*ms|ilk\s*yari\s*/?\s*mac\s*sonucu|ilk\s*yari.*mac\s*sonucu",_norm(m)))

def market_probability(odds):
    raw={x["value"]:1/x["odd"] for x in odds if x.get("odd",0)>0}; total=sum(raw.values()); return {k:v/total for k,v in raw.items()} if total else {}
def rank_market(market):
    p=market_probability(market.get("odds",[])); return sorted(((o["value"],o["odd"],p.get(o["value"],0)) for o in market.get("odds",[])),key=lambda x:x[2],reverse=True)

def find_market(item, market_type):
    for m in item.get("markets",[]):
        if str(m.get("type"))==market_type: return m
    return None

def build_iyms_candidates(item, surprise=False):
    half=find_market(item,"1x2_half"); full=find_market(item,"1x2")
    if not half or not full: return []
    hp=market_probability(half.get("odds",[])); fp=market_probability(full.get("odds",[]))
    if not hp or not fp: return []
    combos=[]
    for h in ("1","X","2"):
        for f in ("1","X","2"):
            p=hp.get(h,0)*fp.get(f,0)
            if not p: continue
            value=f"{h}/{f}"
            if surprise and value in {"1/1","2/2","X/X"}: continue
            combos.append({"selection":value,"prob":p,"odd":round(1/p,2) if p else None})
    total=sum(x["prob"] for x in combos)
    for x in combos: x["prob_norm"]=x["prob"]/total if total else 0
    return sorted(combos,key=lambda x:x["prob_norm"],reverse=True)

def choose_best(pool,message,count):
    candidates=[]; iyms=wants_iyms(message); surprise=wants_surprise(message)
    for item in pool:
        match=item["match"]
        if iyms:
            for c in build_iyms_candidates(item,surprise=surprise):
                candidates.append({"match_id":str(match["MatchID"]),"match":match.get("Teams") or "Maç","home_team":match.get("Team1") or "","away_team":match.get("Team2") or "","competition":match.get("League") or "","kickoff":match.get("KickoffUTC") or match.get("Date"),"market":"İY / MS Agent Projeksiyonu","market_type":"iyms_projection","selection":c["selection"],"odd":c["odd"],"market_probability":round(c["prob_norm"]*100,2),"score":round(c["prob_norm"]*100,2),"basis":"Gerçek 5Dollar Bet365 İlk Yarı 1X2 + Maç Sonucu 1X2"})
            continue
        for market in item.get("markets",[]):
            r=rank_market(market)
            if not r: continue
            if surprise:
                a=[x for x in r if _norm(x[0]) not in {"1","x","2"}]
                if not a: continue
                sel,odd,prob=a[0]
            else: sel,odd,prob=r[0]
            name=_norm(market.get("gameName")); bonus=8 if "maç sonucu" in name or "1x2" in name else 6 if "karşılıklı gol" in name or "kg" in name else 5 if "alt/üst gol" in name else 0
            candidates.append({"match_id":str(match["MatchID"]),"match":match.get("Teams") or "Maç","home_team":match.get("Team1") or "","away_team":match.get("Team2") or "","competition":match.get("League") or "","kickoff":match.get("KickoffUTC") or match.get("Date"),"market":market.get("gameName"),"market_type":market.get("type") or "unknown","selection":sel,"odd":odd,"market_probability":round(prob*100,2),"score":round(prob*100+bonus,2)})
    candidates.sort(key=lambda x:(x["score"],x["market_probability"]),reverse=True)
    out=[]; used=set()
    for c in candidates:
        if c["match_id"] in used: continue
        out.append(c); used.add(c["match_id"])
        if len(out)>=count: break
    return out

async def persist_selections(selections):
    if selections: await asyncio.gather(*(save_prediction(c) for c in selections),return_exceptions=True)

def build_prompt(message,dates,selections,pool_size):
    return f'''Sen Bay Tahmin'sin ve gelişmiş Football AI Agent / FootballAgentOrchestrator / FootballChatAgent mantığıyla çalışan futbol analiz ajanısın.
Veri kaynağı yalnızca 5DollarFootballAPI'dir. İstenen tarihler: {", ".join(d.strftime("%d.%m.%Y") for d in dates)}. Analiz havuzu yalnızca bu tarihlerdeki {pool_size} gerçek maçtır; başka tarihten maç ekleme.
Kullanıcı İY/MS istiyorsa, 5Dollar API'de ayrı bir HT/FT marketi bulunmadığında bunu açıkça "İY / MS Agent Projeksiyonu" olarak adlandır. Bu projeksiyon yalnızca gerçek İlk Yarı 1X2 ve Maç Sonucu 1X2 Bet365 oranlarından türetilmiştir; market varmış gibi sunma. Güven garanti değildir.
KULLANICI: {message}
ADAYLAR: {selections}
Türkçe, net ve profesyonel cevap ver; tarihleri belirt ve sıralamayı koru.'''

class FootballChatAgent:
    def __init__(self,main_module): self.main=main_module
    async def respond(self,message,history=None):
        dates=resolve_requested_dates(message); groups=await asyncio.gather(*(get_matches_for_local_date(d) for d in dates)); pool=[]
        for d,rows in zip(dates,groups):
            for r in rows: pool.append({"date":d,"match":r,"markets":r.get("_markets") or []})
        if not pool: return {"reply":"İstenen tarihlerde 5DollarFootballAPI'den doğrulanmış maç bulunamadı.","dates":[d.isoformat() for d in dates],"source":"5dollarfootballapi"}
        selections=choose_best(pool,message,requested_count(message))
        if not selections:
            if wants_iyms(message):
                return {"reply":"İstenen tarihlerde İY/MS projeksiyonu için gerekli gerçek İlk Yarı 1X2 ve Maç Sonucu 1X2 market verisi bulunan yeterli maç bulunamadı.","dates":[d.isoformat() for d in dates],"match_count":len(pool),"analyzed_count":len(pool),"source":"5dollarfootballapi"}
            return {"reply":"İstenen tarihlerde isteğini karşılayan doğrulanmış açık market bulunamadı.","dates":[d.isoformat() for d in dates],"match_count":len(pool),"analyzed_count":len(pool),"source":"5dollarfootballapi"}
        await persist_selections(selections); prompt=build_prompt(message,dates,selections,len(pool))
        if history: prompt+=f"\nÖNCEKİ SOHBET BAĞLAMI:\n{self.main.compact_data(history,8000)}"
        reply=await asyncio.wait_for(self.main.gemini_generate(prompt),timeout=10.0)
        return {"reply":reply,"dates":[d.isoformat() for d in dates],"date_label":", ".join(d.strftime("%d.%m.%Y") for d in dates),"match_count":len(pool),"analyzed_count":len(pool),"source":"5dollarfootballapi","agent":"FootballAgent / FootballAgentOrchestrator / FootballChatAgent","tracking":"prediction_tracking"}

class MatchChatAgent:
    def __init__(self,main_module): self.main=main_module
    async def respond(self,match_id,message,history=None):
        match_data=await five.get_match_detail(int(match_id)); prompt=f'''Sen Bay Tahmin'sin ve Football AI Agent / MatchChatAgent olarak çalışıyorsun. Yalnızca aşağıdaki gerçek 5DollarFootballAPI maç verisini kullan. Market veya istatistik uydurma.\n\nMAÇ:\n{self.main.compact_data(match_data,30000)}\n\nÖNCEKİ SOHBET:\n{self.main.compact_data(history or [],8000)}\n\nKULLANICI SORUSU:\n{message}\n\nTürkçe, net ve profesyonel cevap ver. Garanti dili kullanma.'''; reply=await asyncio.wait_for(self.main.gemini_generate(prompt),timeout=10.0); return {"reply":reply,"match_id":str(match_id),"source":"5dollarfootballapi","agent":"MatchChatAgent"}

async def general_chat(request,main_module):
    try: payload=await request.json()
    except Exception: payload={}
    message=str(payload.get("message") or payload.get("question") or "").strip()
    if not message: raise HTTPException(status_code=400,detail="Mesaj boş olamaz.")
    return await FootballChatAgent(main_module).respond(message,payload.get("history") or [])

def patch_main(m):
    from fastapi.routing import APIRoute
    m.app.router.routes=[r for r in m.app.router.routes if not(isinstance(r,APIRoute) and r.path in {"/chat","/matches/{match_id}/chat"} and "POST" in(r.methods or set()))]
    async def route(request:Request): return await general_chat(request,m)
    async def match_route(match_id:int,request:Request):
        try: payload=await request.json()
        except Exception: payload={}
        message=str(payload.get("message") or payload.get("question") or "").strip()
        if not message: raise HTTPException(status_code=400,detail="Mesaj boş olamaz.")
        return await MatchChatAgent(m).respond(match_id,message,payload.get("history") or [])
    m.app.add_api_route("/chat",route,methods=["POST"]); m.app.add_api_route("/matches/{match_id}/chat",match_route,methods=["POST"])
    m.get_matches=five.get_matches; m.get_match_detail=five.get_match_detail; m.get_match_detail_alias=five.get_match_detail
