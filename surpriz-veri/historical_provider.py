"""
SÜRPRİZ VERİ - Historical Data Provider

Geçmiş maçları 5DollarFootballAPI üzerinden toplamak için
bağımsız veri katmanı.

Bu modül:
- Geçmiş fixture'ları alır.
- Odds verisini ayrı alır.
- Odds history verisini ayrı alır.
- Gerçekleşmiş sonucu ayrı mapper'a bırakır.

Mevcut Bay Tahmin sistemine dokunmaz.
"""

from typing import Any, Dict, List, Optional

from .data_provider import (
    FiveDollarFootballAPI,
    FootballAPIError,
)
from .models import HistoricalMatch, MatchRecord
from .data_mapper import map_fixture_to_match_record
from .result_mapper import map_fixture_result


class HistoricalDataProvider:
    """
    Sürpriz Veri için tarihsel maç sağlayıcısı.
    """

    def __init__(
        self,
        client: Optional[
            FiveDollarFootballAPI
        ] = None,
    ):
        self.client = (
            client
            or FiveDollarFootballAPI()
        )

    def get_fixtures(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Geçmiş fixture listesini alır.

        Sadece tamamlanmış maçlar hedeflenir.
        """

        payload = self.client.fixtures(
            start_time=start_time,
            end_time=end_time,
            status="finished",
            include="odds,events,stats",
            page=page,
            per_page=per_page,
        )

        return self.client.flatten_fixture_list(
            payload
        )

    def get_odds(
        self,
        fixture_id: int,
    ) -> Dict[str, Any]:
        """
        Bir maçın mevcut odds bilgisini alır.
        """

        return self.client.fixture_odds(
            fixture_id=fixture_id,
            bookmaker="bet365",
        )

    def get_odds_history(
        self,
        fixture_id: int,
        market: str = "1x2",
    ) -> Dict[str, Any]:
        """
        Bir maçın odds history bilgisini alır.

        Opening ve closing'in yanında
        ara hareketleri de korumak için kullanılır.
        """

        return self.client.fixture_odds_history(
            fixture_id=fixture_id,
            market=market,
            bookmaker="bet365",
            page=1,
            per_page=500,
        )

    def build_historical_match(
        self,
        fixture: Dict[str, Any],
        odds_data: Optional[
            Dict[str, Any]
        ] = None,
        odds_history: Optional[
            Dict[str, Any]
        ] = None,
    ) -> HistoricalMatch:
        """
        Fixture + odds + odds history verisini
        HistoricalMatch nesnesine dönüştürür.
        """

        record = map_fixture_to_match_record(
            fixture,
            odds_data=odds_data,
        )

        outcome = map_fixture_result(
            fixture
        )

        # MatchRecord immutable olmadığı için
        # yeni metadata bilgileri doğrudan
        # mevcut metadata alanına eklenir.
        metadata = dict(
            record.metadata
        )

        metadata[
            "odds_history"
        ] = (
            odds_history or {}
        )

        record = MatchRecord(
            match_id=record.match_id,
            home_team=record.home_team,
            away_team=record.away_team,
            kickoff_time=record.kickoff_time,
            competition=record.competition,
            opening_odds=record.opening_odds,
            closing_odds=record.closing_odds,
            odds_movement=record.odds_movement,
            market_profile=record.market_profile,
            outcome=outcome,
            team_profile=record.team_profile,
            metadata=metadata,
        )

        return HistoricalMatch(
            record=record,
            source="5DollarFootballAPI",
        )

    def load_historical_matches(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        page: int = 1,
        per_page: int = 100,
        include_odds_history: bool = True,
    ) -> List[HistoricalMatch]:
        """
        Geçmiş maçları komple HistoricalMatch
        listesine dönüştürür.

        Her maç için:

        1. Fixture
        2. Odds
        3. Odds history
        4. Gerçekleşmiş sonuç

        ayrı olarak işlenir.
        """

        fixtures = self.get_fixtures(
            start_time=start_time,
            end_time=end_time,
            page=page,
            per_page=per_page,
        )

        historical_matches: List[
            HistoricalMatch
        ] = []

        for fixture in fixtures:

            fixture_id = fixture.get(
                "id"
            )

            if fixture_id is None:
                continue

            try:
                odds_data = self.get_odds(
                    fixture_id=int(
                        fixture_id
                    )
                )
            except (
                FootballAPIError,
                ValueError,
                TypeError,
            ):
                odds_data = {}

            odds_history = {}

            if include_odds_history:
                try:
                    odds_history = (
                        self.get_odds_history(
                            fixture_id=int(
                                fixture_id
                            ),
                            market="1x2",
                        )
                    )
                except (
                    FootballAPIError,
                    ValueError,
                    TypeError,
                ):
                    odds_history = {}

            try:
                historical = (
                    self.build_historical_match(
                        fixture=fixture,
                        odds_data=odds_data,
                        odds_history=odds_history,
                    )
                )

                historical_matches.append(
                    historical
                )

            except (
                ValueError,
                TypeError,
            ):
                # Bozuk veya eksik bir fixture
                # bütün tarihsel taramayı durdurmaz.
                continue

        return historical_matches
