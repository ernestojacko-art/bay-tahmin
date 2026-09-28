"""
SÜRPRİZ VERİ - Historical Loader

5DollarFootballAPI üzerinden tarihsel maçları
sayfalı şekilde yükler.

Amaç:
- Çok sayıda geçmiş maçı tarayabilmek
- Sabit 5 maç sınırı kullanmamak
- Eksik/bozuk kayıtların tüm taramayı durdurmasını önlemek
- HistoricalStore'a temiz kayıtlar aktarmak

Mevcut Bay Tahmin sistemine dokunmaz.
"""

from typing import List, Optional

from .historical_provider import (
    HistoricalDataProvider,
)
from .historical_store import (
    HistoricalStore,
)
from .models import HistoricalMatch


class HistoricalLoader:
    """
    Tarihsel veri yükleme yöneticisi.
    """

    def __init__(
        self,
        provider: Optional[
            HistoricalDataProvider
        ] = None,
    ):
        self.provider = (
            provider
            or HistoricalDataProvider()
        )

    def load_page(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        page: int = 1,
        per_page: int = 100,
        include_odds_history: bool = True,
    ) -> List[
        HistoricalMatch
    ]:
        """
        Tek bir tarihsel veri sayfası yükler.
        """

        return (
            self.provider
            .load_historical_matches(
                start_time=start_time,
                end_time=end_time,
                page=page,
                per_page=per_page,
                include_odds_history=(
                    include_odds_history
                ),
            )
        )

    def load_pages(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        start_page: int = 1,
        max_pages: int = 10,
        per_page: int = 100,
        include_odds_history: bool = True,
    ) -> HistoricalStore:
        """
        Birden fazla tarihsel veri sayfasını yükler.

        max_pages yalnızca API'yi sonsuz döngüye
        sokmamak için güvenlik sınırıdır.

        Maç sayısına 5/10/20 gibi bir sınır
        koymaz.
        """

        store = HistoricalStore()

        page = max(
            1,
            start_page,
        )

        pages_loaded = 0

        while (
            pages_loaded
            < max_pages
        ):

            matches = self.load_page(
                start_time=start_time,
                end_time=end_time,
                page=page,
                per_page=per_page,
                include_odds_history=(
                    include_odds_history
                ),
            )

            if not matches:
                break

            store.add_many(
                matches
            )

            pages_loaded += 1
            page += 1

        return store

    def load_until_count(
        self,
        target_count: int,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        start_page: int = 1,
        max_pages: int = 100,
        per_page: int = 100,
        include_odds_history: bool = True,
    ) -> HistoricalStore:
        """
        En az target_count kullanılabilir tarihsel
        kayıt elde edilene kadar sayfaları tarar.

        Önemli:
        Bu bir sonuç limiti değildir.

        Amaç yalnızca tarihsel veri tabanını
        yeterli büyüklüğe ulaştırmaktır.
        """

        if target_count <= 0:
            return HistoricalStore()

        store = HistoricalStore()

        page = max(
            1,
            start_page,
        )

        pages_loaded = 0

        while (
            store.count()
            < target_count
            and pages_loaded
            < max_pages
        ):

            matches = self.load_page(
                start_time=start_time,
                end_time=end_time,
                page=page,
                per_page=per_page,
                include_odds_history=(
                    include_odds_history
                ),
            )

            if not matches:
                break

            store.add_many(
                matches
            )

            pages_loaded += 1
            page += 1

        return store
