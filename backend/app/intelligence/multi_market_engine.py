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

import os

from app.providers.models import MatchRawDataset, OddsMarket
from app.schemas.common import DataQuality, RiskLevel
from app.schemas.picks import PickCandidate
from app.schemas.prediction import MatchPrediction

# Below this, a pick is not "ideal" (safe) -- it has no real statistical
# backing to be presented as a low-risk choice.
IDEAL_MIN_PROBABILITY = float(os.getenv("IDEAL_MIN_PROBABILITY", "0.60"))
# "İdeal" seçim gerçekten oynanabilir olmalı: oranı bu değerin altındaysa (ör. 1.12 favori ya da
# onun "çifte şansı") hiçbir kullanıcı için anlamlı değildir; seçilmez.
# Gerçek sonuçlarla ölçüm (model_log, ~150 maç): modelin en yüksek 1X2 olasılığı %45'in altındaysa isabet
# %17 (net favori yok); "Alt 2.5" ve "KG Yok" tahminleri ise vaat ettiklerinden çok daha az tutuyor.
# Bu yüzden: zayıf favoride 1X2 önerilmez, Alt/KG-Yok olasılıkları yarıya doğru çekilir (kalibrasyon).
IDEAL_MIN_FAVORITE_PROB = float(os.getenv("IDEAL_MIN_FAVORITE_PROB", "0.60"))
UNDER_BTTSNO_SHRINK = float(os.getenv("UNDER_BTTSNO_SHRINK", "0.5"))
IDEAL_MIN_ODD = float(os.getenv("IDEAL_MIN_ODD", "1.40"))
# Çifte şans yalnızca oranı bu değer ve üstündeyse önerilir (düşük oranlı çifte şans anlamsızdır).
IDEAL_DC_MIN_ODD = float(os.getenv("IDEAL_DC_MIN_ODD", "2.00"))
# Çifte şans yalnızca dengeli/yüksek oranlı maçlarda önerilir: çifte şansın iki sonucundan
# EN DÜŞÜK oranlısı bile bu değerin üstündeyse (favori net değilse) seçilebilir.
DC_MIN_SIDE_ODD = float(os.getenv("IDEAL_DC_MIN_SIDE_ODD", "2.00"))

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


def _market_price(dataset: MatchRawDataset, name_part: str, label: str) -> float | None:
    for m in dataset.odds_markets:
        if name_part in (m.market_name or ""):
            for sel in m.selections:
                if sel.label == label and sel.price and sel.price > 1.0:
                    return float(sel.price)
    return None


def _ideal_odd(dataset: MatchRawDataset, market: str, selection: str, home: str, away: str) -> float | None:
    """Seçimin gerçek bahis oranı; çifte şans oranı 1X2 oranlarından türetilir. Yoksa None."""
    p1 = _market_price(dataset, "Maç Sonucu 1X2", "1")
    px = _market_price(dataset, "Maç Sonucu 1X2", "X")
    p2 = _market_price(dataset, "Maç Sonucu 1X2", "2")
    if market.startswith("Maç Sonucu"):
        label = "X" if selection == "Beraberlik" else "1" if selection == home else "2"
        return {"1": p1, "X": px, "2": p2}[label]
    if market == "Çifte Şans":
        if not (p1 and px and p2):
            return None
        pair = (p1, px) if selection.endswith("(1X)") else (px, p2) if selection.endswith("(X2)") else (p1, p2)
        return round(1.0 / (1.0 / pair[0] + 1.0 / pair[1]), 2)
    if market == "Alt/Üst Gol":
        return _market_price(dataset, "Alt/Üst Gol (", selection)
    if market.startswith("Karşılıklı Gol"):
        return _market_price(dataset, "Karşılıklı Gol", selection)
    return None


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
    if favorite_prob >= IDEAL_MIN_FAVORITE_PROB:
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
            candidates.append((0.5 + UNDER_BTTSNO_SHRINK * (main_ou.under - 0.5), "Alt/Üst Gol", "Alt 2.5", None))

    btts = prediction.btts_yes_probability
    if btts >= 1 - btts:
        candidates.append((btts, "Karşılıklı Gol (KG)", "Var", None))
    else:
        candidates.append((0.5 + UNDER_BTTSNO_SHRINK * ((1 - btts) - 0.5), "Karşılıklı Gol (KG)", "Yok", None))

    # Oranı gerçekten oynanabilir (>= IDEAL_MIN_ODD) olmayan seçenekler elenir.
    playable = []
    for cand in candidates:
        odd = _ideal_odd(dataset, cand[1], cand[2], home, away)
        if cand[1] == "Çifte Şans":
            sides = [_market_price(dataset, "Maç Sonucu 1X2", k) for k in
                     (("1", "X") if cand[2].endswith("(1X)") else ("X", "2") if cand[2].endswith("(X2)") else ("1", "2"))]
            if not all(sides) or min(sides) < DC_MIN_SIDE_ODD:
                continue
        floor = IDEAL_DC_MIN_ODD if cand[1] == "Çifte Şans" else IDEAL_MIN_ODD
        if odd is not None and odd >= floor:
            playable.append((cand, odd))
    if not playable:
        return None
    (best_prob, best_market, best_selection, best_market_prob), best_odd = max(playable, key=lambda item: item[0][0])
    if best_prob < IDEAL_MIN_PROBABILITY:
        return None

    divergence = round(best_prob - best_market_prob, 4) if best_market_prob is not None else None
    rationale = (
        f"Model bu seçime %{best_prob*100:.1f} olasılık veriyor, oran {best_odd:.2f} "
        f"(güven: %{prediction.confidence.confidence*100:.1f}, veri kalitesi: {prediction.data_quality.value}). "
        + (prediction.analysis_rationale or "")
    ).strip()

    return PickCandidate(
        match_id=fixture.match_id, home_team=home, away_team=away,
        league_name=fixture.league_name, kickoff=fixture.kickoff,
        market=best_market, selection=best_selection,
        model_probability=round(best_prob, 4), odd=round(best_odd, 2), market_probability=best_market_prob, divergence=divergence,
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
                f"görüyor -- %{divergence*100:.1f} puanlık gerçek bir piyasa/model ayrışması. "
                + (prediction.analysis_rationale or "")
            ).strip(),
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
