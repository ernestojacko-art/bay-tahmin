"""Production 5DollarFootballAPI adapter for the Cloud Intelligence Engine."""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.core.exceptions import ProviderError, ProviderNotConfiguredError
from app.providers.base import BaseFootballDataProvider
from app.providers.models import (
    Fixture, FixtureCongestion, FixtureStatus, H2HRecord, MatchResult,
    OddsMarket, OddsSelection, RecentMatch, StandingsEntry, TeamRawDataset,
    TeamRef,
)


class FiveDollarFootballProvider(BaseFootballDataProvider):
    """Maps the existing 5DollarFootballAPI v1 into the Cloud Engine models."""

    name = "5dollarfootballapi"

    def __init__(self) -> None:
        self._base = os.getenv("FIVE_DOLLAR_BASE_URL", "https://api.5dollarfootballapi.com/v1").rstrip("/")
        self._key = os.getenv("FIVE_DOLLAR_API_KEY")
        if not self._key:
            raise ProviderNotConfiguredError("FIVE_DOLLAR_API_KEY environment variable bulunamadı.")
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Any]] = {}
        self._inflight: dict[tuple[str, tuple[tuple[str, str], ...]], asyncio.Task] = {}
        self._league_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._fixture_cache: dict[str, Fixture] = {}

    def _key_for(self, path: str, params: Optional[dict[str, Any]]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return path, tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))

    def _ttl(self, path: str) -> int:
        if path == "fixtures": return 600
        if path.startswith("leagues/"): return 21600
        if path.startswith("teams/"): return 21600
        if path.startswith("fixtures/"): return 300
        return 300

    async def _request(self, path: str, params: Optional[dict[str, Any]] = None, retries: int = 2) -> Any:
        key = self._key_for(path, params)
        now = datetime.now(timezone.utc).timestamp()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._ttl(path):
            return cached[1]
        if key in self._inflight:
            return await self._inflight[key]

        async def run() -> Any:
            last: Exception | None = None
            try:
                for attempt in range(retries + 1):
                    try:
                        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=4.0)) as client:
                            r = await client.get(
                                f"{self._base}/{path.lstrip('/')}",
                                headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json"},
                                params=params or {},
                            )
                        if r.status_code == 429:
                            if cached: return cached[1]
                            raise ProviderError("5DollarFootballAPI rate limit (429)")
                        if r.status_code in {500, 502, 503, 504} and attempt < retries:
                            await asyncio.sleep(0.5 * (attempt + 1)); continue
                        if r.status_code in {401, 403}:
                            raise ProviderError(f"5DollarFootballAPI authentication failed ({r.status_code})")
                        if r.status_code != 200:
                            raise ProviderError(f"5DollarFootballAPI HTTP {r.status_code}: {r.text[:300]}")
                        payload = r.json()
                        if isinstance(payload, dict) and payload.get("success") not in (None, 1, True):
                            raise ProviderError(f"5DollarFootballAPI error: {payload}")
                        self._cache[key] = (datetime.now(timezone.utc).timestamp(), payload)
                        return payload
                    except ProviderError:
                        raise
                    except (httpx.HTTPError, ValueError) as exc:
                        last = exc
                        if attempt < retries:
                            await asyncio.sleep(0.5 * (attempt + 1)); continue
                        raise ProviderError(f"5DollarFootballAPI bağlantı/JSON hatası: {exc}") from exc
                raise ProviderError(f"5DollarFootballAPI request failed: {last}")
            finally:
                self._inflight.pop(key, None)

        task = asyncio.create_task(run())
        self._inflight[key] = task
        return await task

    async def _all_pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, 101):
            payload = await self._request(path, {**params, "page": page})
            page_rows = payload.get("data") or [] if isinstance(payload, dict) else []
            rows.extend(x for x in page_rows if isinstance(x, dict))
            pagination = payload.get("pagination") or {} if isinstance(payload, dict) else {}
            if not pagination.get("has_more"):
                return rows
        raise ProviderError("5DollarFootballAPI pagination limit exceeded")

    @staticmethod
    def _status(value: Any) -> FixtureStatus:
        v = str(value or "").lower()
        if v in {"live", "inplay", "in_play"}: return FixtureStatus.LIVE
        if v in {"finished", "ft", "aet", "pen"}: return FixtureStatus.FINISHED
        if v in {"postponed", "canceled", "cancelled", "abandoned"}: return FixtureStatus.POSTPONED
        return FixtureStatus.SCHEDULED

    @classmethod
    def _fixture(cls, raw: dict[str, Any]) -> Fixture:
        league = raw.get("league") or {}; teams = raw.get("teams") or {}
        home = teams.get("home") or {}; away = teams.get("away") or {}
        kickoff_raw = raw.get("kickoff_utc") or raw.get("kickoff")
        if not kickoff_raw: raise ProviderError("Fixture missing kickoff_utc")
        try: kickoff = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00"))
        except ValueError as exc: raise ProviderError(f"Invalid kickoff: {kickoff_raw}") from exc
        lid = str(league.get("id") or "")
        return Fixture(
            match_id=str(raw.get("id")), league_id=lid, league_name=league.get("name"), kickoff=kickoff,
            home_team=TeamRef(team_id=str(home.get("id")), name=str(home.get("name") or "Unknown"), league_id=lid or None),
            away_team=TeamRef(team_id=str(away.get("id")), name=str(away.get("name") or "Unknown"), league_id=lid or None),
            status=cls._status(raw.get("status")), round=raw.get("round"),
        )

    async def get_fixtures(self, on_date: date, league_id: Optional[str] = None) -> list[Fixture]:
        tz = ZoneInfo("Europe/Istanbul")
        start = datetime.combine(on_date, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        end = datetime.combine(on_date + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        rows = await self._all_pages("fixtures", {"start_time": int(start.timestamp()), "end_time": int(end.timestamp()), "status": "all", "lang": "en", "per_page": 100})
        out=[]
        for raw in rows:
            if league_id and str((raw.get("league") or {}).get("id")) != str(league_id): continue
            f=self._fixture(raw); self._fixture_cache[f.match_id]=f; out.append(f)
        return out

    async def get_fixture(self, match_id: str) -> Optional[Fixture]:
        if str(match_id) in self._fixture_cache: return self._fixture_cache[str(match_id)]
        payload=await self._request(f"fixtures/{match_id}", {"lang":"en"})
        raw=payload.get("data") or {}
        if not raw: return None
        f=self._fixture(raw); self._fixture_cache[f.match_id]=f; return f

    async def _league_rows(self, league_id: str, target: date) -> list[dict[str, Any]]:
        key=(str(league_id),target.isoformat())
        if key in self._league_cache: return self._league_cache[key]
        tz=ZoneInfo("Europe/Istanbul")
        start=datetime.combine(target-timedelta(days=365),datetime.min.time(),tzinfo=tz).astimezone(timezone.utc)
        end=datetime.combine(target+timedelta(days=1),datetime.min.time(),tzinfo=tz).astimezone(timezone.utc)
        rows=await self._all_pages(f"leagues/{league_id}/fixtures", {"start_time":int(start.timestamp()),"end_time":int(end.timestamp()),"status":"all","lang":"en","per_page":100})
        self._league_cache[key]=rows; return rows

    @staticmethod
    def _finished(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if str(r.get("status") or "").lower() in {"finished","ft","aet","pen"}]

    @staticmethod
    def _recent(raw: dict[str, Any], team_id: str) -> Optional[RecentMatch]:
        teams=raw.get("teams") or {}; h=teams.get("home") or {}; a=teams.get("away") or {}; g=raw.get("goals") or {}; hg,ag=g.get("home"),g.get("away")
        if hg is None or ag is None: return None
        is_home=str(h.get("id"))==str(team_id); gf,ga=(int(hg),int(ag)) if is_home else (int(ag),int(hg))
        result=MatchResult.DRAW if gf==ga else MatchResult.HOME_WIN if (gf>ga and is_home) or (gf<ga and not is_home) else MatchResult.AWAY_WIN
        dt=str(raw.get("kickoff_utc") or raw.get("kickoff") or "1970-01-01")
        return RecentMatch(match_id=str(raw.get("id")),date=date.fromisoformat(dt[:10]),is_home=is_home,opponent=TeamRef(team_id=str((a if is_home else h).get("id")),name=str((a if is_home else h).get("name") or "Unknown")),goals_for=gf,goals_against=ga,result=result)

    @classmethod
    def _standings(cls, team: TeamRef, rows: list[dict[str, Any]]) -> Optional[StandingsEntry]:
        table: dict[str, dict[str, Any]]={}
        for r in cls._finished(rows):
            t=r.get("teams") or {}; h=t.get("home") or {}; a=t.get("away") or {}; g=r.get("goals") or {}; hg,ag=g.get("home"),g.get("away")
            if hg is None or ag is None: continue
            for tm in (h,a): table.setdefault(str(tm.get("id")),{"name":tm.get("name"),"played":0,"won":0,"drawn":0,"lost":0,"gf":0,"ga":0,"points":0})
            H,A=table[str(h.get("id"))],table[str(a.get("id"))]; H["played"]+=1;A["played"]+=1;H["gf"]+=int(hg);H["ga"]+=int(ag);A["gf"]+=int(ag);A["ga"]+=int(hg)
            if hg>ag: H["won"]+=1;H["points"]+=3;A["lost"]+=1
            elif ag>hg: A["won"]+=1;A["points"]+=3;H["lost"]+=1
            else: H["drawn"]+=1;A["drawn"]+=1;H["points"]+=1;A["points"]+=1
        ordered=sorted(table.items(),key=lambda x:(x[1]["points"],x[1]["gf"]-x[1]["ga"],x[1]["gf"]),reverse=True)
        for pos,(tid,r) in enumerate(ordered,1):
            if tid==str(team.team_id): return StandingsEntry(team=team,position=pos,played=r["played"],won=r["won"],drawn=r["drawn"],lost=r["lost"],goals_for=r["gf"],goals_against=r["ga"],points=r["points"])
        return None

    async def get_team_dataset(self, team_id: str, league_id: Optional[str] = None) -> TeamRawDataset:
        fixture=next((f for f in self._fixture_cache.values() if f.home_team.team_id==str(team_id) or f.away_team.team_id==str(team_id)),None)
        league_id=league_id or (fixture.league_id if fixture else None)
        if not league_id: raise ProviderError(f"League ID unavailable for team {team_id}")
        target=fixture.kickoff.date() if fixture else datetime.now(ZoneInfo("Europe/Istanbul")).date()
        rows=await self._league_rows(league_id,target); finished=self._finished(rows)
        recent=[m for m in (self._recent(r,team_id) for r in finished) if m is not None]; recent.sort(key=lambda x:x.date,reverse=True)
        name=f"Team {team_id}"
        for r in rows:
            t=r.get("teams") or {}
            for side in (t.get("home") or {},t.get("away") or {}):
                if str(side.get("id"))==str(team_id): name=str(side.get("name") or name)
        team=TeamRef(team_id=str(team_id),name=name,league_id=str(league_id))
        standing=self._standings(team,rows)
        home=[m for m in recent if m.is_home]; away=[m for m in recent if not m.is_home]
        congestion=None
        if recent:
            congestion=FixtureCongestion(matches_last_14_days=sum(1 for m in recent if 0 <= (target-m.date).days <= 14),days_since_last_match=max(0,(target-recent[0].date).days),next_match_days_away=None)
        return TeamRawDataset(team=team,recent_matches=recent[:20],recent_matches_home=home[:20],recent_matches_away=away[:20],standing=standing,fixture_congestion=congestion)

    async def get_standings(self, league_id: str) -> list[StandingsEntry]:
        rows=await self._league_rows(league_id,datetime.now(ZoneInfo("Europe/Istanbul")).date()); teams={}
        for r in rows:
            t=r.get("teams") or {}; league=r.get("league") or {}
            for side in (t.get("home") or {},t.get("away") or {}):
                if side.get("id") is not None: teams[str(side["id"])]=TeamRef(team_id=str(side["id"]),name=str(side.get("name") or "Unknown"),league_id=str(league.get("id") or league_id))
        return sorted([s for tm in teams.values() if (s:=self._standings(tm,rows))],key=lambda x:x.position)

    async def get_h2h(self, team_a_id: str, team_b_id: str, limit: int = 10) -> Optional[H2HRecord]:
        fixture=next((f for f in self._fixture_cache.values() if f.home_team.team_id==str(team_a_id) or f.away_team.team_id==str(team_a_id)),None)
        if not fixture: return None
        rows=await self._league_rows(fixture.league_id,fixture.kickoff.date()); out=[]
        for r in self._finished(rows):
            t=r.get("teams") or {}; h=t.get("home") or {}; a=t.get("away") or {}
            if {str(h.get("id")),str(a.get("id"))}!={str(team_a_id),str(team_b_id)}: continue
            m=self._recent(r,team_a_id)
            if m: out.append(m)
        return H2HRecord(matches=out[-limit:]) if out else None

    @staticmethod
    def _market_rows(payload: Any) -> list[OddsMarket]:
        data=(payload or {}).get("data") or {}; books=[]
        raw=data.get("odds")
        if isinstance(raw,dict): books.append(("Bet 365",raw))
        elif isinstance(raw,list):
            for b in raw:
                if isinstance(b,dict) and isinstance(b.get("odds"),dict): books.append((b.get("name") or "Bet 365",b["odds"]))
        if isinstance(data.get("bookmakers"),list):
            for b in data["bookmakers"]:
                if isinstance(b,dict) and isinstance(b.get("odds"),dict): books.append((b.get("name") or "Bet 365",b["odds"]))
        aliases={"asian":"asian_handicap","goalline":"goal_line","corner":"corner_line","cards":"card_line","asian_half":"asian_handicap_half","goalline_half":"goal_line_half","corner_half":"corner_line_half","corner_asian":"corner_asian","cards_asian":"card_asian","btts":"btts","1x2_half":"1x2_half"}
        names={"1x2":"Maç Sonucu 1X2","1x2_half":"İlk Yarı Maç Sonucu","goal_line":"Alt/Üst Gol","goal_line_half":"İlk Yarı Alt/Üst Gol","btts":"Karşılıklı Gol (KG)","asian_handicap":"Asya Handikap","asian_handicap_half":"İlk Yarı Asya Handikap","corner_line":"Alt/Üst Korner","corner_line_half":"İlk Yarı Alt/Üst Korner","corner_asian":"Korner Asya Handikap","card_line":"Alt/Üst Kart","card_asian":"Kart Asya Handikap"}
        result=[]; seen=set()
        for book,odds in books:
            for raw_key,e in odds.items():
                key=aliases.get(raw_key,raw_key); stage=None
                if isinstance(e,dict):
                    for k in ("current","closing","opening"):
                        if isinstance(e.get(k),dict): stage=e[k]; break
                if not stage or key not in names: continue
                selections=[]; line=stage.get("line")
                def add(label,val):
                    try:
                        price=float(val)
                        if price>0: selections.append(OddsSelection(label=label,price=price))
                    except (TypeError,ValueError): pass
                if key in {"1x2","1x2_half"}: add("1",stage.get("home"));add("X",stage.get("draw"));add("2",stage.get("away"))
                elif key in {"goal_line","goal_line_half","corner_line","corner_line_half","card_line"}: add(f"Üst {line}",stage.get("over"));add(f"Alt {line}",stage.get("under"))
                elif key=="btts": add("Var",stage.get("yes"));add("Yok",stage.get("no"))
                else: add(f"Ev {line}",stage.get("home"));add(f"Dep {line}",stage.get("away"))
                if selections:
                    market=(f"{book}",key,str(line))
                    if market not in seen: result.append(OddsMarket(market_name=f"{names[key]} ({book})",selections=selections,bookmaker=book));seen.add(market)
        return result

    async def get_odds(self, match_id: str) -> list[OddsMarket]:
        try: return self._market_rows(await self._request(f"fixtures/{match_id}/odds",{"lang":"en"}))
        except Exception: return []

    async def health_check(self) -> bool:
        try:
            await self._all_pages("fixtures", {"start_time": int(datetime.now(timezone.utc).timestamp())-3600, "end_time": int(datetime.now(timezone.utc).timestamp())+86400, "status":"all", "lang":"en", "per_page":1})
            return True
        except Exception: return False
