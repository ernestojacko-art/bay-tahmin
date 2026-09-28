"""
SÜRPRİZ VERİ - Data Mapper

5DollarFootballAPI'den gelen ham veriyi
Sürpriz Veri motorunun MatchRecord modeline dönüştürür.

Bu katman:
- Opening odds
- Closing odds
- Odds movement
- Market profile
- Kickoff time

verilerini birbirinden ayrı tutar.

API response yapısı değişirse yalnızca bu katmanın
güncellenmesi hedeflenir.
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
    """
    Bir sözlükte verilen anahtarlardan ilk bulunan değeri döndürür.
    """

    for key in keys:
        if key in data and data[key] is not None:
            return data[key]

    return None


def _float_or_none(
    value: Any,
) -> Optional[float]:
    """
    Değeri güvenli şekilde float'a dönüştürür.
    """

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_team_name(
    team_data: Any,
) -> Optional[str]:
    """
    Farklı olası takım response yapılarını destekler.
    """

    if isinstance(team_data, str):
        return team_data

    if not isinstance(team_data, dict):
        return None

    value = _first_value(
        team_data,
        "name",
        "team_name",
        "short_name",
        "display_name",
    )

    if value is None:
        return None

    return str(value)


def _extract_odds_from_mapping(
    data: Dict[str, Any],
) -> Odds:
    """
    Bir odds sözlüğünden home/draw/away değerlerini çıkarır.
    """

    home = _first_value(
        data,
        "home",
        "1",
        "home_odds",
    )

    draw = _first_value(
        data,
        "draw",
        "x",
        "X",
        "draw_odds",
    )

    away = _first_value(
        data,
        "away",
        "2",
        "away_odds",
    )

    return Odds(
        home=_float_or_none(home),
        draw=_float_or_none(draw),
        away=_float_or_none(away),
    )


def _extract_odds(
    data: Dict[str, Any],
    state: str,
) -> Odds:
    """
    Opening veya closing odds bilgisini bulmaya çalışır.

    Önce state'e ait nested yapı aranır.
    Bulunamazsa doğrudan response içindeki alanlar kontrol edilir.
    """

    possible_keys = {
        "opening": (
            "opening",
            "opening_odds",
            "open",
            "open_odds",
        ),
        "closing": (
            "closing",
            "closing_odds",
            "close",
            "close_odds",
        ),
    }

    nested = _first_value(
        data,
        *possible_keys[state],
    )

    if isinstance(nested, dict):
        return _extract_odds_from_mapping(
            nested
        )

    return Odds()


def calculate_odds_movement(
    opening: Odds,
    closing: Odds,
) -> OddsMovement:
    """
    Opening → Closing hareketini hesaplar.

    Hareket = Closing - Opening
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


def _extract_market_names(
    data: Dict[str, Any],
) -> List[str]:
    """
    API response içindeki market listesini mümkün olduğunca
    genel biçimde çıkarır.

    Henüz belirli bir bookmaker veya market yapısına
    zorunlu bağımlılık oluşturmaz.
    """

    raw_markets = _first_value(
        data,
        "markets",
        "available_markets",
        "market",
    )

    if raw_markets is None:
        return []

    if isinstance(raw_markets, list):
        result: List[str] = []

        for item in raw_markets:
            if isinstance(item, str):
                result.append(item)

            elif isinstance(item, dict):
                name = _first_value(
                    item,
                    "name",
                    "market",
                    "label",
                    "key",
                )

                if name is not None:
                    result.append(str(name))

        return result

    if isinstance(raw_markets, dict):
        return [
            str(key)
            for key in raw_markets.keys()
        ]

    return [str(raw_markets)]


def build_market_profile(
    data: Dict[str, Any],
) -> MarketProfile:
    """
    Ham response'dan MarketProfile oluşturur.
    """

    markets = _extract_market_names(data)

    normalized = [
        market.lower().replace(" ", "_")
        for market in markets
    ]

    return MarketProfile(
        available_markets=markets,
        market_count=len(markets),
        ht_ft_available=any(
            "ht" in market
            and "ft" in market
            for market in normalized
        ),
        first_half_available=any(
            "first_half" in market
            or "1st_half" in market
            or "half_time" in market
            for market in normalized
        ),
        second_half_available=any(
            "second_half" in market
            or "2nd_half" in market
            for market in normalized
        ),
        over_under_available=any(
            "over" in market
            or "under" in market
            for market in normalized
        ),
        btts_available=any(
            "btts" in market
            or "both_teams" in market
            for market in normalized
        ),
        all_markets_open=bool(markets),
    )


def map_fixture_to_match_record(
    fixture: Dict[str, Any],
    odds_data: Optional[Dict[str, Any]] = None,
) -> MatchRecord:
    """
    API fixture + odds verisini MatchRecord'a dönüştürür.

    Bu fonksiyon nihai analiz yapmaz.
    Yalnızca veri standardizasyonu yapar.
    """

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

    home_team = _first_value(
        fixture,
        "home_team",
        "home",
        "home_name",
    )

    away_team = _first_value(
        fixture,
        "away_team",
        "away",
        "away_name",
    )

    if isinstance(home_team, dict):
        home_team = _extract_team_name(
            home_team
        )

    if isinstance(away_team, dict):
        away_team = _extract_team_name(
            away_team
        )

    kickoff_time = _first_value(
        fixture,
        "kickoff_time",
        "starting_at",
        "start_time",
        "date",
    )

    competition = _first_value(
        fixture,
        "competition",
        "league",
        "tournament",
    )

    combined = dict(fixture)

    if isinstance(odds_data, dict):
        combined.update(
            {
                "odds": odds_data,
            }
        )

    opening_source = (
        odds_data
        if isinstance(odds_data, dict)
        else fixture
    )

    opening = _extract_odds(
        opening_source,
        "opening",
    )

    closing = _extract_odds(
        opening_source,
        "closing",
    )

    movement = calculate_odds_movement(
        opening,
        closing,
    )

    market_profile = build_market_profile(
        odds_data
        if isinstance(odds_data, dict)
        else fixture
    )

    return MatchRecord(
        match_id=str(fixture_id),
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
            "odds": odds_data or {},
        },
    )
