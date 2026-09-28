"""
SÜRPRİZ VERİ - Result Mapper

5DollarFootballAPI fixture sonucunu
Sürpriz Veri MatchOutcome modeline dönüştürür.

Bu modül yalnızca gerçekleşmiş maç sonucunu işler.

Odds verisine müdahale etmez.
Opening / Closing ayrımına müdahale etmez.
Mevcut Bay Tahmin sistemine dokunmaz.
"""

from typing import Any, Dict, Optional

from .models import MatchOutcome


def _to_int(
    value: Any,
) -> Optional[int]:
    """Değeri güvenli şekilde integer'a çevirir."""

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _result_from_goals(
    home: Optional[int],
    away: Optional[int],
) -> Optional[str]:
    """
    Gol değerlerinden 1/X/2 sonucu üretir.
    """

    if home is None or away is None:
        return None

    if home > away:
        return "1"

    if home < away:
        return "2"

    return "X"


def _score_text(
    home: Optional[int],
    away: Optional[int],
) -> Optional[str]:
    """Gol değerlerinden skor metni oluşturur."""

    if home is None or away is None:
        return None

    return f"{home}-{away}"


def _over_under_result(
    total_goals: Optional[int],
    line: float = 2.5,
) -> Optional[str]:
    """
    Gerçekleşen toplam gole göre
    temel Over/Under sonucu üretir.

    Örneğin 2.5 için:

        3+ gol -> Over 2.5
        0-2 gol -> Under 2.5
    """

    if total_goals is None:
        return None

    if total_goals > line:
        return f"Over {line:g}"

    return f"Under {line:g}"


def _btts_result(
    home: Optional[int],
    away: Optional[int],
) -> Optional[str]:
    """BTTS gerçekleşip gerçekleşmediğini belirler."""

    if home is None or away is None:
        return None

    if home > 0 and away > 0:
        return "Yes"

    return "No"


def map_fixture_result(
    fixture: Dict[str, Any],
) -> MatchOutcome:
    """
    5DollarFootballAPI fixture sonucunu MatchOutcome'a dönüştürür.

    Beklenen yapı:

        goals:
            home
            away
            half_home
            half_away

    HT/FT:

        first_half_result + full_time_result

    üzerinden oluşturulur.
    """

    if not isinstance(fixture, dict):
        raise ValueError(
            "Fixture verisi dict olmalıdır."
        )

    goals = fixture.get(
        "goals",
        {},
    )

    if not isinstance(goals, dict):
        goals = {}

    # ---------------------------------------------------------
    # TAM MAÇ GOLLERİ
    # ---------------------------------------------------------

    full_home = _to_int(
        goals.get("home")
    )

    full_away = _to_int(
        goals.get("away")
    )

    # ---------------------------------------------------------
    # İLK YARI GOLLERİ
    # ---------------------------------------------------------

    half_home = _to_int(
        goals.get("half_home")
    )

    half_away = _to_int(
        goals.get("half_away")
    )

    # ---------------------------------------------------------
    # İKİNCİ YARI GOLLERİ
    # ---------------------------------------------------------

    second_home = None
    second_away = None

    if (
        full_home is not None
        and half_home is not None
    ):
        second_home = full_home - half_home

    if (
        full_away is not None
        and half_away is not None
    ):
        second_away = full_away - half_away

    # ---------------------------------------------------------
    # SONUÇLAR
    # ---------------------------------------------------------

    first_half_result = _result_from_goals(
        half_home,
        half_away,
    )

    second_half_result = _result_from_goals(
        second_home,
        second_away,
    )

    full_time_result = _result_from_goals(
        full_home,
        full_away,
    )

    # ---------------------------------------------------------
    # SKORLAR
    # ---------------------------------------------------------

    first_half_score = _score_text(
        half_home,
        half_away,
    )

    second_half_score = _score_text(
        second_home,
        second_away,
    )

    full_time_score = _score_text(
        full_home,
        full_away,
    )

    # ---------------------------------------------------------
    # HT / FT
    # ---------------------------------------------------------

    ht_ft = None

    if (
        first_half_result is not None
        and full_time_result is not None
    ):
        ht_ft = (
            f"{first_half_result}/"
            f"{full_time_result}"
        )

    # ---------------------------------------------------------
    # OVER / UNDER
    # ---------------------------------------------------------

    total_goals = None

    if (
        full_home is not None
        and full_away is not None
    ):
        total_goals = (
            full_home + full_away
        )

    over_under = _over_under_result(
        total_goals,
        line=2.5,
    )

    # ---------------------------------------------------------
    # BTTS
    # ---------------------------------------------------------

    btts = _btts_result(
        full_home,
        full_away,
    )

    # ---------------------------------------------------------
    # DİĞER GERÇEKLEŞEN SONUÇLAR
    # ---------------------------------------------------------

    other_markets = {
        "total_goals": total_goals,
        "first_half_goals": (
            half_home + half_away
            if (
                half_home is not None
                and half_away is not None
            )
            else None
        ),
        "second_half_goals": (
            second_home + second_away
            if (
                second_home is not None
                and second_away is not None
            )
            else None
        ),
    }

    return MatchOutcome(
        first_half_result=first_half_result,

        second_half_result=second_half_result,

        full_time_result=full_time_result,

        first_half_score=first_half_score,

        second_half_score=second_half_score,

        full_time_score=full_time_score,

        ht_ft=ht_ft,

        over_under=over_under,

        btts=btts,

        other_markets=other_markets,
    )
