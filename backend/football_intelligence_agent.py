"""BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE entrypoint."""
from __future__ import annotations
import asyncio, json, math, random, re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import five_dollar_bridge as five
from football_intelligence_data import build_match_context

TZ=ZoneInfo('Europe/Istanbul'); ENGINE='BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE'; VERSION='0.4.0'
def dates(s):
 t=str(s or '').lower(); n=datetime.now(TZ).date(); o=[]
 if 'yarın' in t or 'yarin' in t:o.append(n+timedelta(days=1))
 if 'bugün' in t or 'bugun' in t:o.append(n)
 for w,k in [('cumartesi',5),('pazar',6)]:
  if w in t:o.append(n+timedelta((k-n.weekday())%7))
 m=re.search(r'(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?',t)
 if m:
  y=int(m.group(3) or n.year); y+=2000 if y<100 else 0
  try:o.append(date(y,int(m.group(2)),int(m.group(1))))
  except ValueError:pass
 return sorted(set(o or [n]))
def num(s):
 m=re.search(r'\b(\d{1,2})\s*(?:maç|adet|tane)\b',str(s).lower()); return max(1,min(int(m.group(1)),20)) if m else 5
def isiy(s):return bool(re.search(r'iy\s*/?\s*ms|ilk\s*yari.*mac\s*sonucu|ilk\s*yarı.*maç\s*sonucu',str(s).lower()))
def issur(s):return 'sürpriz' in str(s).lower() or 'surpriz' in str(s).lower()
def pois(l,k):return math.exp(-l)*l**k/math.factorial(k)
def mat(x,y,n=8):
 m=[[pois(x,h)*pois(y,a) for a in range(n+1)] for h in range(n+1)]; r=-.08
 for h in range(n+1):
  for a in range(n+1):
   if h==0 and a==0:m[h][a]*=1-x*y*r
   elif h==0 and a==1:m[h][a]*=1+x*r
   elif h==1 and a==0:m[h][a]*=1+y*r
   elif h==1 and a==1:m[h][a]*=1-r
 z=sum(map(sum,m)); return [[v/z for v in row] for row in m]
def mprob(m):
 one=sum(m[h][a] for h in range(len(m)) for a in range(len(m)) if h>a); d=sum(m[i][i] for i in range(len(m))); two=1-one-d
 ov=sum(m[h][a] for h in range(len(m)) for a in range(len(m)) if h+a>=3); bt=sum(m[h][a] for h in range(1,len(m)) for a in range(1,len(m)))
 return {'1':one,'X':d,'2':two,'over_2_5':ov,'under_2_5':1-ov,'btts_yes':bt,'btts_no':1-bt}
def _norm3(d):
 z=sum(d.values()); return {k:(v/z if z else 1/3) for k,v in d.items()}
def _elo_probs(e):
 # Elo alone cannot identify draw probability cleanly; reserve a transparent draw prior.
 draw=.24; return _norm3({'1':e*(1-draw),'X':draw,'2':(1-e)*(1-draw)})
def _form_probs(hf,af):
 hp,ap=hf.get('points_per_game'),af.get('points_per_game')
 if hp is None or ap is None:return {'1':1/3,'X':1/3,'2':1/3}
 diff=max(-2.5,min(2.5,float(hp)-float(ap))); home=.43+diff*.10; away=.33-diff*.10; draw=.24
 return _norm3({'1':max(.05,home),'X':draw,'2':max(.05,away)})
def _split_probs(hf,af):
 hp=(hf.get('home_split') or {}).get('points_per_game'); ap=(af.get('away_split') or {}).get('points_per_game')
 if hp is None or ap is None:return {'1':1/3,'X':1/3,'2':1/3}
 diff=max(-2.5,min(2.5,float(hp)-float(ap))); return _norm3({'1':.43+diff*.11,'X':.24,'2':.33-diff*.11})
def _mc_probs(x,y,seed):
 rng=random.Random(seed); out=[0,0,0]
 def draw(l):
  z=math.exp(-l); k=0; p=1
  while p>z:k+=1;p*=rng.random()
  return k-1
 for _ in range(5000):
  u,v=draw(x),draw(y);out[0 if u>v else 1 if u==v else 2]+=1
 return {'1':out[0]/5000,'X':out[1]/5000,'2':out[2]/5000}
def _consensus(models):
 weights={'elo':.20,'poisson_dixon_coles':.25,'form':.20,'home_away':.15,'monte_carlo':.20}
 out={k:0. for k in ('1','X','2')}; total=0.
 for name,p in models.items():
  w=weights.get(name,0); total+=w
  for k in out:out[k]+=w*p[k]
 return {k:round(v/total*100,2) for k,v in out.items()}
def model(c):
 h,a,L=c['home'],c['away'],c['league']; hs,as_=h.get('strength',{}),a.get('strength',{}); hf,af=h.get('recent_form',{}),a.get('recent_form',{})
 hg=float(L.get('home_goal_avg') or 1.35); ag=float(L.get('away_goal_avg') or 1.10)
 x=max(.2,min(3.8,hg*float(hs.get('attack_strength') or 1)*float(as_.get('defence_weakness') or 1))); y=max(.15,min(3.5,ag*float(as_.get('attack_strength') or 1)*float(hs.get('defence_weakness') or 1)))
 hp,ap=hf.get('points_per_game'),af.get('points_per_game')
 if hp is not None and ap is not None:
  d=max(-3,min(3,float(hp)-float(ap)));x*=1+max(-.1,min(.1,d*.035));y*=1-max(-.08,min(.08,d*.025))
 M=mat(x,y); q=mprob(M); e=.5 if hs.get('elo') is None or as_.get('elo') is None else 1/(1+10**(-((float(hs['elo'])+55-float(as_['elo']))/400)))
 seed=int((float(hs.get('elo') or 1500)*31+float(as_.get('elo') or 1500)*17))&0xffffffff
 models={'elo':_elo_probs(e),'poisson_dixon_coles':{k:q[k] for k in ('1','X','2')},'form':_form_probs(hf,af),'home_away':_split_probs(hf,af),'monte_carlo':_mc_probs(x,y,seed)}
 consensus=_consensus(models); hm=mat(x*.44,y*.44,6); sm=mat(x*.56,y*.56,6); joint={}
 for i in range(7):
  for j in range(7):
   for u in range(7):
    for v in range(7):
     k=('1' if i>j else 'X' if i==j else '2')+'/'+('1' if i+u>j+v else 'X' if i+u==j+v else '2');joint[k]=joint.get(k,0)+hm[i][j]*sm[u][v]
 ex=sorted(((v,i,j) for i,r in enumerate(M) for j,v in enumerate(r)),reverse=True)[:8]
 return {'probabilities':{**consensus,**{k:round(v*100,2) for k,v in q.items() if k not in ('1','X','2')}},'model_consensus':{'weights':{'elo':20,'poisson_dixon_coles':25,'form':20,'home_away':15,'monte_carlo':20},'models':{n:{k:round(v*100,2) for k,v in p.items()} for n,p in models.items()},'consensus':consensus},'expected_goals':{'home':round(x,3),'away':round(y,3),'kind':'results_based_proxy'},'elo':{'home':hs.get('elo'),'away':as_.get('elo'),'home_win':round(e*100,2),'source':hs.get('elo_source')},'monte_carlo':{'n':5000,**{k:round(v*100,2) for k,v in models['monte_carlo'].items()}},'first_half':{k:round(v*100,2) for k,v in mprob(hm).items() if k in ('1','X','2')},'iyms':{'probabilities':{k:round(v*100,2) for k,v in sorted(joint.items(),key=lambda z:z[1],reverse=True)}},'exact_scores':[{'score':f'{i}-{j}','probability':round(v*100,2)} for v,i,j in ex],'method':'Transparent ensemble: Elo + weighted form + home/away + Poisson/Dixon-Coles + Monte Carlo + HT/FT matrix','quality':'data-aware; xG is not claimed unless supplied by a source'}
def market(ms):
 for m in ms:
  t=str(m.get('type') or m.get('gameName') or '').lower()
  if '1x2' not in t and 'match' not in t and 'maç sonucu' not in t:continue
  q={}
  for o in m.get('odds',[]):
   try:v=str(o.get('value') or '');p=float(o.get('odd'));q[v]=1/p if p>1 else 0
   except:pass
  z=sum(q.values())
  if z:return {k:round(v/z*100,2) for k,v in q.items()}
 return {}
def window(d):
 s=datetime.combine(d,datetime.min.time(),tzinfo=TZ).astimezone(timezone.utc);return int(s.timestamp()),int((s+timedelta(days=1)).timestamp())
async def day(d):
 s,e=window(d);p=await five._get('fixtures',{'start_time':s,'end_time':e,'status':'all','lang':'en','per_page':100,'include':'odds,stats'});out=[]
 for f in p.get('data') or []:
  try:ld=datetime.fromisoformat(str(f.get('kickoff_utc')).replace('Z','+00:00')).astimezone(TZ).date()
  except (ValueError,TypeError):continue
  if ld!=d:continue
  r=five._fixture_row(f);r['_markets']=five._markets_from_odds({'data':{'odds':f.get('odds') or {}}},live=False);r['_stats']=f.get('statistics') or {};out.append(r)
 return out
async def cand(r):
 c=await build_match_context(r);return {'match':r,'context':c,'model':model(c),'markets':r.get('_markets') or []}
def choose(cs,msg):
 out=[]
 for c in cs:
  p=c['model']['probabilities']
  if isiy(msg):
   for k,v in c['model']['iyms']['probabilities'].items():
    if issur(msg) and k in ('1/1','X/X','2/2'):continue
    out.append((v,c,k))
  else:
   k=max(('1','X','2'),key=lambda x:p[x]);v=p[k];mk=market(c['markets'])
   if issur(msg) and mk and k!=max(mk,key=mk.get):v+=8
   out.append((v,c,k))
 out.sort(key=lambda z:z[0],reverse=True);seen=set();r=[]
 for v,c,k in out:
  i=str(c['match'].get('MatchID'))
  if i in seen:continue
  seen.add(i);r.append((v,c,k))
  if len(r)>=num(msg):break
 return r
def pack(sel):
 return [{'match_id':c['match'].get('MatchID'),'match':c['match'].get('Teams'),'kickoff':c['match'].get('KickoffUTC'),'selection':k,'probability':v,'model':c['model'],'home_form':c['context']['home']['recent_form'],'away_form':c['context']['away']['recent_form'],'home_standing':c['context']['home']['standing'],'away_standing':c['context']['away']['standing'],'h2h':c['context'].get('h2h',[]),'market_cross_check':market(c['markets']),'data_quality':c['context'].get('data_quality',{}),'data_availability':c['context'].get('data_availability',{})} for v,c,k in sel]
async def answer(main,msg,history=None):
 ds=dates(msg);rows=[r for g in await asyncio.gather(*(day(d) for d in ds)) for r in g]
 if not rows:return {'reply':'İstenen tarihlerde doğrulanmış gerçek futbol maçı bulunamadı.','engine':ENGINE,'engine_version':VERSION}
 cs=await asyncio.gather(*(cand(r) for r in rows));sel=choose(cs,msg);data=pack(sel)
 prompt=f'''Sen {ENGINE} sürüm {VERSION} profesyonel futbol istihbarat motorusun. Kullanıcı: {msg}\nDOSSIER:{json.dumps(data,ensure_ascii=False)}\nTahmin ana kaynağı şeffaf model konsensüsüdür; piyasa yalnızca çapraz kontroldür. Verilmeyen bilgiyi uydurma. xG resmi veri değilse xG diye sunma. En güçlü futbol kanıtlarını, model ayrışmalarını ve belirsizliği açıkla. Kupon oluşturma. Türkçe yanıt ver.'''
 try:reply=await asyncio.wait_for(main.gemini_generate(prompt),timeout=8.0)
 except Exception:
  lines=[f"{x['match']} — {x['selection']} (%{round(float(x['probability']),1)})" for x in data]
  reply=f"{ENGINE} modeli {len(cs)} maçı analiz etti. En güçlü model konsensüsü projeksiyonları:\n\n"+"\n".join(f"{i+1}. {v}" for i,v in enumerate(lines))+"\n\nPiyasa yalnızca çapraz kontroldür; bu liste kupon değildir."
 return {'reply':reply,'engine':ENGINE,'engine_version':VERSION,'dates':[d.isoformat() for d in ds],'match_count':len(rows),'analyzed_count':len(cs),'source':'5DollarFootballAPI + transparent statistical ensemble'}
async def analyze_match(main,mid):
 p=await five._get(f'fixtures/{int(mid)}',{'lang':'en','include':'events,stats'});f=p.get('data') or {}
 if not f:return {'analysis':{'mac_ozeti':'Maç bulunamadı.'},'engine':ENGINE,'engine_version':VERSION}
 r=five._fixture_row(f);r['_markets']=five._markets_from_odds({'data':{'odds':f.get('odds') or {}}},live=False);r['_stats']=f.get('statistics') or {};c=await cand(r);m=c['model'];pr=m['probabilities'];best=max(('1','X','2'),key=lambda x:pr[x]);score=m['exact_scores'][0]['score'] if m['exact_scores'] else None;names={'1':c['match'].get('Team1'),'X':'Beraberlik','2':c['match'].get('Team2')}
 analysis={'mac_ozeti':f"Model konsensüsü: {names[best]} önde. 1: %{pr['1']}, X: %{pr['X']}, 2: %{pr['2']}.",'takimlarin_durumu':f"{c['context']['home']['name']} son 5 formu {c['context']['home']['recent_form']['last_5'].get('form','-')}, {c['context']['away']['name']} son 5 formu {c['context']['away']['recent_form']['last_5'].get('form','-')}.",'olasi_senaryo':f"Gol modeli proxy'si {m['expected_goals']['home']} - {m['expected_goals']['away']}; en olası skor {score or 'belirsiz'}.",'ms_tahmini':names[best],'kg_tahmini':'Var' if pr['btts_yes']>=50 else 'Yok','alt_ust_tahmini':'Üst 2.5' if pr['over_2_5']>=50 else 'Alt 2.5','ilk_yari_tahmini':max(m['first_half'],key=m['first_half'].get),'ht_ft_tahmini':max(m['iyms']['probabilities'],key=m['iyms']['probabilities'].get),'model_consensus':m['model_consensus'],'data_availability':c['context'].get('data_availability',{}),'risk_seviyesi':'düşük' if max(pr['1'],pr['X'],pr['2'])>=60 else 'orta' if max(pr['1'],pr['X'],pr['2'])>=48 else 'yüksek','tahmin_gerekcesi':f"{m['method']}. Piyasa yalnızca çapraz kontroldür. Veri kalitesi: {c['context'].get('data_quality',{}).get('level','unknown')}."}
 return {'analysis':analysis,'model':m,'context':c['context'],'engine':ENGINE,'engine_version':VERSION,'source':'5DollarFootballAPI + transparent statistical ensemble'}
async def match_answer(main,mid,msg,history=None):
 result=await analyze_match(main,mid)
 if result.get('analysis',{}).get('mac_ozeti')=='Maç bulunamadı.':return {'reply':'Maç bulunamadı.','engine':ENGINE}
 m=result['model'];c={'model':m,'context':result['context'],'match':{'Teams':f"{result['context']['home']['name']} - {result['context']['away']['name']}"}};pr=m['probabilities'];best=max(('1','X','2'),key=lambda x:pr[x]);names={'1':result['context']['home']['name'],'X':'Beraberlik','2':result['context']['away']['name']}
 dossier={'match':c['match']['Teams'],'consensus':m['model_consensus'],'probabilities':pr,'expected_goals':m['expected_goals'],'first_half':m['first_half'],'iyms':m['iyms'],'exact_scores':m['exact_scores'],'home_form':result['context']['home']['recent_form'],'away_form':result['context']['away']['recent_form'],'standing':{'home':result['context']['home']['standing'],'away':result['context']['away']['standing']},'h2h':result['context'].get('h2h',[]),'data_availability':result['context'].get('data_availability',{}),'data_quality':result['context'].get('data_quality',{})}
 prompt=f'''Sen {ENGINE} maç özel uzmanısın. Kullanıcı: {msg}\nDOSSIER:{json.dumps(dossier,ensure_ascii=False)}\nYalnızca bu maçı analiz et. Model konsensüsü ile piyasa bilgisini karıştırma; piyasa verilmemişse uydurma. Eksik veriyi açıkça eksik olarak belirt. Türkçe, detaylı ama kanıta dayalı yanıt ver; kupon oluşturma.'''
 try:reply=await asyncio.wait_for(main.gemini_generate(prompt),timeout=8.0)
 except Exception:
  iy=max(m['iyms']['probabilities'],key=m['iyms']['probabilities'].get)
  reply=(f"{c['match']['Teams']} için {ENGINE} analizi:\n\n• Model konsensüsü: 1 %{pr['1']} | X %{pr['X']} | 2 %{pr['2']}\n• Öne çıkan sonuç: {names[best]}\n• Gol modeli proxy'si: {m['expected_goals']['home']} - {m['expected_goals']['away']}\n• İlk yarı: 1 %{m['first_half']['1']} | X %{m['first_half']['X']} | 2 %{m['first_half']['2']}\n• En güçlü İY/MS: {iy}\n\nModel ayrıntıları gerçek analiz context'inden üretilmiştir. Veri bulunmayan alanlar için istatistik uydurulmamıştır.")
 return {'reply':reply,'match_id':str(mid),'engine':ENGINE,'engine_version':VERSION,'analysis_context':dossier,'source':'5DollarFootballAPI + transparent statistical ensemble'}
def patch_main(main):
 from fastapi import Request,HTTPException
 async def chat(req:Request):
  try:p=await req.json()
  except Exception:p={}
  msg=str(p.get('message') or p.get('question') or '').strip()
  if not msg:raise HTTPException(400,'Mesaj boş olamaz.')
  return await answer(main,msg,p.get('history') or [])
 async def mch(req:Request,match_id:int):
  try:p=await req.json()
  except Exception:p={}
  msg=str(p.get('message') or p.get('question') or '').strip()
  if not msg:raise HTTPException(400,'Mesaj boş olamaz.')
  return await match_answer(main,match_id,msg,p.get('history') or [])
 async def ana(match_id:int):return await analyze_match(main,match_id)
 main.app.add_api_route('/chat',chat,methods=['POST']);main.app.add_api_route('/matches/{match_id}/chat',mch,methods=['POST']);main.app.add_api_route('/ai/analyze/{match_id}',ana,methods=['GET'])
