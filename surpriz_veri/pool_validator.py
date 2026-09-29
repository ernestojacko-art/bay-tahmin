"""
SÜRPRİZ VERİ - Pool Validator

Opening, Closing ve Movement havuzlarının
veri ayrımını kontrol eder.

Bu modül tahmin üretmez.

Amaç:
- Opening verisi gerçekten Opening mi?
- Closing verisi gerçekten Closing mi?
- Movement gerçekten Opening -> Closing mi?
- Eksik odds verisi var mı?

kontrollerini yapmaktır.

Mevcut Bay Tahmin sistemine dokunmaz.
"""

from dataclasses import dataclass
from typing import List, Optional

from .models import HistoricalMatch, MatchRecord


@dataclass
class PoolValidationResult:
    """
    Tek bir tarihsel maç için havuz doğrulama sonucu.
    """

    match_id: str

    opening_valid: bool
    closing_valid: bool
    movement_valid: bool

    opening_missing: bool
    closing_missing: bool
    movement_missing: bool

    warnings: List[str]


def _has_opening_odds(
    record: MatchRecord,
) -> bool:
    odds = record.opening_odds

    return (
        odds.home is not None
        or odds.draw is not None
        or odds.away is not None
    )


def _has_closing_odds(
    record: MatchRecord,
) -> bool:
    odds = record.closing_odds

    return (
        odds.home is not None
        or odds.draw is not None
        or odds.away is not None
    )


def _has_movement(
    record: MatchRecord,
) -> bool:
    movement = record.odds_movement

    return (
        movement.home is not None
        or movement.draw is not None
        or movement.away is not None
    )


def _validate_movement(
    record: MatchRecord,
) -> bool:
    """
    Movement değerlerinin gerçekten:

        closing - opening

    hesabıyla uyumlu olup olmadığını kontrol eder.

    Küçük floating-point farklarına tolerans tanır.
    """

    opening = record.opening_odds
    closing = record.closing_odds
    movement = record.odds_movement

    tolerance = 0.000001

    checks = []

    if (
        opening.home is not None
        and closing.home is not None
        and movement.home is not None
    ):
        checks.append(
            abs(
                (
                    closing.home
                    - opening.home
                )
                - movement.home
            )
            <= tolerance
        )

    if (
        opening.draw is not None
        and closing.draw is not None
        and movement.draw is not None
    ):
        checks.append(
            abs(
                (
                    closing.draw
                    - opening.draw
                )
                - movement.draw
            )
            <= tolerance
        )

    if (
        opening.away is not None
        and closing.away is not None
        and movement.away is not None
    ):
        checks.append(
            abs(
                (
                    closing.away
                    - opening.away
                )
                - movement.away
            )
            <= tolerance
        )

    if not checks:
        return False

    return all(checks)


def validate_match(
    match: HistoricalMatch,
) -> PoolValidationResult:
    """
    Tek bir tarihsel maçın odds havuzlarını doğrular.
    """

    record = match.record

    opening_exists = (
        _has_opening_odds(
            record
        )
    )

    closing_exists = (
        _has_closing_odds(
            record
        )
    )

    movement_exists = (
        _has_movement(
            record
        )
    )

    warnings: List[str] = []

    if not opening_exists:
        warnings.append(
            "Opening odds bulunamadı."
        )

    if not closing_exists:
        warnings.append(
            "Closing odds bulunamadı."
        )

    if (
        opening_exists
        and closing_exists
        and not movement_exists
    ):
        warnings.append(
            "Opening ve Closing var ancak "
            "movement hesaplanmamış."
        )

    movement_valid = False

    if movement_exists:
        movement_valid = (
            _validate_movement(
                record
            )
        )

        if not movement_valid:
            warnings.append(
                "Movement, Opening -> Closing "
                "farkıyla uyuşmuyor."
            )

    return PoolValidationResult(
        match_id=record.match_id,

        opening_valid=opening_exists,

        closing_valid=closing_exists,

        movement_valid=(
            movement_valid
            if movement_exists
            else False
        ),

        opening_missing=(
            not opening_exists
        ),

        closing_missing=(
            not closing_exists
        ),

        movement_missing=(
            not movement_exists
        ),

        warnings=warnings,
    )


def validate_matches(
    matches: List[
        HistoricalMatch
    ],
) -> List[
    PoolValidationResult
]:
    """
    Birden fazla tarihsel maçı doğrular.
    """

    return [
        validate_match(
            match
        )
        for match in matches
    ]


def validation_summary(
    results: List[
        PoolValidationResult
    ],
) -> dict:
    """
    Havuz veri kalitesinin özetini verir.
    """

    return {
        "total": len(results),

        "opening_valid": sum(
            1
            for result in results
            if result.opening_valid
        ),

        "closing_valid": sum(
            1
            for result in results
            if result.closing_valid
        ),

        "movement_valid": sum(
            1
            for result in results
            if result.movement_valid
        ),

        "opening_missing": sum(
            1
            for result in results
            if result.opening_missing
        ),

        "closing_missing": sum(
            1
            for result in results
            if result.closing_missing
        ),

        "movement_missing": sum(
            1
            for result in results
            if result.movement_missing
        ),

        "warnings": sum(
            len(result.warnings)
            for result in results
        ),
    }
