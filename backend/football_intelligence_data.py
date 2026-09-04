"""Historical football context for BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import five_dollar_bridge as five

_CACHE: Dict[str, tuple[float, Any]] = {}
TTL = 15 * 60


def _cache_get(key: str):
    item=_CACHE.get(key)
    if item and time.time()-item[0] < TTL: return item[1]
    return None

def _cache_put(key: str,value: Any): _CACHE[key]=(time.time(),value); return value


async def league_context(league_id: Any,target_date: str)->Dict[str,Any]:
    """One league-batched historical request; derive the table locally."""
    if not league_id: return {"fixtures":[],"standings":None}
    key=f"league:{league_id}:{target_date}"; cached=_cache_get(key)
    if cached is not None: return cached
    target=datetime.fromisoformat(target_date).replace(tzinfo=ZoneInfo("Europe/Istanbul")); end=(target+timedelta(days=1)).astimezone(timezone.utc); start=(target-timedelta(days=120)).astimezone(timezone.utc)
    try:
        payload=await five._get(f"leagues/{int(league_id)}/fixtures",{"start_time":int(start.timestamp()),"end_time":int(end.timestamp()),"status":"all","lang":"en","per_page":100})
        fixtures=payload.get("data") or []
    except Exception:
        fixtures=[]
    # Build a transparent 120-day league table from finished results. This is
    # not a claim of an official table and is used only as model evidence.
    table={}
    for f in fixtures:
        if str(f.get("status","")).lower() not in {"finished","ft","aet","pen"}: continue
        teams=f.get("teams") or {}; h=teams.get("home") or {}; a=teams.get("away") or {}; g=f.get("goals") or {}; hg,ag=g.get("home"),g.get("away")
        if hg is None or ag is None: continue
        for t in (h,a):
            tid=str(t.get("id")); table.setdefault(tid,{"team":t.get("name"),"played":0,"points":0,"gf":0,"ga":0})
        hh,aa=table[str(h.get("id"))],table[str(a.get("id"))]; hh["played"]+=1; aa["played"]+=1; hh["gf"]+=hg; hh["ga"]+=ag; aa["gf"]+=ag; aa["ga"]+=hg
        if hg>ag: hh["points"]+=3
        elif ag>hg: aa["points"]+=3
        else: hh["points"]+=1; aa["points"]+=1
    ordered=sorted(table.values(),key=lambda x:(x["points"],x["gf"]-x["ga"],x["gf"]),reverse=True)
    standings={"data":{"table":[{"position":i+1,"team":{"id":None,"name":x["team"]},"played":x["played"],"points":x["points"],"goals_for":x["gf"],"goals_against":x["ga"]} for i,x in enumerate(ordered)]},"source":"computed_from_finished_results","window_days":120}
    # Keep a name-indexed copy because current provider ids are stable but this
    # derived table deliberately does not manufacture ids.
    result={"fixtures":fixtures,"standings":standings,"standings_by_name":{x["team"]:x for x in ordered}}
    return _cache_put(key,result)


def _finished_for_team(fixtures:List[Dict[str,Any]],team_id:Any)->List[Dict[str,Any]]:
    out=[]; tid=str(team_id)
    for f in fixtures:
        if str(f.get("status","")).lower() not in {"finished","ft","aet","pen"}: continue
        t=f.get("teams") or {}; h=t.get("home") or {}; a=t.get("away") or {}
        if str(h.get("id"))==tid or str(a.get("id"))==tid: out.append(f)
    out.sort(key=lambda x:x.get("kickoff_ts") or 0,reverse=True); return out[:10]


def team_form(fixtures:List[Dict[str,Any]],team_id:Any)->Dict[str,Any]:
    games=_finished_for_team(fixtures,team_id)
    if not games: return {"sample":0,"points_per_game":None,"goals_for_avg":None,"goals_against_avg":None,"form":""}
    tid=str(team_id); points=gf=ga=0.; form=[]; home_games=away_games=0
    for f in games:
        t=f.get("teams") or {}; h=t.get("home") or {}; a=t.get("away") or {}; g=f.get("goals") or {}; hg,ag=g.get("home"),g.get("away")
        if hg is None or ag is None: continue
        if str(h.get("id"))==tid: team_gf,team_ga=float(hg),float(ag); home_games+=1
        else: team_gf,team_ga=float(ag),float(hg); away_games+=1
        gf+=team_gf; ga+=team_ga
        if team_gf>team_ga: points+=3; form.append("W")
        elif team_gf==team_ga: points+=1; form.append("D")
        else: form.append("L")
    n=len(form)
    return {"sample":n,"points":points,"points_per_game":round(points/n,3) if n else None,"goals_for_avg":round(gf/n,3) if n else None,"goals_against_avg":round(ga/n,3) if n else None,"goal_diff_avg":round((gf-ga)/n,3) if n else None,"form":"".join(form),"home_games":home_games,"away_games":away_games}


def standings_for_team(standings:Any,team_id:Any,team_name:Any=None)->Dict[str,Any]:
    rows=((standings or {}).get("data") or {}).get("table") or []; name=str(team_name or "").strip().lower()
    for row in rows:
        team=row.get("team") or {}
        if team_id is not None and str(team.get("id"))==str(team_id): return {"position":row.get("position"),"played":row.get("played"),"points":row.get("points"),"goals_for":row.get("goals_for"),"goals_against":row.get("goals_against")}
        if name and str(team.get("name") or "").strip().lower()==name: return {"position":row.get("position"),"played":row.get("played"),"points":row.get("points"),"goals_for":row.get("goals_for"),"goals_against":row.get("goals_against")}
    return {}


async def build_match_context(row:Dict[str,Any])->Dict[str,Any]:
    league_id=row.get("LeagueID"); target=str(row.get("KickoffUTC") or row.get("Date") or "")[:10]
    context=await league_context(league_id,target) if league_id and target else {"fixtures":[],"standings":None}
    return {"home":{"team_id":row.get("HomeTeamID"),"name":row.get("Team1"),"recent_form":team_form(context["fixtures"],row.get("HomeTeamID")),"standing":standings_for_team(context["standings"],row.get("HomeTeamID"),row.get("Team1"))},"away":{"team_id":row.get("AwayTeamID"),"name":row.get("Team2"),"recent_form":team_form(context["fixtures"],row.get("AwayTeamID")),"standing":standings_for_team(context["standings"],row.get("AwayTeamID"),row.get("Team2"))},"league":{"id":league_id,"name":row.get("League"),"country":row.get("Country")},"history_window_days":120,"source":"5DollarFootballAPI"}
