"""BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE

A football-first reasoning layer. Market odds are an auxiliary signal, never the
primary source of a prediction. The engine is deliberately conservative: it
only scores features that are actually present in the supplied football data
and never invents missing statistics.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class Feature:
    name: str
    value: Any
    source: str
    weight: float
    available: bool = True


class FootballIntelligenceEngine:
    NAME = "BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE"
    VERSION = "0.1.0"

    # Common football-stat aliases. Values are discovered recursively from the
    # real fixture/detail payload; aliases never create a value by themselves.
    ALIASES = {
        "home_xg": ["home_xg", "homeXg", "home_xG", "xg_home", "home_expected_goals"],
        "away_xg": ["away_xg", "awayXg", "away_xG", "xg_away", "away_expected_goals"],
        "home_goals": ["home_goals", "homeGoals", "goals_home"],
        "away_goals": ["away_goals", "awayGoals", "goals_away"],
        "home_shots": ["home_shots", "homeShots", "shots_home"],
        "away_shots": ["away_shots", "awayShots", "shots_away"],
        "home_shots_on_target": ["home_shots_on_target", "homeShotsOnTarget", "shots_on_target_home"],
        "away_shots_on_target": ["away_shots_on_target", "awayShotsOnTarget", "shots_on_target_away"],
        "home_possession": ["home_possession", "homePossession", "possession_home"],
        "away_possession": ["away_possession", "awayPossession", "possession_away"],
        "home_corners": ["home_corners", "homeCorners", "corners_home"],
        "away_corners": ["away_corners", "awayCorners", "corners_away"],
        "home_rank": ["home_rank", "homeRank", "rank_home"],
        "away_rank": ["away_rank", "awayRank", "rank_away"],
        "home_form": ["home_form", "homeForm", "form_home"],
        "away_form": ["away_form", "awayForm", "form_away"],
        "home_injuries": ["home_injuries", "homeInjuries", "injuries_home"],
        "away_injuries": ["away_injuries", "awayInjuries", "injuries_away"],
    }

    def __init__(self) -> None:
        self.feature_weights = {
            "xg": 0.26,
            "form": 0.18,
            "goals": 0.16,
            "shots": 0.10,
            "shots_on_target": 0.08,
            "possession": 0.04,
            "corners": 0.04,
            "rank": 0.07,
            "squad": 0.07,
        }

    @staticmethod
    def _norm(value: Any) -> str:
        return re.sub(r"[^a-z0-9çğıöşü]+", "", str(value or "").lower())

    @classmethod
    def _walk(cls, value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                yield child_path, child
                yield from cls._walk(child, child_path)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                yield from cls._walk(child, f"{path}[{i}]")

    @classmethod
    def _find_alias(cls, payload: Any, aliases: List[str]) -> tuple[Any, str] | tuple[None, None]:
        wanted = {cls._norm(a) for a in aliases}
        for path, value in cls._walk(payload):
            key = cls._norm(path.split(".")[-1].split("[")[0])
            if key in wanted and value not in (None, "", [], {}):
                return value, path
        return None, None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            if isinstance(value, bool):
                return None
            n = float(value)
            return n if math.isfinite(n) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _probability_from_odds(odds: Any) -> Dict[str, float]:
        if not isinstance(odds, list):
            return {}
        raw: Dict[str, float] = {}
        for item in odds:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            odd = FootballIntelligenceEngine._number(item.get("odd"))
            if value and odd and odd > 1.0:
                raw[value] = 1.0 / odd
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()} if total else {}

    def _extract_features(self, detail: Dict[str, Any]) -> List[Feature]:
        features: List[Feature] = []
        for name, aliases in self.ALIASES.items():
            value, source = self._find_alias(detail, aliases)
            if value is None:
                continue
            features.append(Feature(name=name, value=value, source=source or "api", weight=self.feature_weights.get(name.split("_")[-1], 0.05)))
        return features

    @staticmethod
    def _team_values(features: List[Feature], prefix: str) -> Dict[str, float]:
        out = {}
        for feature in features:
            if not feature.name.startswith(prefix + "_"):
                continue
            value = FootballIntelligenceEngine._number(feature.value)
            if value is not None:
                out[feature.name[len(prefix) + 1:]] = value
        return out

    def build_dossier(self, fixture: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        detail = detail or fixture or {}
        features = self._extract_features(detail)
        home = self._team_values(features, "home")
        away = self._team_values(features, "away")

        match = {
            "match_id": fixture.get("MatchID") or fixture.get("match_id") or detail.get("MatchID"),
            "home_team": fixture.get("Team1") or fixture.get("home_team") or detail.get("Team1"),
            "away_team": fixture.get("Team2") or fixture.get("away_team") or detail.get("Team2"),
            "competition": fixture.get("League") or fixture.get("league") or detail.get("League"),
            "date": fixture.get("Date") or fixture.get("date") or detail.get("Date"),
            "kickoff": fixture.get("KickoffUTC") or fixture.get("DateTime") or detail.get("DateTime"),
        }

        return {
            "engine": self.NAME,
            "version": self.VERSION,
            "match": match,
            "football_features": [asdict(x) for x in features],
            "home_metrics": home,
            "away_metrics": away,
            "data_coverage": round(len(features) / max(len(self.ALIASES), 1), 3),
            "missing_feature_count": max(len(self.ALIASES) - len(features), 0),
            "principle": "football-first; market is auxiliary and never substitutes for missing football data",
        }

    def score_match(self, dossier: Dict[str, Any], markets: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        home = dossier.get("home_metrics", {})
        away = dossier.get("away_metrics", {})
        signals: List[Dict[str, Any]] = []

        def add_signal(name: str, edge: float, weight: float, available: bool = True) -> None:
            if available:
                signals.append({"name": name, "edge": max(-1.0, min(1.0, edge)), "weight": weight})

        def edge(home_v: Optional[float], away_v: Optional[float], scale: float = 1.0) -> Optional[float]:
            if home_v is None or away_v is None:
                return None
            denom = abs(home_v) + abs(away_v) + scale
            return (home_v - away_v) / denom

        add_signal("xG", edge(home.get("xg"), away.get("xg"), 1.0) or 0.0, self.feature_weights["xg"], home.get("xg") is not None and away.get("xg") is not None)
        add_signal("gol üretimi", edge(home.get("goals"), away.get("goals"), 2.0) or 0.0, self.feature_weights["goals"], home.get("goals") is not None and away.get("goals") is not None)
        add_signal("şut", edge(home.get("shots"), away.get("shots"), 10.0) or 0.0, self.feature_weights["shots"], home.get("shots") is not None and away.get("shots") is not None)
        add_signal("isabetli şut", edge(home.get("shots_on_target"), away.get("shots_on_target"), 5.0) or 0.0, self.feature_weights["shots_on_target"], home.get("shots_on_target") is not None and away.get("shots_on_target") is not None)
        add_signal("topa sahip olma", edge(home.get("possession"), away.get("possession"), 100.0) or 0.0, self.feature_weights["possession"], home.get("possession") is not None and away.get("possession") is not None)
        add_signal("korner", edge(home.get("corners"), away.get("corners"), 8.0) or 0.0, self.feature_weights["corners"], home.get("corners") is not None and away.get("corners") is not None)

        if home.get("rank") is not None and away.get("rank") is not None:
            # Lower rank is stronger.
            add_signal("lig sıralaması", max(-1.0, min(1.0, (away["rank"] - home["rank"]) / 20.0)), self.feature_weights["rank"])

        raw_edge = sum(s["edge"] * s["weight"] for s in signals)
        weight_total = sum(s["weight"] for s in signals) or 1.0
        football_edge = raw_edge / weight_total

        # Convert a conservative football edge into 1X2 model probabilities.
        home_p = 0.333 + 0.34 * football_edge
        away_p = 0.333 - 0.34 * football_edge
        draw_p = 1.0 - home_p - away_p
        probabilities = {
            "1": round(max(0.02, min(0.90, home_p)), 4),
            "X": round(max(0.02, min(0.70, draw_p)), 4),
            "2": round(max(0.02, min(0.90, away_p)), 4),
        }
        total = sum(probabilities.values())
        probabilities = {k: v / total for k, v in probabilities.items()}

        market_prob = {}
        if markets:
            for market in markets:
                name = str(market.get("type") or market.get("gameName") or "").lower()
                if "1x2" in name or "match" in name or "maç sonucu" in name:
                    market_prob = self._probability_from_odds(market.get("odds"))
                    if market_prob:
                        break

        return {
            "football_probabilities": {k: round(v * 100, 2) for k, v in probabilities.items()},
            "football_edge": round(football_edge, 4),
            "signals": signals,
            "market_probabilities": {k: round(v * 100, 2) for k, v in market_prob.items()},
            "market_role": "auxiliary_cross_check",
            "data_coverage": dossier.get("data_coverage", 0),
            "confidence": round(max(probabilities.values()) * 100 * min(1.0, dossier.get("data_coverage", 0.0) + 0.25), 2),
        }

    def analyze(self, fixture: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        dossier = self.build_dossier(fixture, detail)
        score = self.score_match(dossier, (fixture or {}).get("_markets") or [])
        return {**dossier, "prediction": score}


ENGINE = FootballIntelligenceEngine()
