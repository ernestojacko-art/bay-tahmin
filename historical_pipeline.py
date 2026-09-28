"""
SÜRPRİZ VERİ - Historical Pipeline

Gerçek 5DollarFootballAPI verisini tarihsel
Sürpriz Veri kayıtlarına dönüştüren ana pipeline.

Akış:

5DollarFootballAPI
        ↓
HistoricalDataProvider
        ↓
Result Mapper
        ↓
Odds Mapper
        ↓
HistoricalMatch
        ↓
HistoricalStore

Bu pipeline mevcut Bay Tahmin backend'ine dokunmaz.
"""

from typing import List, Optional

from .historical_provider import (
    HistoricalDataProvider,
)

from .historical_store import (
    HistoricalStore,
)

from .models import (
    HistoricalMatch,
)


class HistoricalPipeline:
    """
    Sürpriz Veri tarihsel veri pipeline'ı.
    """

    def __init__(
        self,
        provider: HistoricalDataProvider,
        store: Optional[
            HistoricalStore
        ] = None,
    ):
        self.provider = provider

        self.store = (
            store
            if store is not None
            else HistoricalStore()
        )

    def load_page(
        self,
        page: int = 1,
        per_page: int = 100,
    ) -> List[HistoricalMatch]:
        """
        API'den tek sayfa tarihsel veri yükler.
        """

        matches = (
            self.provider.load_historical_matches(
                page=page,
                per_page=per_page,
            )
        )

        self.store.add_many(
            matches
        )

        return matches

    def load_pages(
        self,
        start_page: int = 1,
        max_pages: int = 10,
        per_page: int = 100,
    ) -> List[HistoricalMatch]:
        """
        Birden fazla sayfa tarihsel veri yükler.

        Aynı maç tekrar gelirse store katmanı
        kendi kayıt politikasını uygular.
        """

        all_matches = []

        page = start_page

        for _ in range(
            max_pages
        ):

            matches = self.load_page(
                page=page,
                per_page=per_page,
            )

            if not matches:
                break

            all_matches.extend(
                matches
            )

            page += 1

        return all_matches

    def load_until_count(
        self,
        target_count: int = 1000,
        start_page: int = 1,
        per_page: int = 100,
        max_pages: int = 100,
    ) -> List[HistoricalMatch]:
        """
        Belirli sayıda tarihsel maç biriktirmeye çalışır.

        target_count yalnızca veri toplama hedefidir.

        Kullanıcıya gösterilecek aday sayısını
        belirlemez.
        """

        target_count = max(
            1,
            int(target_count),
        )

        collected = []

        page = start_page

        for _ in range(
            max_pages
        ):

            if len(collected) >= target_count:
                break

            matches = self.load_page(
                page=page,
                per_page=per_page,
            )

            if not matches:
                break

            collected.extend(
                matches
            )

            page += 1

        return collected

    def count(self) -> int:
        """
        Store içindeki tarihsel kayıt sayısı.
        """

        return self.store.count()

    def summary(self):
        """
        Store özetini döndürür.
        """

        return self.store.summary()
