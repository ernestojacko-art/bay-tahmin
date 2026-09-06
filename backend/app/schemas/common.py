from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class DataQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ErrorResponse(BaseModel):
    error: str
    detail: str
