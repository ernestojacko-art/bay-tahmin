"""
SÜRPRİZ VERİ - AI destekli karşılaştırmalı yorum

Bu katman tarihsel benzer maç sonuçlarını, güncel takımların
son performanslarını ve ilk yarı eğilimlerini birlikte yorumlar.

Bu ilk sürümde "AI" yorumu gerçek verilerden türetilen açıklanabilir
istatistiksel sentezdir; veri yoksa varsayım üretmez.
"""

from typing import Any, Dict, List, Optional


def _result(home: int, away: int) -> str:
    if home > away:
        return "1"
    if home < away:
        return "2"
    return "X"


def _team_form(fixtures: List[Dict[str, Any]], team_id: Any) -> Dict[str, Any]:
    rows = []
    for f in fixtures:
        teams = f.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        hid = home.get("id")
        aid = away.get("id")
        goals = f.get("goals") or {}
        fh = goals.get("half_home")
        fa = goals.get("half_away")
        gh = goals.get("home")
        ga = goals.get("away")
        if hid != team_id and aid != team_id:
            continue
        if not all(isinstance(x, (int, float)) for x in (gh, ga)):
            continue
        is_home = hid == team_id
        gf = gh if is_home else ga
        gc = ga if is_home else gh
        first_for = fh if is_home else fa
        first_against = fa if is_home else fh
        rows.append({
            "gf": gf,
            "ga": gc,
            "first_for": first_for,
            "first_against": first_against,
            "result": _result(gh, ga) if is_home else _result(ga, gh),
        })

    total = len(rows)
    wins = sum(r["result"] == "1" for r in rows)
    draws = sum(r["result"] == "X" for r in rows)
    losses = total - wins - draws
    fh_rows = [r for r in rows if isinstance(r["first_for"], (int, float)) and isinstance(r["first_against"], (int, float))]
    return {
        "matches": total,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "avg_goals_for": round(sum(r["gf"] for r in rows) / total, 2) if total else None,
        "avg_goals_against": round(sum(r["ga"] for r in rows) / total, 2) if total else None,
        "first_half_avg_for": round(sum(r["first_for"] for r in fh_rows) / len(fh_rows), 2) if fh_rows else None,
        "first_half_avg_against": round(sum(r["first_against"] for r in fh_rows) / len(fh_rows), 2) if fh_rows else None,
        "first_half_data_matches": len(fh_rows),
    }


def build_ai_note(
    home_team: str,
    away_team: str,
    home_form: Dict[str, Any],
    away_form: Dict[str, Any],
    common_distribution: List[Dict[str, Any]],
    opening_distribution: List[Dict[str, Any]],
    closing_distribution: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Tarihsel sonuçlar + takım istatistiklerinden açıklanabilir AI notu.
    """
    signals: List[str] = []
    support = 0
    total = 0

    if home_form.get("matches"):
        signals.append(
            f"{home_team} son {home_form['matches']} maçta "
            f"{home_form['wins']} galibiyet, {home_form['draws']} beraberlik, "
            f"{home_form['losses']} mağlubiyet aldı."
        )
    if away_form.get("matches"):
        signals.append(
            f"{away_team} son {away_form['matches']} maçta "
            f"{away_form['wins']} galibiyet, {away_form['draws']} beraberlik, "
            f"{away_form['losses']} mağlubiyet aldı."
        )

    if home_form.get("first_half_avg_for") is not None and away_form.get("first_half_avg_against") is not None:
        signals.append(
            f"{home_team} ilk yarı gol ortalaması "
            f"{home_form['first_half_avg_for']}; {away_team}'nin ilk yarı "
            f"gol yeme ortalaması {away_form['first_half_avg_against']}."
        )
    if away_form.get("first_half_avg_for") is not None and home_form.get("first_half_avg_against") is not None:
        signals.append(
            f"{away_team} ilk yarı gol ortalaması "
            f"{away_form['first_half_avg_for']}; {home_team}'in ilk yarı "
            f"gol yeme ortalaması {home_form['first_half_avg_against']}."
        )

    distributions = [d for d in (common_distribution or closing_distribution or opening_distribution) if d.get("outcome")]
    top = distributions[:3]
    if top:
        text = ", ".join(f"{d['outcome']} %{d['percentage']}" for d in top)
        signals.append(f"Benzer tarihsel maçlarda öne çıkan İY/MS sonuçları: {text}.")
        support += 1
        total += 1

    # Tarihsel ortak örneklem varsa daha fazla ağırlık ver.
    if common_distribution:
        support += 2
        total += 2
    if home_form.get("matches") and away_form.get("matches"):
        total += 2
        if (
            home_form.get("avg_goals_for") is not None
            and away_form.get("avg_goals_against") is not None
            and home_form["avg_goals_for"] > away_form["avg_goals_against"]
        ):
            support += 1
        if (
            away_form.get("avg_goals_for") is not None
            and home_form.get("avg_goals_against") is not None
            and away_form["avg_goals_for"] > home_form["avg_goals_against"]
        ):
            support += 1

    confidence = round(min(95.0, 50.0 + (support / total * 40.0 if total else 0.0)), 1)

    if not signals:
        note = "Yeterli güncel takım veya tarihsel benzerlik verisi bulunamadığı için güçlü bir AI yorumu üretilemedi."
        confidence = 20.0
    else:
        note = " ".join(signals)

    return {
        "note": note,
        "confidence": confidence,
        "basis": [
            "tarihsel benzer oran sonuçları",
            "güncel takım performansı",
            "ilk yarı performansı",
        ],
        "disclaimer": "Güven puanı gerçekleşecek sonucu garanti etmez; mevcut verilerin birbirini destekleme düzeyini ifade eder.",
    }
