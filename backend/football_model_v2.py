import math, random

def pois(l,k): return math.exp(-l)*l**k/math.factorial(k)
def dc(h,a,x,y,r=-.08):
    if h==0 and a==0:return 1-x*y*r
    if h==0 and a==1:return 1+x*r
    if h==1 and a==0:return 1+y*r
    if h==1 and a==1:return 1-r
    return 1.0
def matrix(x,y,n=8):
    m=[[pois(x,h)*pois(y,a)*dc(h,a,x,y) for a in range(n+1)] for h in range(n+1)]; z=sum(map(sum,m)); return [[v/z for v in row] for row in m]
def probs(m):
    one=sum(m[h][a] for h in range(len(m)) for a in range(len(m)) if h>a); draw=sum(m[i][i] for i in range(len(m))); two=1-one-draw
    over=sum(m[h][a] for h in range(len(m)) for a in range(len(m)) if h+a>=3); btts=sum(m[h][a] for h in range(1,len(m)) for a in range(1,len(m)))
    return {'1':one,'X':draw,'2':two,'over_2_5':over,'under_2_5':1-over,'btts_yes':btts,'btts_no':1-btts}
def elo_prob(h,a): return .5 if h is None or a is None else 1/(1+10**(-((float(h)+55-float(a))/400)))
def clamp(x,a,b): return max(a,min(b,x))
def build_model(c):
    h,a,L=c.get('home',{}),c.get('away',{}),c.get('league',{}); hs,as_=h.get('strength',{}),a.get('strength',{}); hf,af=h.get('recent_form',{}),a.get('recent_form',{})
    hg=float(L.get('home_goal_avg') or 1.35); ag=float(L.get('away_goal_avg') or 1.10); ha=float(hs.get('attack_strength') or 1); hd=float(hs.get('defence_weakness') or 1); aa=float(as_.get('attack_strength') or 1); ad=float(as_.get('defence_weakness') or 1)
    x=clamp(hg*ha*ad,.2,3.8); y=clamp(ag*aa*hd,.15,3.5)
    hp,ap=hf.get('points_per_game'),af.get('points_per_game')
    if hp is not None and ap is not None:
        d=clamp((float(hp)-float(ap)), -3,3); x*=1+clamp(d*.035,-.1,.1); y*=1-clamp(d*.025,-.08,.08)
    m=matrix(x,y); q=probs(m); ep=elo_prob(hs.get('elo'),as_.get('elo')); p1=.75*q['1']+.25*ep; p2=.75*q['2']+.25*(1-ep); px=max(.01,1-p1-p2); z=p1+px+p2; p1,px,p2=p1/z,px/z,p2/z
    hm=matrix(x*.44,y*.44,6); sm=matrix(x*.56,y*.56,6); iy={}
    for ht in range(7):
      for at in range(7):
       for sh in range(7):
        for sa in range(7):
         key=('1' if ht>at else 'X' if ht==at else '2')+'/'+('1' if ht+sh>at+sa else 'X' if ht+sh==at+sa else '2'); iy[key]=iy.get(key,0)+hm[ht][at]*sm[sh][sa]
    ex=[]
    for i,r in enumerate(m):
      for j,v in enumerate(r): ex.append((v,i,j))
    ex.sort(reverse=True)
    rng=random.Random((int(float(hs.get('elo') or 1500))*31+int(float(as_.get('elo') or 1500))*17)&0xffffffff); mc=[0,0,0]
    def rnd(l):
      t=math.exp(-l); k=0; u=1
      while u>t:k+=1;u*=rng.random()
      return k-1
    for _ in range(5000):
      u,v=rnd(x),rnd(y);mc[0 if u>v else 1 if u==v else 2]+=1
    return {'probabilities':{**{k:round(v*100,2) for k,v in {'1':p1,'X':px,'2':p2}.items()},**{k:round(v*100,2) for k,v in q.items() if k not in ('1','X','2')}},'expected_goals':{'home':round(x,3),'away':round(y,3)},'elo':{'home':hs.get('elo'),'away':as_.get('elo'),'home_win':round(ep*100,2)},'monte_carlo':{'n':5000,'1':round(mc[0]/50,2),'X':round(mc[1]/50,2),'2':round(mc[2]/50,2)},'first_half':{k:round(v*100,2) for k,v in probs(hm).items() if k in ('1','X','2')},'iyms':{'probabilities':{k:round(v*100,2) for k,v in sorted(iy.items(),key=lambda z:z[1],reverse=True)}},'exact_scores':[{'score':f'{h}-{a}','probability':round(v*100,2)} for v,h,a in ex[:8]],'method':'Elo + recency-weighted form + attack/defence + Poisson/Dixon-Coles + Monte Carlo + joint HT/FT','quality':'result-based model; expected goals are a goals-derived proxy, not player xG'}
