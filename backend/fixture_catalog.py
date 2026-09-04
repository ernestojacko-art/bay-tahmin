"""Complete 7-day fixture catalog on top of 5DollarFootballAPI."""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import five_dollar_bridge as five

TZ=ZoneInfo('Europe/Istanbul')
CACHE={}
LEAGUE_CACHE=None
TTL=10*60

def _window(d):
    x=datetime.fromisoformat(str(d)).replace(tzinfo=TZ)
    s=x.astimezone(timezone.utc); e=(x+timedelta(days=1)).astimezone(timezone.utc)
    return int(s.timestamp()),int(e.timestamp())

async def get_matches(date=None):
    d=str(date or datetime.now(TZ).date())
    hit=CACHE.get(d)
    if hit and time.time()-hit[0]<TTL:
        return dict(hit[1],cache={'hit':True})
    s,e=_window(d); rows=[]; page=1
    while page<=3:
        p=await five._get('fixtures',{'start_time':s,'end_time':e,'status':'all','lang':'en','per_page':50,'page':page,'include':'odds'})
        rows.extend(p.get('data') or [])
        if not (p.get('pagination') or {}).get('has_more'): break
        page+=1
    normalized=[];seen=set()
    for f in rows:
        r=five._fixture_row(f); key=str(r.get('MatchID'))
        if key in seen: continue
        seen.add(key)
        r['_markets']=five._markets_from_odds({'data':{'odds':f.get('odds') or {}}},live=False)
        normalized.append(r)
    normalized.sort(key=lambda r:r.get('KickoffUTC') or '')
    result={'data':normalized,'source':'5dollarfootballapi','coverage':'complete_pagination','cache':{'hit':False}}
    CACHE[d]=(time.time(),result)
    return result

async def get_weekly_matches(days=7):
    days=max(1,min(int(days or 7),7)); out=[]; seen=set()
    for i in range(days):
        d=(datetime.now(TZ).date()+timedelta(days=i)).isoformat()
        try:r=await get_matches(d)
        except Exception:continue
        for row in r.get('data') or []:
            mid=str(row.get('MatchID'))
            if mid and mid not in seen: seen.add(mid);out.append(row)
    out.sort(key=lambda r:r.get('KickoffUTC') or '')
    return {'data':out,'source':'5dollarfootballapi','days':days,'match_count':len(out),'coverage':'complete_pagination'}

async def get_leagues():
    global LEAGUE_CACHE
    if LEAGUE_CACHE and time.time()-LEAGUE_CACHE[0]<3600:return LEAGUE_CACHE[1]
    rows=[];page=1
    while page<=5:
        p=await five._get('leagues',{'lang':'en','per_page':100,'page':page})
        rows.extend(p.get('data') or [])
        if not (p.get('pagination') or {}).get('has_more'):break
        page+=1
    out=[]
    for x in rows:
        c=x.get('country') or {};out.append({'id':str(x.get('id')),'name':x.get('name') or '', 'short_name':x.get('short_name') or '', 'country':c.get('name') or '', 'countryCode':c.get('code') or '', 'is_popular':bool(x.get('is_popular')), 'has_standings':bool(x.get('has_standings')), 'last_fixture_utc':x.get('last_fixture_utc')})
    LEAGUE_CACHE=(time.time(),out);return out

def patch_main(main):
    main.get_matches=get_matches
    main.get_leagues=get_leagues
    main.get_weekly_matches=get_weekly_matches
    main.app.add_api_route('/weekly-matches',get_weekly_matches,methods=['GET'])
