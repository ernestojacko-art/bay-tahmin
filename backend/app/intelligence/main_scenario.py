"""Ana senaryo: maç sonucu, olası skor ve İY/MS TEK bir zincirden üretilir, birbiriyle çelişmez.

Kural (kullanıcı talebi):
1. Yüzdeler farklıysa (en yüksek iki sonuç arasında >= CLOSE_GAP): ana sonuç en yüksek ihtimalli sonuçtur.
   Skor, bu sonuca uyan skorlar içinden en olası olanıdır.
2. Yüzdeler birbirine yakınsa (fark < CLOSE_GAP): en tepeye yakın sonuçlar arasından, skor matrisinde
   en olası skor belirleyicidir (ör. 33/35/32 -> 1-1 -> beraberlik).
3. İY/MS: ana sonuç = MS; İY, seçilen skorla UYUMLU devre sonuçları içinden (gollerin ilk yarıya düşme
   olasılığına göre) en olası olandır. Skor 1-1 ise X/X, 1-0 ise genelde X/1, 2-0 ise 1/1 gibi.
"""
from __future__ import annotations

from math import comb
from typing import Optional

from pydantic import BaseModel

from app.intelligence.statistical_models import FIRST_HALF_GOAL_SHARE
from app.schemas.prediction import OneXTwoProbabilities, ScoreProbability

CLOSE_GAP = 0.05


class MainScenario(BaseModel):
    outcome: str                  # "1" | "X" | "2"
    outcome_label: str            # takım adı ya da "Beraberlik"
    score_home: int
    score_away: int
    score_probability: float      # tek başına bu skorun olasılığı
    htft: str                     # örn. "X/1"
    htft_given_score: float       # bu skor olursa bu devre sonucunun olasılığı
    clarity: str                  # "net" | "hafif" | "dengeli"
    summary: str                  # kullanıcıya gösterilen tek-paragraf özet


def _outcome_of(h: int, a: int) -> str:
    return "1" if h > a else "X" if h == a else "2"


def _ht_probs(h: int, a: int) -> dict[str, float]:
    s = FIRST_HALF_GOAL_SHARE
    out = {"1": 0.0, "X": 0.0, "2": 0.0}
    for x in range(h + 1):
        px = comb(h, x) * s ** x * (1 - s) ** (h - x)
        for y in range(a + 1):
            py = comb(a, y) * s ** y * (1 - s) ** (a - y)
            out[_outcome_of(x, y)] += px * py
    return out


def _pct(p: float) -> str:
    return f"%{p * 100:.0f}"


def build_main_scenario(
    one_x_two: OneXTwoProbabilities,
    score_matrix: list[ScoreProbability],
    home_name: str,
    away_name: str,
) -> Optional[MainScenario]:
    if not score_matrix:
        return None
    probs = {"1": one_x_two.home_win, "X": one_x_two.draw, "2": one_x_two.away_win}
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top_p = ranked[0]
    gap = top_p - ranked[1][1]

    if gap >= CLOSE_GAP:
        candidates = {top_key}
        clarity = "net" if top_p >= 0.55 else "hafif"
    else:
        candidates = {k for k, p in ranked if p >= top_p - CLOSE_GAP}
        clarity = "dengeli"

    pool = [s for s in score_matrix if _outcome_of(s.home_goals, s.away_goals) in candidates]
    if not pool:
        return None
    best = max(pool, key=lambda s: s.probability)
    h, a = best.home_goals, best.away_goals
    outcome = _outcome_of(h, a)

    ht = _ht_probs(h, a)
    ht_key = max(ht, key=ht.get)
    htft = f"{ht_key}/{outcome}"

    names = {"1": home_name, "X": "Beraberlik", "2": away_name}
    label = names[outcome]
    ht_text = {
        "1": f"ilk yarıyı {home_name} önde kapatır",
        "X": "ilk yarı berabere biter",
        "2": f"ilk yarıyı {away_name} önde kapatır",
    }[ht_key]
    ms_text = {"1": f"{home_name} kazanır", "X": "maç berabere biter", "2": f"{away_name} kazanır"}[outcome]

    pcts = f"{_pct(probs['1'])} / {_pct(probs['X'])} / {_pct(probs['2'])}"
    others = [f"{names[k]} {_pct(p)}" for k, p in ranked if k != outcome and p >= 0.2]
    if clarity == "net":
        lead = f"{label if outcome != 'X' else 'Beraberlik'} bu maçta belirgin öne çıkıyor ({_pct(probs[outcome])})."
    elif clarity == "hafif":
        lead = (f"{label} hafif önde ({_pct(probs[outcome])}); " + (", ".join(others) + " da ciddi ihtimal." if others else "fark küçük."))
    else:
        close_names = [names[k] for k, p in ranked if k != outcome and p >= top_p - CLOSE_GAP]
        extra = f" {', '.join(close_names)} da hemen arkasında." if close_names else ""
        lead = f"Dengeli maç: sonuçlar birbirine çok yakın ({pcts}).{extra}"
    summary = (
        f"{lead} Ana senaryo: {ms_text}, skor {h}-{a}, {ht_text} (İY/MS {htft}). "
        f"Tek bir skorun gerçekleşme ihtimali her zaman düşüktür; {h}-{a} için %{best.probability * 100:.0f}."
    )
    return MainScenario(
        outcome=outcome, outcome_label=label, score_home=h, score_away=a,
        score_probability=round(best.probability, 4), htft=htft,
        htft_given_score=round(ht[ht_key], 4), clarity=clarity, summary=summary,
    )
