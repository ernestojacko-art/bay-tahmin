"""BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE entrypoint."""
from __future__ import annotations
import asyncio,json,math,random,re
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import five_dollar_bridge as five
from football_intelligence_data import build_match_context
TZ=ZoneInfo('Europe/Istanbul'); ENGINE='BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE'; VERSION='0.3.2'
def dates(s):
 t=str(s or '').lower();n=datetime.now(TZ).date();o=[]
 if 'yarın' in t or 'yarin' in t:o+=[n+timedelta(days=1)]
 if 'bugün' in t or 'bugun' in t:o+=[n]
 for w,k in [('cumartesi',5),('pazar',6)]:
  if w in t:o+=[n+timedelta((k-n.weekday())%7)]
 m=re.search(r'(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?',t)
 if m:
  y=int(m.group(3) or n.year);y+=2000 if y<100 else 0
  try:o+=[date(y,int(m.group(2)),int(m.group(1)))]
  except ValueError:pass
 return sorted(set(o or [n]))
def num(s):
 m=re.search(r'\b(\d{1,2})\s*(?:maç|adet|tane)\b',str(s).lower());return max(1,min(int(m.group(1)),20)) if m else 5
def isiy(s):return bool(re.search(r'iy\s*/?\s*ms|ilk\s*yari.*mac\s*sonucu|ilk\s*yarı.*maç\s*sonucu',str(s).lower()))
def issur(s):return 'sürpriz' in str(s).lower() or 'surpriz' in str(s).lower()
def pois(l,k):return math.exp(-l)*l**k/math.factorial(k)
def mat(x,y,n=8):
 m=[[pois(x,h)*pois(y,a) for a in range(n+1)] for h in range(n+1)];r=-.08
 for h in range(n+1):
  for a in range(n+1):
   if h==0 and a==0:m[h][a]*=1-x*y*r
   elif h==0 and a==1:m[h][a]*=1+x*r
   elif h==1 and a==0:m[h][a]*=1+y*r
   elif h==1 and a==1:m[h][a]*=1-r
 z=sum(map(sum,m));return [[v/z for v in row] for row in m]
def mprob(m):
 one=sum(m[h][a] for h in range(len(m)) for a in range(len(m)) if h>a);d=sum(m[i][i] for i in range(len(m)));two=1-one-d;ov=sum(m[h][a] for h in range(len(m)) for a in range(len(m)) if h+a>=3);bt=sum(m[h][a] for h in range(1,len(m)) for a in range(1,len(m)));return {'1':one,'X':d,'2':two,'over_2_5':ov,'under_2_5':1-ov,'btts_yes':bt,'btts_no':1-bt}
def model(c):
 h,a,L=c['home'],c['away'],c['league'];hs,as_=h.get('strength',{}),a.get('strength',{});hf,af=h.get('recent_form',{}),a.get('recent_form',{});hg=float(L.get('home_goal_avg') or 1.35);ag=float(L.get('away_goal_avg') or 1.10)
 x=max(.2,min(3.8,hg*float(hs.get('attack_strength') or 1)*float(as_.get('defence_weakness') or 1)));y=max(.15,min(3.5,ag*float(as_.get('attack_strength') or 1)*float(hs.get('defence_weakness') or 1)))
 hp,ap=hf.get('points_per_game'),af.get('points_per_game')
 if hp is not None and ap is not None:
  d=max(-3,min(3,float(hp)-float(ap)));x*=1+max(-.1,min(.1,d*.035));y*=1-max(-.08,min(.08,d*.025))
 M=mat(x,y);q=mprob(M);e=.5 if hs.get('elo') is None or as_.get('elo') is None else 1/(1+10**(-((float(hs['elo'])+55-float(as_['elo']))/400)));p1=.75*q['1']+.25*e;p2=.75*q['2']+.25*(1-e);px=max(.01,1-p1-p2);z=p1+px+p2;p1,px,p2=p1/z,px/z,p2/z
 hm=mat(x*.44,y*.44,6);sm=mat(x*.56,y*.56,6);joint={}
 for i in range(7):
  for j in range(7):
   for u in range(7):
    for v in range(7):
     k=('1' if i>j else 'X' if i==j else '2')+'/'+('1' if i+u>j+v else 'X' if i+u==j+v else '2');joint[k]=joint.get(k,0)+hm[i][j]*sm[u][v]
 ex=sorted(((v,i,j) for i,r in enumerate(M) for j,v in enumerate(r)),reverse=True)[:8];rng=random.Random(int((float(hs.get('elo') or 1500)*31+float(as_.get('elo') or 1500)*17))&0xffffffff);mc=[0,0,0]
 def draw(l):
  z=math.exp(-l);k=0;p=1
  while p>z:k+=1;p*=rng.random()
  return k-1
 for _ in range(5000):
  u,v=draw(x),draw(y);mc[0 if u>v else 1 if u==v else 2]+=1
 return {'probabilities':{**{k:round(v*100,2) for k,v in {'1':p1,'X':px,'2':p2}.items()},**{k:round(v*100,2) for k,v in q.items() if k not in ('1','X','2')}},'expected_goals':{'home':round(x,3),'away':round(y,3)},'elo':{'home':hs.get('elo'),'away':as_.get('elo'),'home_win':round(e*100,2)},'monte_carlo':{'n':5000,'1':round(mc[0]/50,2),'X':round(mc[1]/50,2),'2':round(mc[2]/50,2)},'first_half':{k:round(v*100,2) for k,v in mprob(hm).items() if k in ('1','X','2')},'iyms':{'probabilities':{k:round(v*100,2) for k,v in sorted(joint.items(),key=lambda z:z[1],reverse=True)}},'exact_scores':[{'score':f'{i}-{j}','probability':round(v*100,2)} for v,i,j in ex],'method':'Elo + recency-weighted form + attack/defence + Poisson/Dixon-Coles + Monte Carlo + joint HT/FT','quality':'results-based model; expected goals are a goals-derived proxy'}
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
 s,e=window(d);p=await five._get('fixtures',{'start_time':s,'end_time':e,'status':'all','lang':'en','per_page':50,'include':'odds,stats'});out=[]
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
 return [{'match_id':c['match'].get('MatchID'),'match':c['match'].get('Teams'),'kickoff':c['match'].get('KickoffUTC'),'selection':k,'probability':v,'model':c['model'],'home_form':c['context']['home']['recent_form'],'away_form':c['context']['away']['recent_form'],'home_standing':c['context']['home']['standing'],'away_standing':c['context']['away']['standing'],'h2h':c['context'].get('h2h',[]),'market_cross_check':market(c['markets']),'data_quality':c['context'].get('data_quality',{})} for v,c,k in sel]
async def answer(main,msg,history=None):
 ds=dates(msg);rows=[r for g in await asyncio.gather(*(day(d) for d in ds)) for r in g]
 if not rows:return {'reply':'İstenen tarihlerde doğrulanmış gerçek futbol maçı bulunamadı.','engine':ENGINE,'engine_version':VERSION}
 cs=await asyncio.gather(*(cand(r) for r in rows));sel=choose(cs,msg);data=pack(sel)
 prompt=f'''Sen {ENGINE} sürüm {VERSION} profesyonel futbol istihbarat motorusun. Kullanıcı: {msg}\nDOSSIER:{json.dumps(data,ensure_ascii=False)}\nTahmin ana kaynağı futbol modelidir; piyasa yalnızca çapraz kontroldür. Model Elo, recency-weighted form, takım hücum/savunma gücü, Poisson/Dixon-Coles, Monte Carlo ve ortak İY/MS dağılımını kullanır. Expected goals sonuçlardan türetilmiş proxy'dir. İY/MS doğrudan market yoksa model projeksiyonudur. Verilmeyen bilgiyi uydurma. En güçlü futbol kanıtlarını açıkla, belirsizliği belirt, kupon oluşturma. Türkçe yanıt ver.'''
 try:
  reply=await asyncio.wait_for(main.gemini_generate(prompt),timeout=8.0)
 except Exception:
  lines=[]
  for x in data:
   lines.append(f"{x['match']} — {x['selection']} (%{round(float(x['probability']),1)})")
  reply=f"{ENGINE} modeli {len(cs)} maçı analiz etti. En güçlü projeksiyonlar:\n\n" + "\n".join(f"{i+1}. {v}" for i,v in enumerate(lines)) + "\n\nBu sonuçlar istatistiksel futbol modelinden üretilmiştir; piyasa verisi yalnızca çapraz kontroldür."
 return {'reply':reply,'engine':ENGINE,'engine_version':VERSION,'dates':[d.isoformat() for d in ds],'match_count':len(rows),'analyzed_count':len(cs),'source':'5DollarFootballAPI + statistical football model'}
async def analyze_match(main,mid):
 p=await five._get(f'fixtures/{int(mid)}',{'lang':'en','include':'events,stats'});f=p.get('data') or {}
 if not f:return {'analysis':{'mac_ozeti':'Maç bulunamadı.'},'engine':ENGINE,'engine_version':VERSION}
 r=five._fixture_row(f);r['_markets']=five._markets_from_odds({'data':{'odds':f.get('odds') or {}}},live=False);r['_stats']=f.get('statistics') or {}
 c=await cand(r);m=c['model'];pr=m['probabilities'];best=max(('1','X','2'),key=lambda x:pr[x]);score=m['exact_scores'][0]['score'] if m['exact_scores'] else None
 names={'1':c['match'].get('Team1'),'X':'Beraberlik','2':c['match'].get('Team2')}
 analysis={'mac_ozeti':f"Model sonucu: {names[best]} önde. Model olasılıkları 1: %{pr['1']}, X: %{pr['X']}, 2: %{pr['2']}.",'takimlarin_durumu':f"{c['context']['home']['name']} formu {c['context']['home']['recent_form'].get('form','-')}, {c['context']['away']['name']} formu {c['context']['away']['recent_form'].get('form','-')}.",'olasi_senaryo':f"Model beklenen gol proxy'si {m['expected_goals']['home']} - {m['expected_goals']['away']}; en olası skor {score or 'belirsiz'}.",'ms_tahmini':names[best],'kg_tahmini':'Var' if pr['btts_yes']>=50 else 'Yok','alt_ust_tahmini':'Üst 2.5' if pr['over_2_5']>=50 else 'Alt 2.5','ilk_yari_tahmini':max(m['first_half'],key=m['first_half'].get),'ht_ft_tahmini':max(m['iyms']['probabilities'],key=m['iyms']['probabilities'].get),'surpriz_ihtimali':f"En güçlü düz olmayan İY/MS: {next(((k,v) for k,v in m['iyms']['probabilities'].items() if k not in ('1/1','X/X','2/2')),('yok',0))[0]}",'en_guvenilir_tahminler':[f"MS {names[best]} (%{pr[best]})",f"KG {'Var' if pr['btts_yes']>=50 else 'Yok'} (%{max(pr['btts_yes'],pr['btts_no'])})",f"Alt/Üst {'Üst 2.5' if pr['over_2_5']>=50 else 'Alt 2.5'} (%{max(pr['over_2_5'],pr['under_2_5'])})"],'risk_seviyesi':'düşük' if max(pr['1'],pr['X'],pr['2'])>=60 else 'orta' if max(pr['1'],pr['X'],pr['2'])>=48 else 'yüksek','tahmin_gerekcesi':f"{m['method']}. Piyasa verisi yalnızca çapraz kontrol olarak kullanılır. Veri kalitesi: {c['context'].get('data_quality',{}).get('level','unknown')}. xG resmi sağlayıcı xG'si değil, sonuçlardan türetilmiş model proxy'sidir."}
 return {'analysis':analysis,'model':m,'context':c['context'],'engine':ENGINE,'engine_version':VERSION,'source':'5DollarFootballAPI + statistical football model'}
async def match_answer(main,mid,msg,history=None):
 p=await five._get(f'fixtures/{int(mid)}',{'lang':'en','include':'events,stats'});f=p.get('data') or {}
 if not f:return {'reply':'Maç bulunamadı.','engine':ENGINE}
 r=five._fixture_row(f);r['_markets']=five._markets_from_odds({'data':{'odds':f.get('odds') or {}}},live=False);r['_stats']=f.get('statistics') or {};c=await cand(r);k=max(('1','X','2'),key=lambda x:c['model']['probabilities'][x]);data=pack([(c['model']['probabilities'][k],c,k)]);prompt=f'''Sen {ENGINE} maç özel uzmanısın. Kullanıcı: {msg}\nDOSSIER:{json.dumps(data,ensure_ascii=False)}\nGerçek futbol verisi ve istatistiksel modeli kullan. Oranlar sadece çapraz kontroldür. Eksik veriyi uydurma. Türkçe profesyonel yanıt ver; kupon oluşturma.'''
 try:
  reply=await asyncio.wait_for(main.gemini_generate(prompt),timeout=8.0)
 except Exception:
  pr=c['model']['probabilities']; iy=max(c['model']['iyms']['probabilities'],key=c['model']['iyms']['probabilities'].get)
  reply=(f"{c['match'].get('Teams')} için {ENGINE} özeti:\n"
         f"• 1: %{pr['1']} | X: %{pr['X']} | 2: %{pr['2']}\n"
         f"• Beklenen gol proxy: {c['model']['expected_goals']['home']} - {c['model']['expected_goals']['away']}\n"
         f"• En güçlü İY/MS projeksiyonu: {iy}\n"
         f"• Model: {c['model']['method']}\n\n"
         "LLM açıklama servisi zaman aşımına uğradığı için bu yanıt doğrudan Football Intelligence Engine tarafından üretildi.")
 return {'reply':reply,'match_id':str(mid),'engine':ENGINE,'engine_version':VERSION,'source':'5DollarFootballAPI + statistical football model'}
def patch_main(main):
 from fastapi import Request,HTTPException
 from fastapi.routing import APIRoute
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
 async def ana(match_id:int):
  return await analyze_match(main,match_id)
 main.app.router.routes=[r for r in main.app.router.routes if not(isinstance(r,APIRoute) and r.path in ('/chat','/matches/{match_id}/chat','/ai/analyze/{match_id}') and ('POST' in (r.methods or set()) or 'GET' in (r.methods or set())))]
 main.app.add_api_route('/chat',chat,methods=['POST']);main.app.add_api_route('/matches/{match_id}/chat',mch,methods=['POST']);main.app.add_api_route('/ai/analyze/{match_id}',ana,methods=['GET'])
