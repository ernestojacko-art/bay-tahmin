"""
SÜRPRİZ VERİ - Data Models

Tarihsel maçların ve güncel maçların analizde kullanılacak
veri yapısını tanımlar.

Önemli:
Opening odds, closing odds ve odds movement birbirinden
ayrı veri alanlarıdır ve birbirine karıştırılmaz.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Odds:
    """
    Üçlü maç sonucu oranları.

    home = 1
    draw = X
    away = 2
    """

    home: Optional[float] = None
    draw: Optional[float] = None
    away: Optional[float] = None

    def values(self) -> List[Optional[float]]:
        return [self.home, self.draw, self.away]


@dataclass
class OddsMovement:
    """
    Açılış oranından kapanış oranına hareket.

    Opening ve closing değerleri ayrıca saklanır.
    Movement yalnızca türetilmiş ayrı bir kanıt katmanıdır.
    """

    home: Optional[float] = None
    draw: Optional[float] = None
    away: Optional[float] = None


@dataclass
class MatchOutcome:
    """
    Maçın gerçekleşmiş sonuçları.
    """

    first_half_result: Optional[str] = None
    second_half_result: Optional[str] = None
    full_time_result: Optional[str] = None

    first_half_score: Optional[str] = None
    second_half_score: Optional[str] = None
    full_time_score: Optional[str] = None

    ht_ft: Optional[str] = None

    over_under: Dict[str, str] = field(default_factory=dict)
    btts: Optional[str] = None

    other_markets: Dict[str, str] = field(default_factory=dict)


@dataclass
class MarketProfile:
    """
    Maçın mevcut bahis-market yapısı.

    Buradaki bilgiler oranlardan ayrı tutulur.
    """

    available_markets: List[str] = field(default_factory=list)
    market_count: int = 0

    ht_ft_available: bool = False
    first_half_available: bool = False
    second_half_available: bool = False
    over_under_available: bool = False
    btts_available: bool = False

    all_markets_open: bool = False


@dataclass
class MatchRecord:
    """
    Güncel veya tarihsel bir maçın temel veri modeli.

    Tarihsel kayıt için opening_odds ve closing_odds
    mutlaka ayrı tutulmalıdır.
    """

    match_id: str
    home_team: str
    away_team: str

    kickoff_time: Optional[str] = None
    competition: Optional[str] = None

    opening_odds: Odds = field(default_factory=Odds)
    closing_odds: Odds = field(default_factory=Odds)

    odds_movement: OddsMovement = field(default_factory=OddsMovement)

    market_profile: MarketProfile = field(
        default_factory=MarketProfile
    )

    outcome: MatchOutcome = field(
        default_factory=MatchOutcome
    )

    # İsteğe bağlı takım/form/veri profili.
    team_profile: Dict[str, object] = field(
        default_factory=dict
    )

    metadata: Dict[str, object] = field(
        default_factory=dict
    )


@dataclass
class HistoricalMatch:
    """
    Tarihsel eşleştirme için kullanılan kayıt.

    Aynı maç:
    - opening pool
    - closing pool
    - movement pool

    içerisinde farklı kanıt katmanları olarak değerlendirilebilir.
    """

    record: MatchRecord

    source: Optional[str] = None
    source_timestamp: Optional[str] = None


@dataclass
class Evidence:
    """
    Bir tahmin sonucunu destekleyen tek bir kanıt.

    Örnek:
    150 opening-similar maç içinde 12 kez 1/2 görülmesi.
    """

    pool_name: str

    outcome: str

    sample_size: int
    occurrence_count: int

    frequency: float = 0.0

    weight: float = 1.0

    details: Dict[str, object] = field(
        default_factory=dict
    )


@dataclass
class SurpriseCandidate:
    """
    Güncel maç için üretilen sürpriz aday sonucu.
    """

    match_id: str
    home_team: str
    away_team: str

    predicted_outcome: str

    evidence: List[Evidence] = field(
        default_factory=list
    )

    combined_score: float = 0.0
    confidence: float = 0.0

    explanation: str = ""

    supporting_pools: List[str] = field(
        default_factory=list
    )
