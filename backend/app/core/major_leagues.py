"""Majör lig listesi.

API'de ayrı bir İY/MS piyasası yok ve ilk yarı 1X2 piyasası Faroe Adaları gibi alt liglerde
bile açık olabiliyor; bu yüzden "bahis sitelerinde İY/MS'nin açık olduğu maçlar" için güvenilir
tek ölçüt LİG KALİTESİDİR. Adlar 5DollarFootballAPI'nin döndürdüğü gerçek `league_name`
değerleridir ("<Ülke> <Lig>" biçimi).
"""
from __future__ import annotations

import unicodedata

_MAJOR = {
    # Avrupa'nın üst düzey ligleri ve ikinci kademeleri
    "england premier league", "england championship",
    "spain la liga", "spain segunda",
    "italy serie a", "italy serie b",
    "germany bundesliga i", "germany bundesliga ii",
    "france ligue 1", "france ligue 2",
    "turkiye super lig", "turkiye 1 lig", "turkey super lig",
    "netherlands eredivisie", "portugal primeira liga",
    "belgium first division a", "scotland premiership",
    "greece super league 1", "austria bundesliga", "switzerland super league",
    "denmark superligaen",
    # Kıtasal kupalar (eleme turları dahil)
    "uefa champions league", "uefa champions league qualifying",
    "uefa europa league", "uefa europa league qualifying",
    "uefa conference league", "uefa conference league qualifying",
    "uefa nations league", "uefa nations league a", "uefa nations league b",
    # Avrupa dışı majörler
    "brazil serie a", "argentina liga profesional", "usa mls", "mexico liga mx",
    "copa libertadores", "copa sudamericana", "saudi arabia pro league",
}


def _norm(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", (name or "").casefold())
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


def is_major_league(league_name: str | None) -> bool:
    return _norm(league_name) in _MAJOR
