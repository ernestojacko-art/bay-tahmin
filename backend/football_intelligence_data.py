"""Historical football context for BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE."""
from __future__ import annotations
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo
import five_dollar_bridge as five

_CACHE: Dict[str, tuple[float, Any]] = {}
TTL = 15 * 60

def _cache_get(key):
    x = _CACHE.get(key)
    return x[1] if x and time.time() - x[0] < TTL else None

def _cache_put(key, value):
    _CACHE[key] = (time.time(), value)
    return value

def _finished(fixtures):
    return [f for f in fixtures if str(f.get('status', '')).lower() in {'finished','ft','aet','pen'}]

async def league_context(league_id: Any, target_date: str) -> Dict[str, Any]:
    if not league_id:
        return {'fixtures': [], 'standings': None, 'league_goal_avg': {'home': 1.35, 'away': 1.10}}
    key = f'league:{league_id}:{target_date}'
    cached = _cache_get(key)
    if cached is not None:
        return cached
    target = datetime.fromisoformat(target_date).replace(tzinfo=ZoneInfo('Europe/Istanbul'))
    end = (target + timedelta(days=1)).astimezone(timezone.utc)
    start = (target - timedelta(days=365)).astimezone(timezone.utc)
    try:
        payload = await five._get(f'leagues/{int(league_id)}/fixtures', {
            'start_time': int(start.timestamp()), 'end_time': int(end.timestamp()),
            'status': 'all', 'lang': 'en', 'per_page': 100
        })
        fixtures = payload.get('data') or []
    except Exception:
        fixtures = []

    fs = _finished(fixtures)
    table, total_h, total_a, n = {}, 0.0, 0.0, 0
    for f in fs:
        t, g = f.get('teams') or {}, f.get('goals') or {}
        h, a = t.get('home') or {}, t.get('away') or {}
        hg, ag = g.get('home'), g.get('away')
        if hg is None or ag is None:
            continue
        n += 1; total_h += float(hg); total_a += float(ag)
        for tm in (h, a):
            tid = str(tm.get('id'))
            table.setdefault(tid, {'team': tm.get('name'), 'team_id': tm.get('id'), 'played': 0, 'points': 0, 'gf': 0., 'ga': 0.})
        hh, aa = table[str(h.get('id'))], table[str(a.get('id'))]
        hh['played'] += 1; aa['played'] += 1
        hh['gf'] += hg; hh['ga'] += ag; aa['gf'] += ag; aa['ga'] += hg
        if hg > ag: hh['points'] += 3
        elif ag > hg: aa['points'] += 3
        else: hh['points'] += 1; aa['points'] += 1

    ordered = sorted(table.values(), key=lambda x: (x['points'], x['gf']-x['ga'], x['gf']), reverse=True)
    rows = [{'position': i+1, 'team': {'id': x['team_id'], 'name': x['team']}, 'played': x['played'], 'points': x['points'], 'goals_for': x['gf'], 'goals_against': x['ga']} for i, x in enumerate(ordered)]
    standings = {'data': {'table': rows}, 'source': 'computed_from_finished_results', 'window_days': 365}
    return _cache_put(key, {
        'fixtures': fixtures, 'standings': standings,
        'league_goal_avg': {'home': total_h/n if n else 1.35, 'away': total_a/n if n else 1.10}
    })

def _team_games(fixtures, team_id, limit=20):
    tid = str(team_id); out = []
    for f in _finished(fixtures):
        t = f.get('teams') or {}; h, a = t.get('home') or {}, t.get('away') or {}
        if str(h.get('id')) == tid or str(a.get('id')) == tid:
            out.append(f)
    return sorted(out, key=lambda x: x.get('kickoff_ts') or 0, reverse=True)[:limit]

def _metrics(games, team_id):
    tid = str(team_id); pts = gf = ga = 0.0; form = []
    for f in games:
        t, g = f.get('teams') or {}, f.get('goals') or {}
        h, a = t.get('home') or {}, t.get('away') or {}
        hg, ag = g.get('home'), g.get('away')
        if hg is None or ag is None:
            continue
        home = str(h.get('id')) == tid
        tg, tc = (float(hg), float(ag)) if home else (float(ag), float(hg))
        gf += tg; ga += tc
        if tg > tc: pts += 3; form.append('W')
        elif tg == tc: pts += 1; form.append('D')
        else: form.append('L')
    n = len(form)
    btts = sum(1 for f in games if (f.get('goals') or {}).get('home') not in (None, 0) and (f.get('goals') or {}).get('away') not in (None, 0))
    over = sum(1 for f in games if (f.get('goals') or {}).get('home') is not None and (f.get('goals') or {}).get('away') is not None and float((f.get('goals') or {}).get('home')) + float((f.get('goals') or {}).get('away')) > 2.5)
    return {'sample': n, 'points': pts, 'points_per_game': round(pts/n,3) if n else None,
            'goals_for_avg': round(gf/n,3) if n else None, 'goals_against_avg': round(ga/n,3) if n else None,
            'goal_diff_avg': round((gf-ga)/n,3) if n else None, 'btts_rate': round(btts/n,3) if n else None, 'over_2_5_rate': round(over/n,3) if n else None, 'form': ''.join(form)}

def team_form(fixtures, team_id):
    games = _team_games(fixtures, team_id, 20)
    last5 = _metrics(games[:5], team_id)
    last10 = _metrics(games[:10], team_id)
    last20 = _metrics(games[:20], team_id)
    tid = str(team_id)
    home_games = [f for f in games if str((f.get('teams') or {}).get('home', {}).get('id')) == tid]
    away_games = [f for f in games if str((f.get('teams') or {}).get('away', {}).get('id')) == tid]
    home = _metrics(home_games[:10], team_id)
    away = _metrics(away_games[:10], team_id)
    weights = [(last5, .50), (last10, .30), (last20, .20)]
    def weighted(key):
        vals = [(float(x[key]), w) for x,w in weights if x.get(key) is not None]
        return round(sum(v*w for v,w in vals)/sum(w for _,w in vals), 3) if vals else None
    return {'last_5': last5, 'last_10': last10, 'last_20': last20, 'home_split': home, 'away_split': away,
            'sample': last20['sample'], 'points_per_game': weighted('points_per_game'),
            'goals_for_avg': weighted('goals_for_avg'), 'goals_against_avg': weighted('goals_against_avg'),
            'btts_rate': weighted('btts_rate'), 'over_2_5_rate': weighted('over_2_5_rate'),
            'form': last5['form'], 'weighted': True}

def standings_for_team(standings, team_id, team_name=None):
    rows = ((standings or {}).get('data') or {}).get('table') or []
    for row in rows:
        tm = row.get('team') or {}
        if team_id is not None and str(tm.get('id')) == str(team_id):
            return {'position': row.get('position'), 'played': row.get('played'), 'points': row.get('points'), 'goals_for': row.get('goals_for'), 'goals_against': row.get('goals_against')}
    return {}

def _strength(form, standing, league_avg, home):
    ppg, gf, ga = form.get('points_per_game'), form.get('goals_for_avg'), form.get('goals_against_avg')
    base = max(.35, float(league_avg or 1.2))
    attack = max(.55, min(1.65, (float(gf)/base) if gf is not None else 1.0))
    defence = max(.55, min(1.65, (float(ga)/base) if ga is not None else 1.0))
    pos = standing.get('position')
    rank_bonus = max(-.12, min(.12, (20-float(pos))*.006)) if pos else 0
    form_bonus = max(-.10, min(.10, ((float(ppg)-1.35)*.05))) if ppg is not None else 0
    return {'attack_strength': round(max(.65,min(1.5,attack*(1+form_bonus+rank_bonus))),3),
            'defence_weakness': round(max(.65,min(1.5,defence*(1-form_bonus-rank_bonus))),3),
            'elo': round(1500+400*rank_bonus+120*form_bonus,1), 'played': standing.get('played') or 0,
            'elo_source': 'derived_strength_rating'}

def _h2h(fixtures, hid, aid):
    out=[]; a,b=str(hid),str(aid)
    for f in _finished(fixtures):
        t=f.get('teams') or {}; h=t.get('home') or {}; aw=t.get('away') or {}
        if {str(h.get('id')),str(aw.get('id'))}!={a,b}: continue
        g=f.get('goals') or {}; out.append({'date':f.get('kickoff_utc'),'home':h.get('name'),'away':aw.get('name'),'score':f"{g.get('home')}-{g.get('away')}"})
    return out[-10:]

async def build_match_context(row):
    lid = row.get('LeagueID'); target = str(row.get('KickoffUTC') or row.get('Date') or '')[:10]
    lc = await league_context(lid, target) if lid and target else {'fixtures': [], 'standings': None, 'league_goal_avg': {'home':1.35,'away':1.10}}
    hf, af = team_form(lc['fixtures'], row.get('HomeTeamID')), team_form(lc['fixtures'], row.get('AwayTeamID'))
    hs = standings_for_team(lc.get('standings'), row.get('HomeTeamID'), row.get('Team1'))
    ass = standings_for_team(lc.get('standings'), row.get('AwayTeamID'), row.get('Team2'))
    av = lc.get('league_goal_avg') or {'home':1.35,'away':1.10}
    h2h = _h2h(lc['fixtures'], row.get('HomeTeamID'), row.get('AwayTeamID'))
    quality = 'high' if hf.get('sample',0)>=10 and af.get('sample',0)>=10 else 'medium' if hf.get('sample',0)>=5 and af.get('sample',0)>=5 else 'low'
    providers = {'api_football_configured': bool(os.getenv('API_FOOTBALL_KEY') or os.getenv('APIFOOTBALL_KEY')), 'rich_stats_configured': bool(os.getenv('FOOTBALL_STATS_API_KEY')), 'news_configured': bool(os.getenv('FOOTBALL_NEWS_API_KEY'))}
    return {'home': {'team_id':row.get('HomeTeamID'),'name':row.get('Team1'),'recent_form':hf,'standing':hs,'strength':_strength(hf,hs,av.get('home'),True)},
            'away': {'team_id':row.get('AwayTeamID'),'name':row.get('Team2'),'recent_form':af,'standing':ass,'strength':_strength(af,ass,av.get('away'),False)},
            'league': {'id':lid,'name':row.get('League'),'country':row.get('Country'),'home_goal_avg':av.get('home'),'away_goal_avg':av.get('away')},
            'history_window_days':365,'h2h':h2h,
            'data_availability': {'xg': False, 'xga': False, 'shots': False, 'shots_on_target': False, 'big_chances': False, 'possession': False, 'corners': False, 'cards': False, 'injuries': False, 'suspensions': False, 'lineups': False, 'news': False},
            'provider_readiness': providers,
            'data_quality': {'level':quality,'home_sample':hf.get('sample',0),'away_sample':af.get('sample',0)}}
