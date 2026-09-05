from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import Any
CORE_PATH=Path(__file__).resolve().parent/"football_intelligence_agent_v6_core.py"
spec=importlib.util.spec_from_file_location("_bay_v6_core",CORE_PATH)
if spec is None or spec.loader is None: raise ImportError(CORE_PATH)
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
ENGINE,VERSION=core.ENGINE,"1.3.0"
def _n(v):
 try:return float(v) if v is not None else None
 except:return None
def _second(form:dict[str,Any]):
 f=form.get("first_half") or {};g=(form.get("goals_for_avg"),form.get("goals_against_avg"));h=(f.get("goals_for_avg"),f.get("goals_against_avg"))
 if all(_n(x) is not None for x in (*g,*h)):return {"goals_for_avg":max(0.,_n(g[0])-_n(h[0])),"goals_against_avg":max(0.,_n(g[1])-_n(h[1])),"source":"team_observed_full_minus_first_half_history"}
 return None
def _team_second(c,s):return _second(((c.get(s) or {}).get("recent_form") or {}))
def _matrix(x,y,n=8,rho=-.08):
 import math
 def p(l,k):return math.exp(-l)*l**k/math.factorial(k)
 def t(i,j):return 1-x*y*rho if(i,j)==(0,0) else 1+x*rho if(i,j)==(0,1) else 1+y*rho if(i,j)==(1,0) else 1-rho if(i,j)==(1,1) else 1
 m=[[max(0.,p(max(.01,x),i)*p(max(.01,y),j)*t(i,j)) for j in range(n+1)] for i in range(n+1)];z=sum(map(sum,m)) or 1;return [[v/z for v in q] for q in m]
def _rp(m):
 one=sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if i>j);d=sum(m[i][i] for i in range(len(m)));return {"1":one,"X":d,"2":max(0.,1-one-d)}
def _joint(a,b):
 o={f"{h}/{f}":0. for h in R for f in R}
 for hi,q in enumerate(a):
  for hj,hp in enumerate(q):
   h="1" if hi>hj else "X" if hi==hj else "2"
   for si,sq in enumerate(b):
    for sj,sp in enumerate(sq):
     f="1" if hi+si>hj+sj else "X" if hi+si==hj+sj else "2";o[f"{h}/{f}"]+=hp*sp
 z=sum(o.values()) or 1;return {k:v/z for k,v in o.items()}
R=("1","X","2")
def model(context):
 r=core.model(context);h=_team_second(context,"home");a=_team_second(context,"away")
 if h and a:
  fh=(r.get("first_half_model") or {}).get("expected_goals") or {};hx,ax=_n(fh.get("home")),_n(fh.get("away"))
  if hx is not None and ax is not None:
   sx=(h["goals_for_avg"]+a["goals_against_avg"])/2;sy=(a["goals_for_avg"]+h["goals_against_avg"])/2;hm,sm=_matrix(hx,ax),_matrix(sx,sy);j=_joint(hm,sm)
   r["first_half"]={k:round(v*100,2) for k,v in _rp(hm).items()};r["iyms"]={"probabilities":{k:round(v*100,2) for k,v in sorted(j.items(),key=lambda z:z[1],reverse=True)},"top":max(j,key=j.get),"source":"team-specific independent HT × 2H model","surprise_candidates":[{"selection":k,"model_probability":round(v*100,2)} for k,v in sorted(j.items(),key=lambda z:z[1],reverse=True) if k not in {"1/1","X/X","2/2"}][:5]};r["second_half_model"]={"independent":True,"source":"team_observed_full_minus_first_half_history","expected_goals":{"home":round(sx,3),"away":round(sy,3)}};r["htft_model"]={"independent_first_half":True,"independent_second_half":True,"joint_method":"team-specific Dixon-Coles HT × 2H","all_9_outcomes":True}
 return r
async def cand(row:dict[str,Any],*,track:bool=True):
 c=await core._safe_context(row);r={"match":row,"context":c,"model":model(c),"markets":row.get("_markets") or []}
 if track:
  try:
   from prediction_tracking import track_predictions;track_predictions(row,r["model"])
  except Exception:pass
 return r
for obj in (core.v5.v4.v3,core.v5.v4.v3.v2):obj.cand=cand;obj.model=model
dates,num,isiy,issur,market,window,day,five=core.dates,core.num,core.isiy,core.issur,core.market,core.window,core.day,core.five
resolve_finished_match,performance_summary=core.resolve_finished_match,core.performance_summary
async def analyze_match(main,mid):return await core.v5.v4.v3.analyze_match(main,mid)
async def match_answer(main,mid,msg,history=None):return await core.v5.v4.match_answer(main,mid,msg,history or [])
async def answer(main,message,history=None):return await core.v5.v4._impl.v3.answer(main,message,history or [])
