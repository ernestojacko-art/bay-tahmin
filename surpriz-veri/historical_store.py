"""
SÜRPRİZ VERİ - Historical Store

Tarihsel maç kayıtlarını Sürpriz Veri motoru için
havuzlanabilir şekilde tutar.

Bu modül:
- Opening odds
- Closing odds
- Odds movement
- Market profile
- Kickoff
- Gerçekleşmiş sonuç

verilerini tek tarihsel kayıt altında korur.

Ancak bu verileri birbirine karıştırmaz.

Mevcut Bay Tahmin sistemine dokunmaz.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .models import HistoricalMatch


@dataclass
class HistoricalStore:
    """
    Sürpriz Veri tarihsel kayıt deposu.

    Aynı maç birden fazla kanıt havuzunda
    kullanılabilir.

    Bu bilinçli bir davranıştır.
    """

    matches: List[
        HistoricalMatch
    ] = field(
        default_factory=list
    )

    def add(
        self,
        match: HistoricalMatch,
    ) -> None:
        """
        Tek bir tarihsel maç ekler.

        Aynı match_id tekrar gelirse mevcut kayıt
        güncellenir.
        """

        match_id = (
            match.record.match_id
        )

        for index, existing in enumerate(
            self.matches
        ):
            if (
                existing.record.match_id
                == match_id
            ):
                self.matches[index] = match
                return

        self.matches.append(
            match
        )

    def add_many(
        self,
        matches: Iterable[
            HistoricalMatch
        ],
    ) -> None:
        """Birden fazla tarihsel maç ekler."""

        for match in matches:
            self.add(
                match
            )

    def get(
        self,
        match_id: str,
    ) -> Optional[
        HistoricalMatch
    ]:
        """ID ile tarihsel maç bulur."""

        for match in self.matches:
            if (
                match.record.match_id
                == str(match_id)
            ):
                return match

        return None

    def remove(
        self,
        match_id: str,
    ) -> bool:
        """
        ID'ye göre kayıt siler.

        Silinen kayıt varsa True döndürür.
        """

        original_length = len(
            self.matches
        )

        self.matches = [
            match
            for match in self.matches
            if (
                match.record.match_id
                != str(match_id)
            )
        ]

        return (
            len(self.matches)
            != original_length
        )

    def clear(self) -> None:
        """Tüm tarihsel kayıtları temizler."""

        self.matches.clear()

    def count(self) -> int:
        """Toplam tarihsel maç sayısı."""

        return len(
            self.matches
        )

    def ids(self) -> List[str]:
        """Tüm tarihsel maç ID'lerini döndürür."""

        return [
            match.record.match_id
            for match in self.matches
        ]

    def by_competition(
        self,
        competition: str,
    ) -> List[
        HistoricalMatch
    ]:
        """
        Belirli bir lig/organizasyondaki
        tarihsel maçları döndürür.
        """

        target = (
            competition
            .strip()
            .lower()
        )

        return [
            match
            for match in self.matches
            if (
                match.record.competition
                and
                match.record.competition
                .strip()
                .lower()
                == target
            )
        ]

    def with_opening_odds(
        self,
    ) -> List[
        HistoricalMatch
    ]:
        """
        Opening odds bilgisi bulunan maçları döndürür.
        """

        return [
            match
            for match in self.matches
            if (
                match.record.opening_odds.home
                is not None
                or
                match.record.opening_odds.draw
                is not None
                or
                match.record.opening_odds.away
                is not None
            )
        ]

    def with_closing_odds(
        self,
    ) -> List[
        HistoricalMatch
    ]:
        """
        Closing odds bilgisi bulunan maçları döndürür.
        """

        return [
            match
            for match in self.matches
            if (
                match.record.closing_odds.home
                is not None
                or
                match.record.closing_odds.draw
                is not None
                or
                match.record.closing_odds.away
                is not None
            )
        ]

    def with_movement(
        self,
    ) -> List[
        HistoricalMatch
    ]:
        """
        Opening -> Closing hareketi bulunan
        maçları döndürür.
        """

        return [
            match
            for match in self.matches
            if (
                match.record.odds_movement.home
                is not None
                or
                match.record.odds_movement.draw
                is not None
                or
                match.record.odds_movement.away
                is not None
            )
        ]

    def with_htft(
        self,
    ) -> List[
        HistoricalMatch
    ]:
        """
        Gerçekleşmiş HT/MS sonucu bulunan
        maçları döndürür.
        """

        return [
            match
            for match in self.matches
            if (
                match.record.outcome
                is not None
                and
                match.record.outcome.ht_ft
                is not None
            )
        ]

    def summary(self) -> Dict[str, int]:
        """
        Tarihsel veri deposunun temel durumunu döndürür.
        """

        return {
            "total_matches": self.count(),

            "with_opening_odds": len(
                self.with_opening_odds()
            ),

            "with_closing_odds": len(
                self.with_closing_odds()
            ),

            "with_movement": len(
                self.with_movement()
            ),

            "with_htft": len(
                self.with_htft()
            ),
        }
