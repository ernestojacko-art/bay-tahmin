"""
SÜRPRİZ VERİ - 5DollarFootballAPI Data Provider

Bu modül yalnızca Sürpriz Veri motorunun veri erişim katmanıdır.

Mevcut Bay Tahmin backend'ine dokunmaz.

Veri kaynağı:
    https://api.5dollarfootballapi.com/v1

Önemli:
    Opening odds
    Closing odds
    Odds movement
    Market profile

ayrı veri alanları olarak korunur.
"""

import os
from typing import Any, Dict, List, Optional

import requests


DEFAULT_BASE_URL = (
    "https://api.5dollarfootballapi.com/v1"
)


class FootballAPIError(Exception):
    """5DollarFootballAPI erişim/veri hatası."""


class FiveDollarFootballAPI:
    """
    5DollarFootballAPI için bağımsız istemci.

    API anahtarı kod içine yazılmaz.
    Ortam değişkeninden okunur.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
    ):
        self.api_key = (
            api_key
            or os.getenv("FIVE_DOLLAR_API_KEY")
            or os.getenv("FOOTBALL_API_KEY")
            or os.getenv("FIVE_DOLLAR_FOOTBALL_API_KEY")
        )

        self.base_url = (
            base_url
            or os.getenv(
                "SURPRISE_API_BASE_URL",
                DEFAULT_BASE_URL,
            )
        ).rstrip("/")

        self.timeout = timeout

        if not self.api_key:
            raise FootballAPIError(
                "FIVE_DOLLAR_API_KEY ortam değişkeni bulunamadı."
            )

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        API'ye GET isteği gönderir.
        """

        url = f"{self.base_url}/{path.lstrip('/')}"

        response = requests.get(
            url,
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            raise FootballAPIError(
                f"API HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise FootballAPIError(
                "API geçerli JSON döndürmedi."
            ) from exc

        if payload.get("success") == 0:
            raise FootballAPIError(
                str(
                    payload.get(
                        "error",
                        payload,
                    )
                )
            )

        return payload

    def fixtures(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        status: str = "all",
        include: str = "odds,events,stats",
        page: int = 1,
        per_page: int = 100,
    ) -> Dict[str, Any]:
        """
        Belirli zaman aralığındaki maçları getirir.

        5DollarFootballAPI fixtures endpoint'i en fazla
        24 saatlik pencere kabul eder.
        """

        params: Dict[str, Any] = {
            "status": status,
            "include": include,
            "page": page,
            "per_page": per_page,
            "esports": "false",
        }

        if start_time is not None:
            params["start_time"] = start_time

        if end_time is not None:
            params["end_time"] = end_time

        return self._get(
            "/fixtures",
            params=params,
        )

    def fixture(
        self,
        fixture_id: int,
        include: str = "events,stats",
    ) -> Dict[str, Any]:
        """
        Tek bir maçı getirir.
        """

        return self._get(
            f"/fixtures/{fixture_id}",
            params={
                "include": include,
            },
        )

    def fixture_odds(
        self,
        fixture_id: int,
        bookmaker: str = "bet365",
    ) -> Dict[str, Any]:
        """
        Bir maçın bookmaker odds verilerini getirir.

        Varsayılan bookmaker:
            bet365
        """

        return self._get(
            f"/fixtures/{fixture_id}/odds",
            params={
                "bookmakers": bookmaker,
            },
        )

    def fixture_odds_history(
        self,
        fixture_id: int,
        market: str = "1x2",
        bookmaker: str = "bet365",
        page: int = 1,
        per_page: int = 500,
    ) -> Dict[str, Any]:
        """
        Bir maçın odds hareket geçmişini getirir.

        Bu endpoint özellikle Opening → Closing hareketinin
        daha ayrıntılı incelenebilmesi için kullanılacaktır.
        """

        return self._get(
            f"/fixtures/{fixture_id}/odds/history",
            params={
                "bookmaker": bookmaker,
                "market": market,
                "page": page,
                "per_page": per_page,
            },
        )

    def leagues(
        self,
        popular: Optional[int] = None,
        active_since: Optional[int] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> Dict[str, Any]:
        """Kapsanan ligleri döndürür; tarihsel tarama için kullanılır."""
        params: Dict[str, Any] = {
            "page": page,
            "per_page": min(per_page, 100),
            "esports": "false",
        }
        if popular is not None:
            params["popular"] = popular
        if active_since is not None:
            params["active_since"] = active_since
        return self._get("/leagues", params=params)

    def league_fixtures(
        self,
        league_id: int,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        status: str = "finished",
        include: str = "odds,events,stats",
        page: int = 1,
        per_page: int = 50,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """Bir ligin uzun tarihsel maç listesini getirir.

        /fixtures endpoint'indeki 24 saatlik pencere sınırını kullanmaz.
        """
        params: Dict[str, Any] = {
            "status": status,
            "include": include,
            "page": page,
            "per_page": min(per_page, 50 if include else 100),
            "order": order,
            "esports": "false",
        }
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time
        return self._get(f"/leagues/{int(league_id)}/fixtures", params=params)

    def team_fixtures(
        self,
        team_id: int,
        status: str = "finished",
        include: str = "stats",
        page: int = 1,
        per_page: int = 20,
    ) -> Dict[str, Any]:
        """Takımın geçmiş maçlarını getirir; AI takım/form katmanı için kullanılır."""
        return self._get(
            f"/teams/{team_id}/fixtures",
            params={
                "status": status,
                "include": include,
                "page": page,
                "per_page": min(per_page, 50),
                "order": "desc",
            },
        )

    def bookmakers(self) -> Dict[str, Any]:
        """
        Kullanılabilir bookmaker listesini getirir.
        """

        return self._get(
            "/bookmakers"
        )

    def flatten_fixture_list(
        self,
        payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        API response içindeki fixture listesini döndürür.
        """

        data = payload.get("data", [])

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in (
                "fixtures",
                "items",
                "results",
                "data",
            ):
                value = data.get(key)

                if isinstance(value, list):
                    return value

        return []
