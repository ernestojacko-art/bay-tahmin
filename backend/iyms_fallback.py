import math


def _pmf(lam, k):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _result_probs(home_lambda, away_lambda, max_goals=8):
    hp=[_pmf(home_lambda,k) for k in range(max_goals+1)]
    ap=[_pmf(away_lambda,k) for k in range(max_goals+1)]
    home=sum(hp[h]*ap[a] for h in range(max_goals+1) for a in range(max_goals+1) if h>a)
    draw=sum(hp[h]*ap[a] for h in range(max_goals+1) for a in range(max_goals+1) if h==a)
    return {"1":home,"X":draw,"2":max(0.0,1-home-draw)}


def estimate_half(full_probs):
    best=None
    # Coarse fit is intentional: keep chat latency low while matching the real FT 1X2 snapshot.
    for hi in range(5,61):
        lh=hi/20
        for ai in range(4,51):
            la=ai/20
            p=_result_probs(lh,la)
            err=sum((p[k]-full_probs.get(k,0))**2 for k in ("1","X","2"))
            if best is None or err<best[0]: best=(err,lh,la)
    if best is None: return {}
    return _result_probs(best[1]*0.45,best[2]*0.45)


def build(item, market_probability, find_market, surprise=False):
    full=find_market(item,"1x2")
    if not full: return []
    fp=market_probability(full.get("odds",[]))
    if not fp: return []
    half=find_market(item,"1x2_half")
    if half:
        hp=market_probability(half.get("odds",[]))
        basis="Gerçek 5Dollar Bet365 İlk Yarı 1X2 + Maç Sonucu 1X2"
    else:
        hp=estimate_half(fp)
        basis="Gerçek 5Dollar Bet365 Maç Sonucu 1X2 + Agent Poisson İlk Yarı projeksiyonu"
    if not hp: return []
    combos=[]
    for h in ("1","X","2"):
        for f in ("1","X","2"):
            p=hp.get(h,0)*fp.get(f,0)
            if not p: continue
            value=f"{h}/{f}"
            if surprise and value in {"1/1","2/2","X/X"}: continue
            combos.append({"selection":value,"prob":p,"odd":round(1/p,2),"basis":basis})
    total=sum(x["prob"] for x in combos)
    for x in combos: x["prob_norm"]=x["prob"]/total if total else 0
    return sorted(combos,key=lambda x:x["prob_norm"],reverse=True)
