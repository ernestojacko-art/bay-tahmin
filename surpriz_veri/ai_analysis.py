"""
SÜRPRİZ VERİ - Tarihsel karşılaştırma + takım verisi AI notu.

AI katmanı tarihsel verinin yerine geçmez. Önce gerçek geçmiş maçlar
bulunur; AI yalnızca açılış tablosu, kapanış tablosu, iki tablonun
kesişimi ve mevcut takım verilerini birlikte özetler.
"""

from typing import Any, Dict, List


def _top(distribution: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
    return [d for d in (distribution or []) if d.get("outcome")][:limit]


def _team_form(fixtures: List[Dict[str, Any]], team_id: Any) -> Dict[str, Any]:
    """Takımın tamamlanmış maçlarından gerçek temel form/ilk yarı özetini çıkarır."""
    result = {
        "matches": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "avg_goals_for": None,
        "avg_goals_against": None,
        "first_half_avg_for": None,
        "first_half_avg_against": None,
    }

    if not isinstance(fixtures, list) or team_id is None:
        return result

    rows = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        teams = fixture.get("teams") or {}
        home = teams.get("home") if isinstance(teams, dict) else {}
        away = teams.get("away") if isinstance(teams, dict) else {}
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue

        hid = home.get("id")
        aid = away.get("id")
        try:
            tid = int(team_id)
            is_home = int(hid) == tid
            is_away = int(aid) == tid
        except (TypeError, ValueError):
            is_home = str(hid) == str(team_id)
            is_away = str(aid) == str(team_id)

        if not (is_home or is_away):
            continue

        goals = fixture.get("goals") or {}
        if not isinstance(goals, dict):
            continue

        def num(value):
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        gh = num(goals.get("home"))
        ga = num(goals.get("away"))
        if gh is None or ga is None:
            continue

        gf = gh if is_home else ga
        against = ga if is_home else gh

        score = fixture.get("score") or {}
        halftime = score.get("halftime") if isinstance(score, dict) else None
        if not isinstance(halftime, dict):
            halftime = score.get("half_time") if isinstance(score, dict) else None

        hf = ha = None
        if isinstance(halftime, dict):
            h_h = num(halftime.get("home"))
            h_a = num(halftime.get("away"))
            if h_h is not None and h_a is not None:
                hf = h_h if is_home else h_a
                ha = h_a if is_home else h_h

        rows.append((gf, against, hf, ha))

    if not rows:
        return result

    result["matches"] = len(rows)
    result["wins"] = sum(1 for gf, ga, _, _ in rows if gf > ga)
    result["draws"] = sum(1 for gf, ga, _, _ in rows if gf == ga)
    result["losses"] = sum(1 for gf, ga, _, _ in rows if gf < ga)
    result["avg_goals_for"] = round(sum(r[0] for r in rows) / len(rows), 2)
    result["avg_goals_against"] = round(sum(r[1] for r in rows) / len(rows), 2)

    first_half_rows = [(r[2], r[3]) for r in rows if r[2] is not None and r[3] is not None]
    if first_half_rows:
        result["first_half_avg_for"] = round(
            sum(r[0] for r in first_half_rows) / len(first_half_rows), 2
        )
        result["first_half_avg_against"] = round(
            sum(r[1] for r in first_half_rows) / len(first_half_rows), 2
        )

    return result


def build_ai_note(
    home_team: str,
    away_team: str,
    home_form: Dict[str, Any],
    away_form: Dict[str, Any],
    comparison_distribution: List[Dict[str, Any]],
    opening_distribution: List[Dict[str, Any]],
    closing_distribution: List[Dict[str, Any]],
    opening_count: int = 0,
    closing_count: int = 0,
    comparison_count: int = 0,
) -> Dict[str, Any]:
    signals: List[str] = []

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

    if (
        home_form.get("first_half_avg_for") is not None
        and away_form.get("first_half_avg_against") is not None
    ):
        signals.append(
            f"{home_team} ilk yarı gol ortalaması "
            f"{home_form['first_half_avg_for']}; {away_team}'nin ilk yarı "
            f"gol yeme ortalaması {away_form['first_half_avg_against']}."
        )

    if (
        away_form.get("first_half_avg_for") is not None
        and home_form.get("first_half_avg_against") is not None
    ):
        signals.append(
            f"{away_team} ilk yarı gol ortalaması "
            f"{away_form['first_half_avg_for']}; {home_team}'in ilk yarı "
            f"gol yeme ortalaması {home_form['first_half_avg_against']}."
        )

    if comparison_distribution:
        top = _top(comparison_distribution)
        if top:
            text = ", ".join(
                f"{d['outcome']} %{d['percentage']}" for d in top
            )
            signals.append(
                f"İki tarihsel tablonun ortak maçlarında öne çıkan "
                f"İY/MS sonuçları: {text}."
            )
    else:
        opening_top = _top(opening_distribution)
        closing_top = _top(closing_distribution)
        if opening_top:
            text = ", ".join(
                f"{d['outcome']} %{d['percentage']}" for d in opening_top
            )
            signals.append(
                f"Açılış benzerlerinde öne çıkan İY/MS sonuçları: {text}."
            )
        if closing_top:
            text = ", ".join(
                f"{d['outcome']} %{d['percentage']}" for d in closing_top
            )
            signals.append(
                f"Güncel oran-kapanış karşılaştırmasında öne çıkan "
                f"İY/MS sonuçları: {text}."
            )

    # Güven puanı tarihsel örneklem büyüklüğünü açıkça dikkate alır.
    # Tek maç veya birkaç maç artık 90/100 üretemez.
    evidence_count = comparison_count or max(opening_count, closing_count)
    if comparison_count >= 20:
        confidence = 78.0
    elif comparison_count >= 10:
        confidence = 70.0
    elif comparison_count >= 5:
        confidence = 60.0
    elif comparison_count >= 1:
        confidence = 50.0
    elif opening_count >= 20 and closing_count >= 20:
        confidence = 65.0
    elif opening_count >= 10 and closing_count >= 10:
        confidence = 58.0
    elif opening_count >= 5 or closing_count >= 5:
        confidence = 50.0
    else:
        confidence = 35.0

    # Takım verisi mevcutsa küçük bir destek ver, ancak tarihsel veri
    # zayıfken bu destek güveni yüksek seviyeye taşımasın.
    if home_form.get("matches") and away_form.get("matches"):
        if (
            home_form.get("avg_goals_for") is not None
            and away_form.get("avg_goals_against") is not None
            and home_form["avg_goals_for"] > away_form["avg_goals_against"]
        ):
            confidence += 3
        if (
            away_form.get("avg_goals_for") is not None
            and home_form.get("avg_goals_against") is not None
            and away_form["avg_goals_for"] > home_form["avg_goals_against"]
        ):
            confidence += 3

    confidence = round(min(confidence, 85.0), 1)

    if not signals:
        note = (
            "Yeterli tarihsel benzerlik veya güncel takım verisi bulunamadığı "
            "için güçlü bir AI yorumu üretilemedi."
        )
    else:
        note = " ".join(signals)

    if evidence_count < 5:
        note += (
            " Tarihsel örneklem küçük olduğu için bu bulgu yalnızca "
            "gözlemsel karşılaştırma olarak değerlendirilmelidir."
        )

    return {
        "note": note,
        "confidence": confidence,
        "basis": [
            "geçmiş açılış oranı karşılaştırması",
            "geçmiş kapanış oranı karşılaştırması",
            "iki tablonun gerçek maç bazlı kesişimi",
            "güncel takım performansı",
            "ilk yarı performansı",
        ],
        "disclaimer": (
            "Güven puanı gerçekleşecek sonucu garanti etmez; "
            "tarihsel örneklem büyüklüğü ve mevcut verilerin birbirini "
            "destekleme düzeyini ifade eder."
        ),
    }
