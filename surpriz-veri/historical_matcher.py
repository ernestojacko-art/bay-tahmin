"""
SÜRPRİZ VERİ - Historical Matcher

Güncel maçı geçmiş maçlarla farklı kanıt havuzları üzerinden
eşleştirir.

Havuzlar birbirinden bağımsız tutulur:

1. Opening Odds
2. Closing Odds
3. Odds Movement
4. Market Profile
5. Kickoff Time

Bu modül henüz nihai tahmin üretmez.
Görevi tarihsel eşleşme kümelerini oluşturmaktır.
"""

from dataclasses import dataclass, field
from typing import Dict, List

from .closing_pool import build_closing_pool
from .historical_matcher import *  # type: ignore
from .kickoff_pool import build_kickoff_pool
from .market_profile import build_market_profile_pool
from .models import HistoricalMatch, MatchRecord
from .odds_movement import build_movement_pool
from .opening_pool import build_opening_pool


@dataclass
class HistoricalPools:
    """
    Güncel maç için oluşturulan tüm tarihsel havuzlar.
    """

    opening: List[HistoricalMatch] = field(
        default_factory=list
    )

    closing: List[HistoricalMatch] = field(
        default_factory=list
    )

    movement: List[HistoricalMatch] = field(
        default_factory=list
    )

    market_profile: List[HistoricalMatch] = field(
        default_factory=list
    )

    kickoff: List[HistoricalMatch] = field(
        default_factory=list
    )

    def sizes(self) -> Dict[str, int]:
        """
        Her havuzdaki tarihsel maç sayısını döndürür.
        """

        return {
            "opening": len(self.opening),
            "closing": len(self.closing),
            "movement": len(self.movement),
            "market_profile": len(self.market_profile),
            "kickoff": len(self.kickoff),
        }


def build_historical_pools(
    current: MatchRecord,
    historical_matches: List[HistoricalMatch],
    kickoff_tolerance_minutes: int = 60,
) -> HistoricalPools:
    """
    Güncel maç için tüm bağımsız tarihsel havuzları oluşturur.

    Önemli:
    Aynı tarihsel maç birden fazla havuzda bulunabilir.

    Örneğin aynı maç:
        Opening Pool
        Closing Pool
        Movement Pool

    içerisinde ayrı ayrı kanıt olarak değerlendirilebilir.

    Bu durum veri karışıklığı değildir; her havuz farklı
    bir benzerlik boyutunu temsil eder.
    """

    return HistoricalPools(
        opening=build_opening_pool(
            current,
            historical_matches,
        ),
        closing=build_closing_pool(
            current,
            historical_matches,
        ),
        movement=build_movement_pool(
            current,
            historical_matches,
        ),
        market_profile=build_market_profile_pool(
            current,
            historical_matches,
        ),
        kickoff=build_kickoff_pool(
            current,
            historical_matches,
            tolerance_minutes=kickoff_tolerance_minutes,
        ),
    )


def pool_matches(
    pools: HistoricalPools,
) -> Dict[str, List[HistoricalMatch]]:
    """
    Havuzları isimleriyle birlikte sözlük olarak döndürür.
    """

    return {
        "opening": pools.opening,
        "closing": pools.closing,
        "movement": pools.movement,
        "market_profile": pools.market_profile,
        "kickoff": pools.kickoff,
    }


def unique_historical_match_ids(
    pools: HistoricalPools,
) -> List[str]:
    """
    En az bir havuzda bulunan benzersiz tarihsel maç ID'lerini
    döndürür.

    Bu liste yalnızca referans içindir.
    Havuzların bağımsızlığı korunur.
    """

    ids = set()

    for matches in pool_matches(pools).values():
        for historical in matches:
            ids.add(historical.record.match_id)

    return sorted(ids)
