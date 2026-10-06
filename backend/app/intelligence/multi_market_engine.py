"""
Multi-Market Pick Engine.

This module exists for exactly one reason: the "İdeal 4'lü" and
"Sürpriz 4'lü" products must never again be a free-text prompt handed to a
chat model. They must be concrete, real-market-backed selections, computed
the same deterministic way every time.

Two independent things are built here, per already-analyzed fixture:

  * `ideal_candidate_for_match`   -- the single safest/most-backed pick for
    this fixture, chosen across SEVERAL markets (1X2, Double Chance,
    Over/Under 2.5, BTTS) rather than being hard-wired to 1X2 alone.

  * `surprise_candidates_for_match` -- every real-market divergence
    (model vs. market) found for this fixture across 1X2, Over/Under,
    BTTS and Asian Handicap. İY/MS (HT/FT) is EXCLUDED here on purpose --
    that has its own dedicated engine (`app.intelligence.surprise_engine`)
    and its own product card ("İY/MS Sürpriz 4'lü").

Both functions only ever read already-computed `MatchPrediction` numbers
and the fixture's real odds markets (`MatchRawDataset.odds_markets`) --
never fabricate a probability or a market price. A market that cannot be
found or parsed simply means no candidate is produced for it; it is never
estimated.
"""
from __future__ import annotations

from app.providers.models import MatchRawDataset, OddsMarket
from app.schemas.common import DataQuality, RiskLevel
from app.schemas.picks import PickCandidate
from app.schemas.prediction import MatchPrediction

# Below this, a pick is not "ideal" (safe) -- it has no real statistical
# backing to be presented as a low-risk choice.
IDEAL_MIN_PROBABILITY = 0.55

# A model-vs-market gap below this is normal noise, not a genuine surprise.
SURPRISE_MIN_DIVERGENCE = 0.12


def _is_half_time_market(name: str) -> bool:
    lowered = name.lower()
    return "ilk yarı" in lowered or "i̇lk yarı" in lowered or ("ilk" in lowered and "yarı" in lowered)


def _find_market(markets: list[OddsMarket], *name_hints: str) -> OddsMarket | None:
    """First non-half-time market whose name contains one of `name_hints`."""
    for market in markets:
        name = market.market_name or ""
        if _is_half_time_market(name):
            continue
        lowered = name.lower()
        if any(hint in lowered for hint in name_hints):
            return market
    return None


def _price(market: OddsMarket, label: str) -> float | None:
    for selection in market.selections:
        if selection.label == label:
            return selection.price
    return None


def _implied_two_way(price_a: float, price_b: float) -> tuple[float, float] | None:
    """Overround-removed implied probabilities for a two-outcome market."""
    if price_a <= 0 or price_b <= 0:
        return None
    raw_a, raw_b = 1 / price_a, 1 / price_b
    total = raw_a + raw_b
    if total <= 0:
        return None
    return round(raw_a / total, 4), round(raw_b / total, 4)


def _risk_for_probability(probability: float) -> RiskLevel:
    if probability >= 0.62:
        return RiskLevel.LOW
    if probability >= 0.48:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def ideal_candidate_for_match(prediction: MatchPrediction, dataset: MatchRawDataset) -> PickCandidate | None:
    """The single safest pick for this fixture, across several real markets.

    Requires the 1X2 market to be open at all (spec: never offer an
    "ideal" pick on a fixture with no real market to ground it in), then
    compares candidates from 1X2, Double Chance, Over/Under 2.5 and BTTS
    and returns whichever one is genuinely the most backed -- not always
    the plain match-result favorite.
    """
    mc = prediction.market_comparison
    if not mc.market_available or mc.market_implied is None:
        return None

    home = prediction.home_team.team_name
    away = prediction.away_team.team_name
    fixture = dataset.fixture

    candidates: list[tuple[float, str, str, float | None]] = []  # (prob, market_label, selection, market_prob)

    ox = prediction.one_x_two
    favorite_prob = max(ox.home_win, ox.draw, ox.away_win)
    favorite_label = home if ox.home_win == favorite_prob else away if ox.away_win == favorite_prob else "Beraberlik"
    market_fav_prob = {
        home: mc.market_implied.home_win, "Beraberlik": mc.market_implied.draw, away: mc.market_implied.away_win,
    }.get(favorite_label)
    candidates.append((favorite_prob, "Maç Sonucu (1X2)", favorite_label, market_fav_prob))

    dc = prediction.double_chance
    dc_options = [
        (dc.home_or_draw, f"{home} veya Beraberlik (1X)"),
        (dc.draw_or_away, f"Beraberlik veya {away} (X2)"),
        (dc.home_or_away, f"{home} veya {away} (12)"),
    ]
    best_dc_prob, best_dc_label = max(dc_options, key=lambda item: item[0])
    candidates.append((best_dc_prob, "Çifte Şans", best_dc_label, None))

    main_ou = next((line for line in prediction.over_under if abs(line.line - 2.5) < 1e-6), None)
    if main_ou is not None:
        if main_ou.over >= main_ou.under:
            candidates.append((main_ou.over, "Alt/Üst Gol", "Üst 2.5", None))
        else:
            candidates.append((main_ou.under, "Alt/Üst Gol", "Alt 2.5", None))

    btts = prediction.btts_yes_probability
    if btts >= 1 - btts:
        candidates.append((btts, "Karşılıklı Gol (KG)", "Var", None))
    else:
        candidates.append((1 - btts, "Karşılıklı Gol (KG)", "Yok", None))

    best_prob, best_market, best_selection, best_market_prob = max(candidates, key=lambda item: item[0])
    if best_prob < IDEAL_MIN_PROBABILITY:
        return None

    divergence = round(best_prob - best_market_prob, 4) if best_market_prob is not None else None
    rationale = (
        f"Model bu seçime %{best_prob*100:.1f} olasılık veriyor "
        f"(güven: %{prediction.confidence.confidence*100:.1f}, veri kalitesi: {prediction.data_quality.value})."
    )

    return PickCandidate(
        match_id=fixture.match_id, home_team=home, away_team=away,
        league_name=fixture.league_name, kickoff=fixture.kickoff,
        market=best_market, selection=best_selection,
        model_probability=round(best_prob, 4), market_probability=best_market_prob, divergence=divergence,
        confidence=prediction.confidence.confidence, risk=_risk_for_probability(best_prob),
        data_quality=prediction.data_quality, rationale=rationale,
    )


def surprise_candidates_for_match(prediction: MatchPrediction, dataset: MatchRawDataset) -> list[PickCandidate]:
    """Every real-market model/market divergence for this fixture.

    İY/MS is intentionally excluded -- see module docstring. Only markets
    that are ACTUALLY open for this fixture (found in `dataset.odds_markets`)
    are considered; nothing here is inferred from a market that doesn't exist.
    """
    markets = dataset.odds_markets
    fixture = dataset.fixture
    home = prediction.home_team.team_name
    away = prediction.away_team.team_name
    out: list[PickCandidate] = []

    def add(market_label: str, selection: str, model_prob: float, market_prob: float) -> None:
        divergence = round(model_prob - market_prob, 4)
        # A surprise means the model favors a side MORE than the market does
        # -- the opposite direction (model is more pessimistic) is not an
        # upset pick, it's just caution, so it is not offered as a "surprise".
        if divergence < SURPRISE_MIN_DIVERGENCE:
            return
        out.append(PickCandidate(
            match_id=fixture.match_id, home_team=home, away_team=away,
            league_name=fixture.league_name, kickoff=fixture.kickoff,
            market=market_label, selection=selection,
            model_probability=round(model_prob, 4), market_probability=round(market_prob, 4), divergence=divergence,
            confidence=prediction.confidence.confidence, risk=_risk_for_probability(model_prob),
            data_quality=prediction.data_quality,
            rationale=(
                f"Model bu seçime %{model_prob*100:.1f} olasılık veriyor, piyasa ise %{market_prob*100:.1f} "
                f"görüyor -- %{divergence*100:.1f} puanlık gerçek bir piyasa/model ayrışması."
            ),
        ))

    # 1X2: only the market's own FAVORITE can be "surprised against" -- i.e.
    # the model must prefer a DIFFERENT side than the one the market favors.
    mc = prediction.market_comparison
    if mc.market_available and mc.market_implied is not None:
        mi = mc.market_implied
        ox = prediction.one_x_two
        market_fav = max((mi.home_win, home), (mi.draw, "Beraberlik"), (mi.away_win, away))[1]
        for model_prob, market_prob, label in (
            (ox.home_win, mi.home_win, home), (ox.draw, mi.draw, "Beraberlik"), (ox.away_win, mi.away_win, away),
        ):
            if label != market_fav:
                add("Maç Sonucu (1X2)", label, model_prob, market_prob)

    ou_market = _find_market(markets, "alt/üst gol", "üst/alt gol", "goal line", "over/under")
    if ou_market is not None:
        for line in prediction.over_under:
            over_price = _price(ou_market, f"Üst {line.line:g}")
            under_price = _price(ou_market, f"Alt {line.line:g}")
            if over_price is None or under_price is None:
                continue
            implied = _implied_two_way(over_price, under_price)
            if implied is None:
                continue
            market_over, market_under = implied
            add("Alt/Üst Gol", f"Üst {line.line:g}", line.over, market_over)
            add("Alt/Üst Gol", f"Alt {line.line:g}", line.under, market_under)

    btts_market = _find_market(markets, "karşılıklı gol", "kg var", "btts")
    if btts_market is not None:
        yes_price, no_price = _price(btts_market, "Var"), _price(btts_market, "Yok")
        if yes_price is not None and no_price is not None:
            implied = _implied_two_way(yes_price, no_price)
            if implied is not None:
                market_yes, market_no = implied
                add("Karşılıklı Gol (KG)", "Var", prediction.btts_yes_probability, market_yes)
                add("Karşılıklı Gol (KG)", "Yok", 1 - prediction.btts_yes_probability, market_no)

    handicap_market = _find_market(markets, "asya handikap", "asian handicap")
    if handicap_market is not None:
        for ah in prediction.asian_handicap:
            home_price = _price(handicap_market, f"Ev {ah.line:g}")
            away_price = _price(handicap_market, f"Dep {ah.line:g}")
            if home_price is None or away_price is None:
                continue
            implied = _implied_two_way(home_price, away_price)
            if implied is None:
                continue
            market_home, market_away = implied
            add("Asya Handikap", f"{home} {ah.line:+g}", ah.home_cover, market_home)
            add("Asya Handikap", f"{away} {-ah.line:+g}", ah.away_cover, market_away)

    return out
