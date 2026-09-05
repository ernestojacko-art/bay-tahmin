"""BAY TAHMIN Football Intelligence Engine v0.5 HT/FT layer.
Delegates legacy data/transport helpers while replacing prediction assembly.
"""
from __future__ import annotations
import asyncio, importlib.util, json, math, random, re
from pathlib import Path

_BASE_PATH = Path(__file__).resolve().parent / "football_intelligence_agent.py"
_spec = importlib.util.spec_from_file_location("_bay_tahmin_base_engine", _BASE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load base engine: {_BASE_PATH}")
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

ENGINE = "BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE"
VERSION = "0.5.0"
dates = _base.dates
num = _base.num
isiy = _base.isiy
issur = _base.issur
market = _base.market
window = _base.window
day = _base.day
five = _base.five
build_match_context = _base.build_match_context


def _norm3(d):
    z = sum(d.values())
    return {k: (v / z if z else 1 / 3) for k, v in d.items()}


def _poisson(lam, k):
    return math.exp(-lam) * lam**k / math.factorial(k)


def _matrix(x, y, n=8):
    m = [[_poisson(x, h) * _poisson(y, a) for a in range(n + 1)] for h in range(n + 1)]
    r = -0.08
    for h in range(n + 1):
        for a in range(n + 1):
            if h == 0 and a == 0:
                m[h][a] *= 1 - x * y * r
            elif h == 0 and a == 1:
                m[h][a] *= 1 + x * r
            elif h == 1 and a == 0:
                m[h][a] *= 1 + y * r
            elif h == 1 and a == 1:
                m[h][a] *= 1 - r
    z = sum(map(sum, m))
    return [[v / z for v in row] for row in m]


def _result_probs(m):
    one = sum(m[h][a] for h in range(len(m)) for a in range(len(m)) if h > a)
    draw = sum(m[i][i] for i in range(len(m)))
    two = max(0.0, 1 - one - draw)
    return {"1": one, "X": draw, "2": two}


def _mc_probs(x, y, seed, n=5000):
    rng = random.Random(seed)
    out = [0, 0, 0]
    def sample(lam):
        threshold = math.exp(-lam)
        k, p = 0, 1.0
        while p > threshold:
            k += 1
            p *= rng.random()
        return k - 1
    for _ in range(n):
        h, a = sample(x), sample(y)
        out[0 if h > a else 1 if h == a else 2] += 1
    return {"1": out[0] / n, "X": out[1] / n, "2": out[2] / n}


def _form_probs(hf, af):
    hp, ap = hf.get("points_per_game"), af.get("points_per_game")
    if hp is None or ap is None:
        return {"1": 1 / 3, "X": 1 / 3, "2": 1 / 3}
    diff = max(-2.5, min(2.5, float(hp) - float(ap)))
    return _norm3({"1": max(.05, .43 + diff * .10), "X": .24, "2": max(.05, .33 - diff * .10)})


def _split_probs(hf, af):
    hp = (hf.get("home_split") or {}).get("points_per_game")
    ap = (af.get("away_split") or {}).get("points_per_game")
    if hp is None or ap is None:
        return {"1": 1 / 3, "X": 1 / 3, "2": 1 / 3}
    diff = max(-2.5, min(2.5, float(hp) - float(ap)))
    return _norm3({"1": .43 + diff * .11, "X": .24, "2": .33 - diff * .11})


def _elo_probs(e):
    return _norm3({"1": e * .76, "X": .24, "2": (1 - e) * .76})


def _weighted_first_half(c, full_x, full_y):
    hf = c["home"].get("recent_form", {}).get("first_half", {})
    af = c["away"].get("recent_form", {}).get("first_half", {})
    hgf, hga = hf.get("goals_for_avg"), hf.get("goals_against_avg")
    agf, aga = af.get("goals_for_avg"), af.get("goals_against_avg")
    if all(v is not None for v in (hgf, hga, agf, aga)):
        hx = max(.08, min(2.2, (float(hgf) + float(aga)) / 2))
        hy = max(.08, min(2.0, (float(agf) + float(hga)) / 2))
        source = "observed_first_half_5_10_20_weighted"
    else:
        hx, hy = full_x * .44, full_y * .44
        source = "fallback_44_percent_of_ft_model_due_to_missing_first_half_sample"
    return hx, hy, source


def _joint_ht_ft(ht, sh):
    joint = {f"{h}/{f}": 0.0 for h in ("1", "X", "2") for f in ("1", "X", "2")}
    for hi, hr in enumerate(ht):
        for hj, hp in enumerate(hr):
            for si, sr in enumerate(sh):
                for sj, sp in enumerate(sr):
                    ht_r = "1" if hi > hj else "X" if hi == hj else "2"
                    ft_r = "1" if hi + si > hj + sj else "X" if hi + si == hj + sj else "2"
                    joint[f"{ht_r}/{ft_r}"] += hp * sp
    z = sum(joint.values()) or 1.0
    return {k: v / z for k, v in joint.items()}


def _surprise_scores(iyms, market_probs=None):
    straight = {"1/1", "X/X", "2/2"}
    out = []
    for key, prob in iyms.items():
        if key in straight:
            continue
        market_p = (market_probs or {}).get(key)
        divergence = (prob * 100 - market_p) if market_p is not None else prob * 100
        out.append({"selection": key, "model_probability": round(prob * 100, 2), "market_probability": market_p, "divergence_points": round(divergence, 2)})
    return sorted(out, key=lambda x: (x["divergence_points"], x["model_probability"]), reverse=True)


def model(c):
    h, a, league = c["home"], c["away"], c["league"]
    hs, ass = h.get("strength", {}), a.get("strength", {})
    hf, af = h.get("recent_form", {}), a.get("recent_form", {})
    hg = float(league.get("home_goal_avg") or 1.35)
    ag = float(league.get("away_goal_avg") or 1.10)
    x = max(.2, min(3.8, hg * float(hs.get("attack_strength") or 1) * float(ass.get("defence_weakness") or 1)))
    y = max(.15, min(3.5, ag * float(ass.get("attack_strength") or 1) * float(hs.get("defence_weakness") or 1)))
    hp, ap = hf.get("points_per_game"), af.get("points_per_game")
    if hp is not None and ap is not None:
        d = max(-3, min(3, float(hp) - float(ap)))
        x *= 1 + max(-.1, min(.1, d * .035))
        y *= 1 - max(-.08, min(.08, d * .025))
    full = _matrix(x, y)
    full_probs = _result_probs(full)
    elo = .5 if hs.get("elo") is None or ass.get("elo") is None else 1 / (1 + 10 ** (-((float(hs["elo"]) + 55 - float(ass["elo"])) / 400)))
    seed = int(float(hs.get("elo") or 1500) * 31 + float(ass.get("elo") or 1500) * 17) & 0xffffffff
    mc = _mc_probs(x, y, seed)
    models = {"elo": _elo_probs(elo), "poisson_dixon_coles": full_probs, "form": _form_probs(hf, af), "home_away": _split_probs(hf, af), "monte_carlo": mc}
    weights = {"elo": .20, "poisson_dixon_coles": .25, "form": .20, "home_away": .15, "monte_carlo": .20}
    consensus = {k: 0.0 for k in ("1", "X", "2")}
    for name, probs in models.items():
        for k in consensus:
            consensus[k] += weights[name] * probs[k]
    consensus = {k: round(v * 100, 2) for k, v in consensus.items()}
    hx, hy, ht_source = _weighted_first_half(c, x, y)
    ht = _matrix(hx, hy, 6)
    second = _matrix(max(.08, x - hx), max(.08, y - hy), 6)
    ht_probs = _result_probs(ht)
    joint = _joint_ht_ft(ht, second)
    btts = sum(full[i][j] for i in range(1, len(full)) for j in range(1, len(full)))
    over25 = sum(full[i][j] for i in range(len(full)) for j in range(len(full)) if i + j >= 3)
    ex = sorted(((v, i, j) for i, row in enumerate(full) for j, v in enumerate(row)), reverse=True)[:8]
    return {
        "probabilities": {**consensus, "over_2_5": round(over25 * 100, 2), "under_2_5": round((1-over25)*100, 2), "btts_yes": round(btts*100, 2), "btts_no": round((1-btts)*100, 2)},
        "model_consensus": {"weights": {k: round(v*100) for k,v in weights.items()}, "models": {n: {k: round(v*100,2) for k,v in p.items()} for n,p in models.items()}, "consensus": consensus, "market_is_cross_check_only": True},
        "expected_goals": {"home": round(x,3), "away": round(y,3), "kind": "results_based_proxy"},
        "elo": {"home": hs.get("elo"), "away": ass.get("elo"), "home_win": round(elo*100,2), "source": hs.get("elo_source")},
        "monte_carlo": {"n": 5000, **{k: round(v*100,2) for k,v in mc.items()}},
        "first_half": {k: round(v*100,2) for k,v in ht_probs.items()},
        "first_half_model": {"independent": True, "source": ht_source, "expected_goals": {"home": round(hx,3), "away": round(hy,3)}, "observed_context": {"home": hf.get("first_half",{}), "away": af.get("first_half",{})}},
        "iyms": {"probabilities": {k: round(v*100,2) for k,v in sorted(joint.items(), key=lambda z:z[1], reverse=True)}, "surprise_candidates": _surprise_scores(joint)},
        "exact_scores": [{"score": f"{i}-{j}", "probability": round(v*100,2)} for v,i,j in ex],
        "method": "Transparent ensemble: Elo + weighted form + home/away + Poisson/Dixon-Coles + Monte Carlo; independent observed first-half model; HT x FT 9-cell matrix",
        "quality": "data-aware; no unavailable xG/injury/news fields are invented",
    }


async def cand(r):
    c = await build_match_context(r)
    return {"match": r, "context": c, "model": model(c), "markets": r.get("_markets") or []}


def _iyms_market_probs(markets):
    for m in markets:
        name = str(m.get("gameName") or m.get("type") or "").lower().replace(" ", "")
        if "iy/ms" in name or "iyms" in name or "ilkyarı/maçsonucu" in name or "ilkyarı-maçsonucu" in name:
            raw = {}
            for o in m.get("odds", []):
                try:
                    odd = float(o.get("odd")); value = str(o.get("value") or "")
                    if odd > 0: raw[value] = 1 / odd
                except Exception:
                    continue
            z = sum(raw.values())
            return {k: round(v/z*100,2) for k,v in raw.items()} if z else {}
    return {}


def choose(cs, msg):
    out=[]
    for c in cs:
        p=c["model"]["probabilities"]
        if isiy(msg):
            market_probs = _iyms_market_probs(c["markets"])
            for k,v in c["model"]["iyms"]["probabilities"].items():
                if issur(msg) and k in ("1/1","X/X","2/2"): continue
                bonus = max(0, v-market_probs.get(k, v))/10 if issur(msg) and market_probs else 0
                out.append((v + bonus, c, k))
        else:
            k=max(("1","X","2"), key=lambda x:p[x]); v=p[k]
            mk=market(c["markets"])
            if issur(msg) and mk and k!=max(mk,key=mk.get): v += 8
            out.append((v,c,k))
    out.sort(key=lambda z:z[0], reverse=True)
    seen=set(); result=[]
    for v,c,k in out:
        mid=str(c["match"].get("MatchID"))
        if mid in seen: continue
        seen.add(mid); result.append((v,c,k))
        if len(result)>=num(msg): break
    return result


def pack(sel):
    return [{"match_id":c["match"].get("MatchID"),"match":c["match"].get("Teams"),"kickoff":c["match"].get("KickoffUTC"),"selection":k,"probability":v,"model":c["model"],"home_form":c["context"]["home"]["recent_form"],"away_form":c["context"]["away"]["recent_form"],"home_standing":c["context"]["home"]["standing"],"away_standing":c["context"]["away"]["standing"],"h2h":c["context"].get("h2h",[]),"market_cross_check":market(c["markets"]),"data_quality":c["context"].get("data_quality",{}),"data_availability":c["context"].get("data_availability",{})} for v,c,k in sel]


async def answer(main,msg,history=None):
    ds=dates(msg); rows=[r for g in await asyncio.gather(*(day(d) for d in ds)) for r in g]
    if not rows: return {"reply":"İstenen tarihlerde doğrulanmış gerçek futbol maçı bulunamadı.","engine":ENGINE,"engine_version":VERSION}
    cs=await asyncio.gather(*(cand(r) for r in rows)); sel=choose(cs,msg); data=pack(sel)
    prompt=f'''Sen {ENGINE} sürüm {VERSION} profesyonel futbol istihbarat motorusun. Kullanıcı: {msg}\nDOSSIER:{json.dumps(data,ensure_ascii=False)}\nTahmin ana kaynağı şeffaf model konsensüsüdür. Piyasa yalnızca çapraz kontroldür. İY/MS için bağımsız ilk yarı modeli ve 9 hücreli HT/FT matrisini esas al. Verilmeyen bilgiyi uydurma. Türkçe yanıt ver. Kupon oluşturma.'''
    try: reply=await asyncio.wait_for(main.gemini_generate(prompt),timeout=8.0)
    except Exception:
        lines=[f"{x['match']} — {x['selection']} (%{round(float(x['probability']),1)})" for x in data]
        reply=f"{ENGINE} {len(cs)} maçı bağımsız HT/FT modeliyle değerlendirdi.\n\n"+"\n".join(f"{i+1}. {v}" for i,v in enumerate(lines))+"\n\nPiyasa yalnızca çapraz kontroldür; bu liste kupon değildir."
    return {"reply":reply,"engine":ENGINE,"engine_version":VERSION,"dates":[d.isoformat() for d in ds],"match_count":len(rows),"analyzed_count":len(cs),"source":"5DollarFootballAPI + transparent statistical ensemble"}


async def analyze_match(main,mid):
    p=await five._get(f"fixtures/{int(mid)}",{"lang":"en","include":"events,stats"}); f=p.get("data") or {}
    if not f:return {"analysis":{"mac_ozeti":"Maç bulunamadı."},"engine":ENGINE,"engine_version":VERSION}
    r=five._fixture_row(f); r["_markets"]=five._markets_from_odds({"data":{"odds":f.get("odds") or {}}},live=False); r["_stats"]=f.get("statistics") or {}; c=await cand(r); m=c["model"]; pr=m["probabilities"]; best=max(("1","X","2"),key=lambda x:pr[x]); score=m["exact_scores"][0]["score"] if m["exact_scores"] else None; names={"1":c["match"].get("Team1"),"X":"Beraberlik","2":c["match"].get("Team2")}
    analysis={"mac_ozeti":f"Model konsensüsü: {names[best]} önde. 1: %{pr['1']}, X: %{pr['X']}, 2: %{pr['2']}.","takimlarin_durumu":f"{c['context']['home']['name']} son 5 formu {c['context']['home']['recent_form']['last_5'].get('form','-')}, {c['context']['away']['name']} son 5 formu {c['context']['away']['recent_form']['last_5'].get('form','-')}.","olasi_senaryo":f"Gol modeli proxy'si {m['expected_goals']['home']} - {m['expected_goals']['away']}; en olası skor {score or 'belirsiz'}.","ms_tahmini":names[best],"kg_tahmini":"Var" if pr['btts_yes']>=50 else "Yok","alt_ust_tahmini":"Üst 2.5" if pr['over_2_5']>=50 else "Alt 2.5","ilk_yari_tahmini":max(m['first_half'],key=m['first_half'].get),"ht_ft_tahmini":max(m['iyms']['probabilities'],key=m['iyms']['probabilities'].get),"iy_ms_suprizleri":m['iyms']['surprise_candidates'][:6],"model_consensus":m['model_consensus'],"data_availability":c['context'].get('data_availability',{}),"risk_seviyesi":"düşük" if max(pr['1'],pr['X'],pr['2'])>=60 else "orta" if max(pr['1'],pr['X'],pr['2'])>=48 else "yüksek","tahmin_gerekcesi":f"{m['method']}. Piyasa yalnızca çapraz kontroldür. Veri kalitesi: {c['context'].get('data_quality',{}).get('level','unknown')}."}
    return {"analysis":analysis,"model":m,"context":c["context"],"engine":ENGINE,"engine_version":VERSION,"source":"5DollarFootballAPI + transparent statistical ensemble"}


async def match_answer(main,mid,msg,history=None):
    result=await analyze_match(main,mid)
    if result.get("analysis",{}).get("mac_ozeti")=="Maç bulunamadı.": return {"reply":"Maç bulunamadı.","engine":ENGINE}
    m=result["model"]; ctx=result["context"]; pr=m["probabilities"]; best=max(("1","X","2"),key=lambda x:pr[x]); names={"1":ctx["home"]["name"],"X":"Beraberlik","2":ctx["away"]["name"]}
    dossier={"match":f"{ctx['home']['name']} - {ctx['away']['name']}","consensus":m["model_consensus"],"probabilities":pr,"expected_goals":m["expected_goals"],"first_half":m["first_half"],"first_half_model":m["first_half_model"],"iyms":m["iyms"],"exact_scores":m["exact_scores"],"home_form":ctx["home"]["recent_form"],"away_form":ctx["away"]["recent_form"],"standing":{"home":ctx["home"]["standing"],"away":ctx["away"]["standing"]},"h2h":ctx.get("h2h",[]),"data_availability":ctx.get("data_availability",{}),"data_quality":ctx.get("data_quality",{})}
    prompt=f'''Sen {ENGINE} maç özel uzmanısın. Kullanıcı: {msg}\nDOSSIER:{json.dumps(dossier,ensure_ascii=False)}\nYalnızca bu maçı analiz et. Bağımsız ilk yarı modeli ile HT/FT 9 hücreli matrisi kullan. Model konsensüsü ile piyasa bilgisini karıştırma. Eksik veriyi açıkça belirt. Türkçe, kanıta dayalı yanıt ver; kupon oluşturma.'''
    try: reply=await asyncio.wait_for(main.gemini_generate(prompt),timeout=8.0)
    except Exception:
        iy=max(m["iyms"]["probabilities"],key=m["iyms"]["probabilities"].get)
        surprises=", ".join(x["selection"] for x in m["iyms"]["surprise_candidates"][:3]) or "yeterli ayrışma yok"
        reply=(f"{dossier['match']} için {ENGINE} analizi:\n\n• Model konsensüsü: 1 %{pr['1']} | X %{pr['X']} | 2 %{pr['2']}\n• Öne çıkan sonuç: {names[best]}\n• Gol modeli proxy'si: {m['expected_goals']['home']} - {m['expected_goals']['away']}\n• İlk yarı: 1 %{m['first_half']['1']} | X %{m['first_half']['X']} | 2 %{m['first_half']['2']}\n• En güçlü İY/MS: {iy}\n• İY/MS sürpriz adayları: {surprises}\n• İlk yarı modeli: bağımsız, kaynak={m['first_half_model']['source']}\n\nModel ayrıntıları gerçek analiz context'inden üretilmiştir; veri bulunmayan alanlar uydurulmamıştır.")
    return {"reply":reply,"match_id":str(mid),"engine":ENGINE,"engine_version":VERSION,"analysis_context":dossier,"source":"5DollarFootballAPI + transparent statistical ensemble"}
