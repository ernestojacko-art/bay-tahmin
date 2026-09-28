"""
SÜRPRİZ VERİ - Data Mapper

5DollarFootballAPI'den gelen gerçek fixture ve odds verisini
Sürpriz Veri motorunun MatchRecord modeline dönüştürür.

Önemli:
- Opening odds ayrı tutulur.
- Closing odds ayrı tutulur.
- Opening -> Closing movement ayrıca hesaplanır.
- Market profili ayrıca tutulur.
- Mevcut Bay Tahmin sistemine dokunmaz.
"""

from typing import Any, Dict, List, Optional

from .models import (
    MatchRecord,
    MarketProfile,
    Odds,
    OddsMovement,
)


def _first_value(
    data: Dict[str, Any],
    *keys: str,
) -> Any:
    """Verilen anahtarlar içinden bulunan ilk değeri döndürür."""

    for key in keys:
        if key in data and data[key] is not None:
            return data[key]

    return None


def _float_or_none(
    value: Any,
) -> Optional[float]:
    """Değeri güvenli şekilde float'a çevirir."""

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_team_name(
    team_data: Any,
) -> Optional[str]:
    """5DollarFootballAPI team objesinden takım adını çıkarır."""

    if isinstance(team_data, str):
        return team_data

    if not isinstance(team_data, dict):
        return None

    value = _first_value(
        team_data,
        "name",
        "short_name",
        "display_name",
    )

    if value is None:
        return None

    return str(value)


def _extract_odds_stage(
    odds_data: Dict[str, Any],
    stage: str,
) -> Odds:
    """
    1X2 odds içinden opening veya closing değerini alır.

    Beklenen yapı:

    odds:
      1x2:
        opening:
          home
          draw
          away
        closing:
          home
          draw
          away
    """

    if not isinstance(odds_data, dict):
        return Odds()

    one_x_two = odds_data.get("1x2")

    if not isinstance(one_x_two, dict):
        return Odds()

    stage_data = one_x_two.get(stage)

    if not isinstance(stage_data, dict):
        return Odds()

    return Odds(
        home=_float_or_none(
            stage_data.get("home")
        ),
        draw=_float_or_none(
            stage_data.get("draw")
        ),
        away=_float_or_none(
            stage_data.get("away")
        ),
    )


def calculate_odds_movement(
    opening: Odds,
    closing: Odds,
) -> OddsMovement:
    """
    Opening -> Closing hareketini hesaplar.

    Formül:

        movement = closing - opening

    Örnek:

        opening home = 2.10
        closing home = 1.85

        movement = -0.25
    """

    home = None
    draw = None
    away = None

    if (
        opening.home is not None
        and closing.home is not None
    ):
        home = closing.home - opening.home

    if (
        opening.draw is not None
        and closing.draw is not None
    ):
        draw = closing.draw - opening.draw

    if (
        opening.away is not None
        and closing.away is not None
    ):
        away = closing.away - opening.away

    return OddsMovement(
        home=home,
        draw=draw,
        away=away,
    )


def _market_name_list(
    odds_data: Dict[str, Any],
) -> List[str]:
    """
    5DollarFootballAPI odds objesindeki market isimlerini çıkarır.

    Örnek marketler:

    1x2
    asian_handicap
    goal_line
    corner_line
    corner_asian
    card_line
    card_asian
    btts
    1x2_half
    asian_half
    goalline_half
    corner_half
    goal_line_fixed
    """

    if not isinstance(odds_data, dict):
        return []

    return [
        str(key)
        for key, value in odds_data.items()
        if isinstance(value, dict)
    ]


def _normalize_market_name(
    market: str,
) -> str:
    return (
        market
        .lower()
        .strip()
        .replace("-", "_")
        .replace(" ", "_")
    )


def build_market_profile(
    odds_data: Optional[Dict[str, Any]],
) -> MarketProfile:
    """
    Gerçek API odds yapısından market profilini oluşturur.
    """

    if not isinstance(odds_data, dict):
        return MarketProfile(
            available_markets=[],
            market_count=0,
            ht_ft_available=False,
            first_half_available=False,
            second_half_available=False,
            over_under_available=False,
            btts_available=False,
            all_markets_open=False,
        )

    markets = _market_name_list(
        odds_data
    )

    normalized = [
        _normalize_market_name(market)
        for market in markets
    ]

    return MarketProfile(
        available_markets=markets,
        market_count=len(markets),

        # API'de 1X2 half-time marketi ayrı tutulur.
        ht_ft_available=(
            "1x2" in normalized
            or "1x2_half" in normalized
        ),

        first_half_available=any(
            market in {
                "1x2_half",
                "asian_half",
                "goalline_half",
                "corner_half",
            }
            for market in normalized
        ),

        second_half_available=False,

        over_under_available=any(
            market in {
                "goal_line",
                "goal_line_fixed",
                "goalline_half",
            }
            for market in normalized
        ),

        btts_available=(
            "btts" in normalized
        ),

        all_markets_open=bool(markets),
    )


def extract_odds_block(
    fixture: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Fixture içindeki odds bloğunu döndürür.

    /fixtures ve /fixtures/{id} cevaplarında
    odds doğrudan fixture altında bulunabilir.
    """

    odds = fixture.get("odds")

    if isinstance(odds, dict):
        return odds

    return {}


def map_fixture_to_match_record(
    fixture: Dict[str, Any],
    odds_data: Optional[Dict[str, Any]] = None,
) -> MatchRecord:
    """
    5DollarFootballAPI fixture verisini MatchRecord'a dönüştürür.

    odds_data verilirse öncelikli olarak onu kullanır.
    Verilmezse fixture içindeki odds bloğunu kullanır.
    """

    if not isinstance(fixture, dict):
        raise ValueError(
            "Fixture verisi dict olmalıdır."
        )

    fixture_id = _first_value(
        fixture,
        "id",
        "fixture_id",
        "match_id",
    )

    if fixture_id is None:
        raise ValueError(
            "Fixture ID bulunamadı."
        )

    # ---------------------------------------------------------
    # TAKIMLAR
    # ---------------------------------------------------------

    teams = fixture.get(
        "teams",
        {},
    )

    home_team = None
    away_team = None

    if isinstance(teams, dict):
        home_team = _extract_team_name(
            teams.get("home")
        )

        away_team = _extract_team_name(
            teams.get("away")
        )

    # Fallback
    if home_team is None:
        home_team = _extract_team_name(
            fixture.get("home_team")
        )

    if away_team is None:
        away_team = _extract_team_name(
            fixture.get("away_team")
        )

    # ---------------------------------------------------------
    # KICKOFF
    # ---------------------------------------------------------

    kickoff_time = _first_value(
        fixture,
        "kickoff_utc",
        "kickoff_ts",
        "starting_at",
        "start_time",
    )

    # ---------------------------------------------------------
    # LİG
    # ---------------------------------------------------------

    league = fixture.get(
        "league",
        {}
    )

    competition = None

    if isinstance(league, dict):
        competition = league.get(
            "name"
        )

    if competition is None:
        competition = _first_value(
            fixture,
            "competition",
            "league_name",
        )

    # ---------------------------------------------------------
    # ODDS
    # ---------------------------------------------------------

    fixture_odds = extract_odds_block(
        fixture
    )

    external_odds = (
        odds_data
        if isinstance(odds_data, dict)
        else {}
    )

    # Eğer odds_data doğrudan response içindeki
    # {"data": {"bookmakers": [...]}} şeklindeyse
    # bookmaker odds bloğunu ayrıca çöz.
    if "data" in external_odds:
        data_block = external_odds.get(
            "data"
        )

        if isinstance(data_block, dict):
            bookmakers = data_block.get(
                "bookmakers"
            )

            if (
                isinstance(bookmakers, list)
                and bookmakers
                and isinstance(
                    bookmakers[0],
                    dict,
                )
            ):
                bookmaker = bookmakers[0]

                bookmaker_odds = bookmaker.get(
                    "odds"
                )

                if isinstance(
                    bookmaker_odds,
                    dict,
                ):
                    external_odds = (
                        bookmaker_odds
                    )

    # Doğrudan odds bloğu verilmişse onu kullan.
    if external_odds:
        source_odds = external_odds
    else:
        source_odds = fixture_odds

    opening = _extract_odds_stage(
        source_odds,
        "opening",
    )

    closing = _extract_odds_stage(
        source_odds,
        "closing",
    )

    movement = calculate_odds_movement(
        opening,
        closing,
    )

    # ---------------------------------------------------------
    # MARKET PROFİLİ
    # ---------------------------------------------------------

    market_profile = build_market_profile(
        source_odds
    )

    # ---------------------------------------------------------
    # MATCH RECORD
    # ---------------------------------------------------------

    return MatchRecord(
        match_id=str(
            fixture_id
        ),

        home_team=str(
            home_team
            or "Bilinmeyen Ev Sahibi"
        ),

        away_team=str(
            away_team
            or "Bilinmeyen Deplasman"
        ),

        kickoff_time=(
            str(kickoff_time)
            if kickoff_time is not None
            else None
        ),

        competition=(
            str(competition)
            if competition is not None
            else None
        ),

        opening_odds=opening,

        closing_odds=closing,

        odds_movement=movement,

        market_profile=market_profile,

        metadata={
            "fixture": fixture,
            "odds": source_odds,
        },
    )
