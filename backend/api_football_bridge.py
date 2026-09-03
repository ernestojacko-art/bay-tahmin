import os, httpx, time
from datetime import datetime
BASE=os.getenv('API_FOOTBALL_BASE_URL','https://v3.football.api-sports.io').rstrip('/')
KEY=os.getenv('API_FOOTBALL_KEY') or os.getenv('APIFOOTBALL_KEY')
TTL=int(os.getenv('API_FOOTBALL_CACHE_TTL','21600'))
_CACHE={}
def _cached(k):
    x=_CACHE.get(k)
    if x and x[0]>time.time(): return x[1]
    return None
def _put(k,v): _CACHE[k]=(time.time()+TTL,v); return v

def _get(path, params):
    if not KEY: raise RuntimeError('API_FOOTBALL_KEY environment variable bulunamadı.')
    r=httpx.get(f'{BASE}/{path.lstrip("/")}',headers={'x-apisports-key':KEY,'Accept':'application/json'},params=params,timeout=30)
    r.raise_for_status(); p=r.json()
    if p.get('errors'): raise RuntimeError(str(p['errors']))
    return p

def _map(f):
    fx=f.get('fixture',{}); t=f.get('teams',{}); l=f.get('league',{}); g=f.get('goals',{}); s=fx.get('status',{})
    sm={'NS':'scheduled','TBD':'scheduled','1H':'live','2H':'live','HT':'halftime','ET':'live','P':'live','LIVE':'live','FT':'finished','AET':'finished','PEN':'finished','PST':'postponed','CANC':'canceled','ABD':'canceled'}
    return {'id':str(fx.get('id')),'kickoff':fx.get('date'),'status':sm.get(s.get('short'),'scheduled'),'minute':s.get('elapsed'),'league':{'id':str(l.get('id')),'name':l.get('name') or '','country':l.get('country') or '','logoUrl':l.get('logo')},'homeTeam':{'id':str(t.get('home',{}).get('id')),'name':t.get('home',{}).get('name') or '','shortName':t.get('home',{}).get('code'),'logoUrl':t.get('home',{}).get('logo')},'awayTeam':{'id':str(t.get('away',{}).get('id')),'name':t.get('away',{}).get('name') or '','shortName':t.get('away',{}).get('code'),'logoUrl':t.get('away',{}).get('logo')},'score':{'home':g.get('home'),'away':g.get('away'),'halftimeHome':g.get('halftime'),'halftimeAway':None}}

def patch_main(m):
    async def get_matches(date=None):
        d=date or datetime.now().date().isoformat(); k=f'fixtures:{d}'; c=_cached(k); rows=c if c is not None else _put(k,[_map(x) for x in _get('fixtures',{'date':d}).get('response',[])]); return {'data':rows,'source':'cache' if c is not None else 'api-football'}
    async def get_match_detail(match_id:int):
        k=f'fixture:{match_id}'; c=_cached(k)
        if c is not None: return c
        f=_get('fixtures',{'id':match_id}).get('response',[])
        if not f: raise m.HTTPException(404,'Maç bulunamadı.')
        base={'fixture':f[0],'match':_map(f[0]),'source':'api-football'}
        try:
            pr=_get('predictions',{'fixture':match_id}).get('response',[]); base['prediction']=pr[0].get('predictions') if pr else None
        except Exception: base['prediction']=None
        try:
            od=_get('odds',{'fixture':match_id}).get('response',[]); markets=[]
            for b in od:
                bname=b.get('bookmaker',{}).get('name','Bookmaker')
                for bet in b.get('bookmaker',{}).get('bets',[]) or []:
                    vals=[]
                    for v in bet.get('values',[]) or []:
                        try: odd=float(v.get('odd'))
                        except (TypeError,ValueError): continue
                        if v.get('value') and odd>0: vals.append({'value':str(v['value']),'odd':odd})
                    if vals: markets.append({'gameName':f"{bet.get('name','')} ({bname})",'type':str(bet.get('id') or ''),'odds':vals})
            base['markets']=markets
        except Exception: base['markets']=[]
        return _put(k,base)
    if KEY:
        m.get_matches=get_matches; m.get_match_detail=get_match_detail; m.get_match_detail_alias=get_match_detail
        async def inspect_match(row):
            key=str(row.get('id') or '')
            if not key: return None
            try:
                d=await get_match_detail(int(key))
                return {'match':row,'markets':d.get('markets',[]),'prediction':d.get('prediction')}
            except Exception: return None
        m.inspect_match=inspect_match
        m.app.router.routes=[r for r in m.app.router.routes if getattr(r,'path',None) not in ['/matches','/mac/{match_id}','/match/{match_id}']]
        m.app.add_api_route('/matches',get_matches,methods=['GET'])
        m.app.add_api_route('/mac/{match_id}',get_match_detail,methods=['GET'])
        m.app.add_api_route('/match/{match_id}',get_match_detail,methods=['GET'])
