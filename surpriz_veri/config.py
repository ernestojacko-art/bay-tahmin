"""
SÜRPRİZ VERİ - Configuration

Bu modül mevcut Bay Tahmin sisteminden tamamen bağımsızdır.
Mevcut Bay Tahmin dosyalarına müdahale etmez.
"""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class SurpriseConfig:
    # Mevcut ve kullanılacak veri sağlayıcı.
    api_base_url: str = os.getenv(
        "SURPRISE_API_BASE_URL",
        "https://api.5dollarfootballapi.com/v1",
    )

    # Tarihsel eşleştirmede kullanılacak MUTLAK oran toleransı: güncel oranın
    # 1, X ve 2 bileşenlerinin HER BİRİ, geçmiş maçın aynı bileşeninden en
    # fazla bu kadar (oran birimi cinsinden) farklı olabilir. Yüzde/oran
    # bazlı genişletme YOKTUR -- "birebir ya da çok yakın" eşleşme istenir.
    # (Eski varsayılan 0.15 + %5 genişleme, alakasız maçları da eşleştiriyordu.)
    opening_odds_tolerance: float = float(
        os.getenv("SURPRISE_OPENING_ODDS_TOLERANCE", "0.10")
    )

    closing_odds_tolerance: float = float(
        os.getenv("SURPRISE_CLOSING_ODDS_TOLERANCE", "0.10")
    )

    movement_tolerance: float = float(
        os.getenv("SURPRISE_MOVEMENT_TOLERANCE", "0.15")
    )

    # Tarihsel benzerlik taramasının hedeflediği geriye dönük pencere.
    # Mevcut 5DollarFootballAPI Pro kapsamındaki yaklaşık 12 aylık geçmiş aranır.
    historical_days: int = int(
        os.getenv("SURPRISE_HISTORICAL_DAYS", "365")
    )

    # Minimum tarihsel örnek sayısı.
    min_historical_samples: int = int(
        os.getenv("SURPRISE_MIN_HISTORICAL_SAMPLES", "20")
    )

    # Kullanıcı kaç maç isterse o kadar sonuç üretilebilir.
    # Burada 5 maç sınırı yoktur.
    default_result_limit: int = int(
        os.getenv("SURPRISE_DEFAULT_RESULT_LIMIT", "5")
    )

    max_result_limit: int = int(
        os.getenv("SURPRISE_MAX_RESULT_LIMIT", "100")
    )

    # Havuzların birbirine karıştırılmasını önleyen açık ayrım.
    use_opening_pool: bool = True
    use_closing_pool: bool = True
    # Hareket (yüzde düşüş/yükseliş), market profili ve başlama saati
    # havuzları varsayılan olarak KAPALI: kullanıcı yalnızca güncel
    # açılış/kapanış oranlarının geçmiş maçların açılış/kapanış oranlarıyla
    # birebir örtüşmesini istiyor. Env ile açılabilir.
    use_movement_pool: bool = os.getenv("SURPRISE_USE_MOVEMENT_POOL", "0") == "1"
    use_market_profile_pool: bool = os.getenv("SURPRISE_USE_MARKET_PROFILE_POOL", "0") == "1"
    use_kickoff_pool: bool = os.getenv("SURPRISE_USE_KICKOFF_POOL", "0") == "1"
    use_team_profile: bool = True


CONFIG = SurpriseConfig()
