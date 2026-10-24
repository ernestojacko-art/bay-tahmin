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


def _num(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def _team_sentence(p: TeamStrengthProfile, at_home: bool, role: str) -> str | None:
    gf = p.home_goals_for if at_home else p.away_goals_for
    ga = p.home_goals_against if at_home else p.away_goals_against
    n = p.home_sample if at_home else p.away_sample
    if gf is not None and ga is not None and n > 0:
        where = "iç saha" if at_home else "deplasman"
        return (f"{role} {p.team_name} son {n} {where} maçında maç başına ortalama {_num(gf)} gol attı, "
                f"{_num(ga)} gol yedi.")
    gf, ga, n = p.goals_for_avg, p.goals_against_avg, p.matches_considered
    if gf is None or ga is None or n <= 0:
        return None
    return f"{role} {p.team_name} son {n} maçında maç başına ortalama {_num(gf)} gol attı, {_num(ga)} gol yedi."


def _h2h_sentence(home: TeamStrengthProfile, h2h) -> str:
    if not h2h:
        return "İki takımın yakın geçmişte oynadığı kayıtlı bir maç bulunamadı."
    w = d = l = 0
    for m in h2h:
        if m.goals_for > m.goals_against:
            w += 1
        elif m.goals_for == m.goals_against:
            d += 1
        else:
            l += 1
    return (f"İki takımın son {len(h2h)} karşılaşmasında {home.team_name} {w} galibiyet, {d} beraberlik, "
            f"{l} mağlubiyet aldı.")


def build_rationale(
    home: TeamStrengthProfile,
    away: TeamStrengthProfile,
    xg: ExpectedGoals,
    raw: OneXTwoProbabilities,
    final: OneXTwoProbabilities,
    market: OneXTwoProbabilities | None,
    weight: float,
    h2h=None,
) -> str:
    """Üyelerin kolay anlayacağı, sade Türkçe gerekçe. Yalnızca gerçek verilerden üretilir."""
    parts: list[str] = []
    bits = [x for x in (_team_sentence(home, True, "Ev sahibi"), _team_sentence(away, False, "Deplasman takımı")) if x]
    if bits:
        parts.append(" ".join(bits))
        parts.append(
            f"Bu rakamlara göre maçta yaklaşık {_num(xg.home_xg)} - {_num(xg.away_xg)} gol bekleniyor."
        )
    else:
        parts.append("Bu maç için yeterli gol verisi bulunamadı, bu yüzden analiz lig ortalamalarına dayanıyor.")
    parts.append(_h2h_sentence(home, h2h))

    if market is not None:
        names = [(home.team_name, final.home_win, market.home_win, raw.home_win),
                 ("Beraberlik", final.draw, market.draw, raw.draw),
                 (away.team_name, final.away_win, market.away_win, raw.away_win)]
        mkt_fav = max(names, key=lambda r: r[2])
        fin_fav = max(names, key=lambda r: r[1])
        parts.append(
            f"Bahis şirketlerinin en çok güvendiği sonuç: {mkt_fav[0]} ({_pct(mkt_fav[2])}). "
            f"Sadece istatistiklere bakınca bu sonucun oranı {_pct(mkt_fav[3])} çıkıyor."
        )
        parts.append(
            f"İstatistikler tek başına yanıltabileceği için iki görüşü birleştirdik "
            f"(istatistiğe {_pct(weight)}, bahis oranlarına {_pct(1 - weight)} ağırlık verdik). "
            f"Birleşik sonuçlar: {names[0][0]} {_pct(names[0][1])}, Beraberlik {_pct(names[1][1])}, "
            f"{names[2][0]} {_pct(names[2][1])}."
        )
        gap = mkt_fav[2] - mkt_fav[1]
        if gap >= SURPRISE_GAP:
            parts.append(
                f"Dikkat: Bahis şirketleri ile bizim analizimiz arasında hâlâ {gap * 100:.0f} puanlık fark var. "
                "Bu, maçta sürpriz çıkma ihtimalinin biraz yüksek olduğunu gösterir. "
                "Yine de kesin sonuç değildir; sakatlık ve kadro gibi bilgiler analize dahil değildir."
            )
    return " ".join(parts)
