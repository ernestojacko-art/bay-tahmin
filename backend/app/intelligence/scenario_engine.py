"""
Scenario Engine (spec section 7 / 8).

Turns raw probabilities into named, human-readable match scenarios:
favorite, balanced, and upset -- each carrying its own expected-goals
projection and tempo read, rather than collapsing everything into a
single predicted score.
"""
from __future__ import annotations

from app.schemas.prediction import ExpectedGoals, MatchScenario, OneXTwoProbabilities


def _tempo_label(total_xg: float) -> str:
    if total_xg < 2.1:
        return "düşük gollü"
    if total_xg > 3.0:
        return "yüksek gollü"
    return "dengeli"


def build_scenarios(
    one_x_two: OneXTwoProbabilities,
    expected_goals: ExpectedGoals,
    home_team_name: str,
    away_team_name: str,
    market_implied: OneXTwoProbabilities | None = None,
) -> list[MatchScenario]:
    scenarios: list[MatchScenario] = []
    tempo = _tempo_label(expected_goals.total_xg)

    outcomes = [
        ("home_win", one_x_two.home_win, home_team_name, away_team_name, True),
        ("draw", one_x_two.draw, home_team_name, away_team_name, None),
        ("away_win", one_x_two.away_win, away_team_name, home_team_name, False),
    ]
    outcomes_sorted = sorted(outcomes, key=lambda o: o[1], reverse=True)

    favorite_key, favorite_prob, fav_team, other_team, fav_is_home = outcomes_sorted[0]

    # KRİTİK: "favori" burada her zaman MODELİN kendi görüşüdür -- piyasanın
    # favorisiyle KARIŞTIRILMAMALI. Bir maç zaten sürpriz adayıysa (model
    # piyasadan ayrışıyorsa), modelin favorisi piyasanın gördüğü favoriden
    # FARKLI olabilir. Bu durumda bunu açıkça belirtiyoruz; aksi halde
    # kullanıcı "favori" etiketini piyasanın favorisiyle karıştırır.
    market_note = ""
    if market_implied is not None:
        market_outcomes = [
            ("home_win", market_implied.home_win, home_team_name),
            ("draw", market_implied.draw, "Beraberlik"),
            ("away_win", market_implied.away_win, away_team_name),
        ]
        market_favorite_key, _, market_favorite_team = max(market_outcomes, key=lambda o: o[1])
        if market_favorite_key != favorite_key:
            market_note = (
                f" (Not: Bahis piyasası burada {market_favorite_team}'ı favori görüyor -- "
                "bu, modelin görüşü ile piyasanın görüşü arasında bir ayrışma; sürpriz "
                "potansiyelinin kaynağı da tam olarak bu.)"
            )

    if favorite_key == "draw":
        favorite_desc = (
            f"İstatistiksel modele göre en olası tek sonuç, {home_team_name} ile "
            f"{away_team_name} arasında bir beraberlik.{market_note}"
        )
        favorite_label = "Beraberlik (model favorisi)"
    else:
        favorite_desc = f"{fav_team}, MODELİN bu maçı kazanma favorisi.{market_note}"
        favorite_label = f"{fav_team} (model favorisi)"
    scenarios.append(
        MatchScenario(
            scenario_type="favorite",
            label=favorite_label,
            description=favorite_desc,
            probability=favorite_prob,
            expected_goals=expected_goals,
            tempo=tempo,
        )
    )

    # Balanced scenario: whichever of the two non-favorite outcomes is closest
    # in probability to the favorite (captures "could easily go either way").
    remaining = outcomes_sorted[1:]
    remaining_sorted_by_closeness = sorted(remaining, key=lambda o: abs(o[1] - favorite_prob))
    balanced_key, balanced_prob, b_team, b_other, _ = remaining_sorted_by_closeness[0]
    if balanced_key == "draw":
        balanced_desc = "Beraberlik, canlı ve rekabetçi bir alternatif sonuç."
        balanced_label = "Beraberlik canlı"
    else:
        balanced_desc = f"{b_team}'ın kazanması, gerçekçi ve rekabetçi bir alternatif."
        balanced_label = f"{b_team} rekabetçi"
    scenarios.append(
        MatchScenario(
            scenario_type="balanced",
            label=balanced_label,
            description=balanced_desc,
            probability=balanced_prob,
            expected_goals=expected_goals,
            tempo=tempo,
        )
    )

    # Upset scenario: the least-likely outcome, framed explicitly as a surprise.
    upset_key, upset_prob, u_team, u_other, _ = outcomes_sorted[-1]
    if upset_key == "draw":
        upset_desc = "Buradaki düşük olasılıklı sürpriz senaryo, form durumuna rağmen bir beraberlik."
        upset_label = "Sürpriz beraberlik"
    else:
        upset_desc = f"Sürpriz senaryo, {u_team}'ın favoriye rağmen galibiyeti."
        upset_label = f"{u_team} sürprizi"
    scenarios.append(
        MatchScenario(
            scenario_type="upset",
            label=upset_label,
            description=upset_desc,
            probability=upset_prob,
            expected_goals=expected_goals,
            tempo=tempo,
        )
    )

    return scenarios
