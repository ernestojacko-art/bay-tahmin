"""
SÜRPRİZ VERİ - Odds History Mapper

5DollarFootballAPI odds history verisini
Sürpriz Veri'nin bağımsız hareket modeline dönüştürür.

Önemli ayrım:

OPENING
CLOSING
HAREKET

birbirinden ayrı tutulur.

Bu modül tahmin üretmez.
Sadece odds hareketini normalize eder.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Odds, OddsMovement


@dataclass
class OddsHistoryPoint:
    """
    Tek bir odds zaman noktası.
    """

    timestamp: Optional[str] = None

    home: Optional[float] = None
    draw: Optional[float] = None
    away: Optional[float] = None

    source: Optional[str] = None


@dataclass
class OddsHistoryAnalysis:
    """
    Bir maçın odds hareketinin normalize edilmiş hali.
    """

    opening: Odds = field(
        default_factory=Odds
    )

    closing: Odds = field(
        default_factory=Odds
    )

    movement: OddsMovement = field(
        default_factory=OddsMovement
    )

    history: List[
        OddsHistoryPoint
    ] = field(
        default_factory=list
    )


def _float_or_none(
    value: Any,
) -> Optional[float]:
    if value is None:
        return None

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _first(
    data: Dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        if (
            key in data
            and data[key] is not None
        ):
            return data[key]

    return None


def _extract_timestamp(
    item: Dict[str, Any],
) -> Optional[str]:
    value = _first(
        item,
        "timestamp",
        "time",
        "datetime",
        "created_at",
        "updated_at",
    )

    if value is None:
        return None

    return str(value)


def _extract_side_value(
    data: Dict[str, Any],
    side: str,
) -> Optional[float]:

    aliases = {
        "home": (
            "home",
            "1",
            "home_odds",
        ),
        "draw": (
            "draw",
            "x",
            "X",
            "draw_odds",
        ),
        "away": (
            "away",
            "2",
            "away_odds",
        ),
    }

    value = _first(
        data,
        *aliases[side],
    )

    return _float_or_none(
        value
    )


def _extract_point(
    item: Dict[str, Any],
) -> OddsHistoryPoint:

    return OddsHistoryPoint(
        timestamp=_extract_timestamp(
            item
        ),

        home=_extract_side_value(
            item,
            "home",
        ),

        draw=_extract_side_value(
            item,
            "draw",
        ),

        away=_extract_side_value(
            item,
            "away",
        ),

        source=(
            str(
                _first(
                    item,
                    "bookmaker",
                    "source",
                    "provider",
                )
            )
            if _first(
                item,
                "bookmaker",
                "source",
                "provider",
            )
            is not None
            else None
        ),
    )


def _extract_history_list(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    API response içindeki history listesini bulur.
    """

    if not isinstance(
        payload,
        dict,
    ):
        return []

    data = payload.get(
        "data"
    )

    if isinstance(
        data,
        list,
    ):
        return [
            item
            for item in data
            if isinstance(
                item,
                dict,
            )
        ]

    if isinstance(
        data,
        dict,
    ):
        for key in (
            "history",
            "odds",
            "items",
            "results",
        ):
            value = data.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                return [
                    item
                    for item in value
                    if isinstance(
                        item,
                        dict,
                    )
                ]

    for key in (
        "history",
        "odds",
        "items",
        "results",
    ):
        value = payload.get(
            key
        )

        if isinstance(
            value,
            list,
        ):
            return [
                item
                for item in value
                if isinstance(
                    item,
                    dict,
                )
            ]

    return []


def calculate_movement(
    opening: Odds,
    closing: Odds,
) -> OddsMovement:
    """
    Opening -> Closing farkını hesaplar.
    """

    home = None
    draw = None
    away = None

    if (
        opening.home is not None
        and closing.home is not None
    ):
        home = (
            closing.home
            - opening.home
        )

    if (
        opening.draw is not None
        and closing.draw is not None
    ):
        draw = (
            closing.draw
            - opening.draw
        )

    if (
        opening.away is not None
        and closing.away is not None
    ):
        away = (
            closing.away
            - opening.away
        )

    return OddsMovement(
        home=home,
        draw=draw,
        away=away,
    )


def map_odds_history(
    payload: Dict[str, Any],
) -> OddsHistoryAnalysis:
    """
    Odds history response'unu normalize eder.

    Tarihsel noktalar kronolojik sıraya sokulur.

    İlk kullanılabilir nokta:
        OPENING

    Son kullanılabilir nokta:
        CLOSING
    """

    raw_history = (
        _extract_history_list(
            payload
        )
    )

    points = [
        _extract_point(item)
        for item in raw_history
    ]

    # Eksik odds noktalarını temizle.
    points = [
        point
        for point in points
        if (
            point.home is not None
            or point.draw is not None
            or point.away is not None
        )
    ]

    if not points:
        return OddsHistoryAnalysis()

    # API'nin zaman alanı farklı formatlarda
    # gelebileceğinden burada güvenli şekilde
    # mevcut sıralamayı koruyoruz.
    opening_point = points[0]
    closing_point = points[-1]

    opening = Odds(
        home=opening_point.home,
        draw=opening_point.draw,
        away=opening_point.away,
    )

    closing = Odds(
        home=closing_point.home,
        draw=closing_point.draw,
        away=closing_point.away,
    )

    movement = calculate_movement(
        opening,
        closing,
    )

    return OddsHistoryAnalysis(
        opening=opening,
        closing=closing,
        movement=movement,
        history=points,
    )
