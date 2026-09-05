from __future__ import annotations
import math,sys
R=("1","X","2");HTFT=tuple(f"{h}/{f}" for h in R for f in R)
def n(v):
 try:x=float(v);return x if math.isfinite(x) else None
 except:return None
def dc(x,y,i,j,r=-.08):
 if(i,j)==(0,0):return 1-x*y*r
 if(i,j)==(0,1):return 1+x*r
 if(i,j)==(1,0):return 1+y*r
 if(i,j)==(1,1):return 1-r
 return 1
def mat(x,y,z=8):
 def p(l,k):return math.exp(-l)*l**k/math.factorial(k)
 m=[[max(0,p(max(.01,x),i)*p(max(.01,y),j)*dc(x,y,i,j)) for j in range(z+1)] for i in range(z+1)];q=sum(map(sum,m)) or 1;return [[v/q for v in row] for row in m]
def rp(m):
 a=sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if i>j);d=sum(m[i][i] for i in range(len(m)));return {"1":a,"X":d,"2":max(0,1-a-d)}
def joint(a,b):
 o={k:0. for k in HTFT}
 for hi,r in enumerate(a):
  for hj,hp in enumerate(r):
   h="1" if hi>hj else "X" if hi==hj else "2"
   for si,sr in enumerate(b):
    for sj,sp in enumerate(sr):
     f="1" if hi+si>hj+sj else "X" if hi+si==hj+sj else "2";o[f"{h}/{f}"]+=hp*sp
 q=sum(o.values()) or 1;return {k:v/q for k,v in o.items()}
def avg(*v):
 q=[x for x in (n(x) for x in v) if x is not None];return sum(q)/len(q) if q else None
def team_half(c,side,second):
 f=((c.get(side) or {}).get("recent_form") or {});h=f.get("first_half") or {}
 if second:
  vals=(f.get("goals_for_avg"),f.get("goals_against_avg"),h.get("goals_for_avg"),h.get("goals_against_avg"))
  if all(n(x) is not None for x in vals):return max(.01,n(vals[0])-n(vals[2])),max(.01,n(vals[1])-n(vals[3]))
 x=f.get("first_half") or {};vals=(x.get("goals_for_avg"),x.get("goals_against_avg"));return vals if all(n(v) is not None for v in vals) else None
def goal_prior(m,c):
 for k in ("goal_model","score_model","poisson","score_projection"):
  b=m.get(k)
  if isinstance(b,dict) and str(b.get("kind") or "").lower() not in {"proxy","fallback","results_based_proxy"}:
   e=b.get("expected_goals") or b.get("lambda") or b.get("lambdas")
   if isinstance(e,dict) and n(e.get("home")) is not None and n(e.get("away")) is not None:return n(e["home"]),n(e["away"])
 h=(c.get("home") or {}).get("recent_form") or {};a=(c.get("away") or {}).get("recent_form") or {};vals=(h.get("goals_for_avg"),h.get("goals_against_avg"),a.get("goals_for_avg"),a.get("goals_against_avg"))
 if all(n(v) is not None for v in vals) and n(h.get("sample") or 0)>0 and n(a.get("sample") or 0)>0:return avg(vals[0],vals[3]),avg(vals[2],vals[1])
 return None
def reconcile(m,c=None):
 m=dict(m or {});c=c or {};w=list(m.get("prediction_warnings") or []);g=goal_prior(m,c);sm=None
 if g:
  sm=mat(*g);p=rp(sm);i,j,top=max(((i,j,v) for i,row in enumerate(sm) for j,v in enumerate(row)),key=lambda x:x[2]);m["goal_expectancy"]={"home":round(g[0],3),"away":round(g[1],3),"source":"pre-match model/team-form prior"};m["score_distribution"]={f"{i}-{j}":round(v*100,4) for i,row in enumerate(sm) for j,v in enumerate(row) if v>=.001};m["predicted_score"]=f"{i}-{j}";m["predicted_score_probability"]=round(top*100,2);m["ms"]=max(p,key=p.get);m["ms_probabilities"]={k:round(v*100,2) for k,v in p.items()};b=sum(sm[i][j] for i in range(1,9) for j in range(1,9));o=sum(sm[i][j] for i in range(9) for j in range(9) if i+j>=3);m["btts_probabilities"]={"Var":round(b*100,2),"Yok":round((1-b)*100,2)};m["btts"]="Var" if b>=.5 else "Yok";m["ou_2_5"]={"Alt":round((1-o)*100,2),"Üst":round(o*100,2)}
 else:w.append("Güvenilir maç-gol öncülü yok; ortak skor modeli üretilmedi")
 fh,sh=team_half(c,"home",False),team_half(c,"away",False);f2,s2=team_half(c,"home",True),team_half(c,"away",True)
 if fh and sh and f2 and s2:
  hm=mat((n(fh[0])+n(sh[1]))/2,(n(sh[0])+n(fh[1]))/2);sm2=mat((n(f2[0])+n(s2[1]))/2,(n(s2[0])+n(f2[1]))/2);ht=rp(hm);j=joint(hm,sm2);top_ht=max(ht,key=ht.get);global_top=max(j,key=j.get);recommended=max((k,v) for k,v in j.items() if k.startswith(top_ht+"/"));m["first_half"]={k:round(v*100,2) for k,v in ht.items()};m["iyms"]={"probabilities":{k:round(v*100,2) for k,v in sorted(j.items(),key=lambda z:z[1],reverse=True)},"top":recommended[0],"global_top":global_top,"source":"team-specific independent HT × 2H joint model","surprise_candidates":[{"selection":k,"model_probability":round(v*100,2)} for k,v in sorted(j.items(),key=lambda z:z[1],reverse=True) if k not in {"1/1","X/X","2/2"}][:5]};m["htft_model"]={"independent_first_half":True,"independent_second_half":True,"joint_method":"team-specific Dixon-Coles HT × 2H","recommended_top_constrained_to_top_HT":True}
 else:w.append("Bağımsız takım bazlı İY ve 2Y öncülleri birlikte mevcut değil; İY/MS joint tahmini üretilmedi")
 if w:m["prediction_warnings"]=list(dict.fromkeys(w))
 a=c.get("data_availability") or {};cov=sum(bool(a.get(k)) for k in ("xg","shots","shots_on_target","possession","corners","cards","first_half_goals","second_half_goals","goal_timing"))/9;m["data_quality"]={"level":"high" if cov>=.78 else "medium" if cov>=.45 else "low","coverage":round(cov*100,1),"consistency_validated":True};m["prediction_consistency"]={"validated":True,"score_ft_linked":bool(sm),"htft_linked":bool(fh and sh and f2 and s2)};return m
def install(impl):
 o=getattr(impl,"cand",None)
 if o is not None and not getattr(o,"_consistency_guard",False):
  async def g(row,**kw):r=await o(row,**kw);r["model"]=reconcile(r.get("model") or {},r.get("context") or {});return r
  g._consistency_guard=True;impl.cand=g
  for x in (getattr(getattr(getattr(impl,"v5",None),"v4",None),"v3",None),getattr(getattr(getattr(getattr(impl,"v5",None),"v4",None),"v3",None),"v2",None)):
   if x is not None:x.cand=g
