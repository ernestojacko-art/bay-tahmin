"""
Model + piyasa dengeleme ve gerekçe üretimi.

Ham istatistiksel model, özellikle az maç verisi / xG yokken uç sonuçlar üretebilir.
Piyasa oranları ise binlerce bilginin (sakatlık, kadro, para akışı) damıtılmış hâlidir.
Bu yüzden nihai olasılık, modelin VERİ GÜVENİLİRLİĞİNE göre ağırlıklandırılmış bir
karışımdır: örneklem büyüdükçe model ağırlığı artar, azaldıkça piyasaya yaklaşılır.
Hiçbir sayı uydurulmaz; her cümle gerçek profil / oran verisinden üretilir.
"""
from __future__ import annotations

from app.schemas.prediction import ExpectedGoals, OneXTwoProbabilities
from app.schemas.team import TeamStrengthProfile

MIN_MODEL_WEIGHT = 0.20
MAX_MODEL_WEIGHT = 0.60
FULL_CREDIBILITY_MATCHES = 20
SURPRISE_GAP = 0.08


def model_weight(home: TeamStrengthProfile, away: TeamStrengthProfile) -> float:
    n = min(home.matches_considered, away.matches_considered)
    credibility = max(0.0, min(1.0, n / FULL_CREDIBILITY_MATCHES))
    return round(MIN_MODEL_WEIGHT + (MAX_MODEL_WEIGHT - MIN_MODEL_WEIGHT) * credibility, 3)


def blend_with_market(
    raw: OneXTwoProbabilities, market: OneXTwoProbabilities | None, weight: float
) -> OneXTwoProbabilities:
    if market is None:
        return raw
    h = weight * raw.home_win + (1 - weight) * market.home_win
    d = weight * raw.draw + (1 - weight) * market.draw
    a = weight * raw.away_win + (1 - weight) * market.away_win
    t = h + d + a or 1.0
    return OneXTwoProbabilities(home_win=round(h / t, 4), draw=round(d / t, 4), away_win=round(a / t, 4))


def _pct(x: float) -> str:
    return f"%{x * 100:.0f}"


def _scoring_line(p: TeamStrengthProfile, at_home: bool) -> str | None:
    gf = p.home_goals_for if at_home else p.away_goals_for
    ga = p.home_goals_against if at_home else p.away_goals_against
    n = p.home_sample if at_home else p.away_sample
    where = "iç sahada" if at_home else "deplasmanda"
    if gf is None or ga is None or n <= 0:
        gf, ga, n, where = p.goals_for_avg, p.goals_against_avg, p.matches_considered, "son maçlarda"
    if gf is None or ga is None or n <= 0:
        return None
    return f"{p.team_name} {where} ({n} maç) maç başı {gf:.1f} gol atıp {ga:.1f} yiyor"


def build_rationale(
    home: TeamStrengthProfile,
    away: TeamStrengthProfile,
    xg: ExpectedGoals,
    raw: OneXTwoProbabilities,
    final: OneXTwoProbabilities,
    market: OneXTwoProbabilities | None,
    weight: float,
) -> str:
    parts: list[str] = []
    hl, al = _scoring_line(home, True), _scoring_line(away, False)
    stats_bits = [x for x in (hl, al) if x]
    if stats_bits:
        parts.append("Veriler: " + "; ".join(stats_bits) + f". Beklenen gol {xg.home_xg:.2f} - {xg.away_xg:.2f}.")
    else:
        parts.append("Bu maç için yeterli gol verisi yok; analiz lig ortalamalarına yaslanıyor.")

    if market is not None:
        names = [(home.team_name, final.home_win, market.home_win, raw.home_win),
                 ("Beraberlik", final.draw, market.draw, raw.draw),
                 (away.team_name, final.away_win, market.away_win, raw.away_win)]
        mkt_fav = max(names, key=lambda r: r[2])
        fin_fav = max(names, key=lambda r: r[1])
        parts.append(
            f"Piyasa en çok {mkt_fav[0]} sonucunu {_pct(mkt_fav[2])} ile fiyatlıyor; istatistiksel model tek başına "
            f"{mkt_fav[0]} için {_pct(mkt_fav[3])} veriyor."
        )
        n_min = min(home.matches_considered, away.matches_considered)
        parts.append(
            f"Ham modele tek başına güvenmemek için sonuç, model (%{weight*100:.0f}) ve "
            f"piyasa (%{(1-weight)*100:.0f}) karışımıyla dengelendi (iki takımdan en az {n_min} maçlık veri; veri azaldıkça piyasa ağırlığı artar)."
        )
        gap = mkt_fav[2] - mkt_fav[1]
        if gap >= SURPRISE_GAP:
            parts.append(
                f"Dengeye rağmen {mkt_fav[0]} için piyasa modelden {gap * 100:.0f} puan daha yüksek; "
                "bu fark sürpriz ihtimalinin kaynağıdır, sonucu garanti etmez."
            )
        elif fin_fav[0] != mkt_fav[0]:
            parts.append(f"Dengeli analiz {fin_fav[0]} tarafını hafif önde görüyor; fark küçük.")
    return " ".join(parts)
