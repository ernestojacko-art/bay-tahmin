# BAY TAHMİN — Football Intelligence Engine

Modüler, üretime hazır bir futbol analiz/tahmin motoru. FastAPI üzerine
kurulmuş; futbolu **kendi verisiyle** analiz eden bağımsız bir istatistiksel
motor ile, bu motorun üzerine konuşlanmış bir sohbet (chat) katmanından
oluşur.

## Altın Kural

> **Prediction Engine bir chatbot değildir ve bahis marketlerine bağımlı
> değildir.**

Somut olarak bu şu anlama gelir:

1. Sistem önce **kendi futbol verisini** (takım formu, hücum/savunma gücü,
   istatistiksel modeller) analiz ederek **bağımsız** tahmin/olasılık üretir.
2. Market/oran verisi varsa, bu veri **yalnızca** `Market Cross-Check`
   katmanında, tahmin **üretildikten sonra**, karşılaştırma/doğrulama amacıyla
   kullanılır. Market verisi asla Prediction Engine'in girdisi değildir.
3. `Chat Agent`, kendi başına asla bir tahmin/olasılık üretmez. Sadece
   `Prediction Engine`'in zaten hesapladığı sayıları doğal dile çevirir
   (bkz. [Chat Agent Mimarisi](#chat-agent-mimarisi)).
4. Gerçek veri kaynağı yoksa veya eksikse, sistem **veri uydurmaz** —
   eksikliği açıkça raporlar ve güven skorunu buna göre düşürür
   (bkz. [Bilinen Sınırlamalar](#bilinen-sınırlamalar)).

Akış şeması:

```
KULLANICI / FRONTEND
        |
        v
CHAT ORCHESTRATOR
        |
   +----+---------------------------+
   |                                |
   v                                v
GENERAL FOOTBALL EXPERT     MATCH / PREDICTION REQUEST
(taktik, formasyon, xG...)          |
                                     v
                     FOOTBALL INTELLIGENCE ENGINE
                     (Data -> Team Strength -> Stat.
                      Models -> Scenario -> Surprise ->
                      Sanity -> Confidence)
                                     |
                                     v
                      [varsa] MARKET CROSS-CHECK
                      (yalnızca karşılaştırma; asla girdi değil)
                                     |
                                     v
                       NATURAL LANGUAGE RESPONSE
```

---

## Mimari Açıklaması

Katmanlar tek yönlü ve birbirinden bağımsız sorumluluklara sahiptir:

| Katman | Sorumluluk |
|---|---|
| **Providers / Adapters** | Ham veriyi dış kaynaktan çeker, normalize eder. |
| **Data Intelligence** | Ham veriyi toplar, eksik alanları raporlar (uydurmaz). |
| **Team Strength Engine** | Takım gücünü çok faktörlü, açıklanabilir şekilde puanlar. |
| **Statistical Models** | Poisson/Dixon-Coles, Elo-tarzı ve form-bazlı modelleri üretir, ensemble ile birleştirir. |
| **Scenario Engine** | Favori/dengeli/sürpriz senaryoları isimlendirir. |
| **Surprise Intelligence** | Rutin olmayan İY/MS kombinasyonlarını çok boyutlu skorlar. |
| **Sanity & Contradiction** | Veri/model tutarsızlıklarını (örn. market ile aşırı sapma) yakalar. |
| **Market Cross-Check** | Tahmin üretildikten SONRA, varsa market verisiyle karşılaştırır. |
| **Confidence Engine** | Tüm sinyalleri birleştirip dürüst bir güven/risk raporu üretir. |
| **Prediction Engine** | Yukarıdaki tüm katmanları orkestre eden ana motor. |
| **Chat Agent** | Yalnızca zaten hesaplanmış sonuçları doğal dile çevirir. |
| **Context Memory** | Sohbet oturumu bazında aktif maç/analiz bağlamını tutar. |
| **Cache** | Takım gücü / maç analizi gibi pahalı hesaplamaları TTL ile önbellekler. |

## Klasör Yapısı

```
bay-tahmin-football-intelligence-engine/
├── app/
│   ├── main.py                     # FastAPI giriş noktası
│   ├── core/
│   │   ├── config.py                # Settings (env-driven)
│   │   ├── exceptions.py            # Özel hata hiyerarşisi
│   │   └── logging.py
│   ├── api/
│   │   ├── deps.py                  # Dependency Injection
│   │   └── v1/
│   │       ├── router.py
│   │       ├── matches.py
│   │       ├── predictions.py
│   │       └── chat.py
│   ├── schemas/                     # Pydantic response modelleri
│   │   ├── common.py, team.py, prediction.py, match.py, chat.py
│   ├── providers/                   # Adapter mimarisi
│   │   ├── base.py                  # BaseFootballDataProvider (ana interface)
│   │   ├── models.py                # Normalize veri modelleri
│   │   ├── null_provider.py         # Varsayılan: veri kaynağı yok, uydurmaz
│   │   ├── sample_provider.py       # SADECE test/dev — üretimde kilitli
│   │   ├── rest_football_provider.py# Production-ready generic REST adapter
│   │   ├── interfaces.py            # Dar kapsamlı alt-interface'ler
│   │   ├── composite_provider.py    # Çoklu-vendor birleşik provider
│   │   └── registry.py              # Provider seçici (env-driven)
│   ├── intelligence/                # Football Intelligence Engine
│   │   ├── data_intelligence.py
│   │   ├── team_strength.py
│   │   ├── statistical_models.py
│   │   ├── scenario_engine.py
│   │   ├── prediction_engine.py
│   │   ├── surprise_engine.py
│   │   ├── confidence_engine.py
│   │   ├── sanity_engine.py
│   │   └── market_cross_check.py
│   ├── chat/                        # Chat Agent
│   │   ├── orchestrator.py
│   │   ├── football_expert.py
│   │   ├── llm_client.py
│   │   └── context_memory.py
│   ├── services/
│   │   └── analysis_service.py      # Provider + Intelligence Engine + Cache bağlayıcısı
│   └── cache/
│       └── cache.py                 # In-memory TTL cache
├── tests/                           # pytest test suite
├── requirements.txt
├── .env.example
├── Dockerfile
└── .gitignore
```

---

## Intelligence Engine Modüllerinin Görevleri

- **`data_intelligence.py`** — Provider'dan ham veriyi toplar, normalize
  eder, hangi alanların eksik olduğunu (`missing_fields`) açıkça kaydeder.
  Hiçbir eksik alanı varsayılan/uydurma bir değerle doldurmaz.
- **`team_strength.py`** — Son 5/10/20 maç ağırlıklı hücum/savunma oranları,
  form skoru, ev/deplasman ayrımı, rakip gücüne göre düzeltme ve sezonluk
  performansı birleştirerek açıklanabilir bir `TeamStrengthProfile` üretir.
- **`statistical_models.py`** — Üç bağımsız model üretir ve ağırlıklı olarak
  birleştirir (**tek model karar vermez**):
  1. Poisson + Dixon-Coles (düşük skor korelasyon düzeltmeli) gol modeli
  2. Elo-tarzı reyting farkı modeli
  3. Kısa vadeli form-oranı modeli
  Modeller arası uyumu (`model_agreement`) da hesaplar.
- **`scenario_engine.py`** — Olasılıkları "favori / dengeli / sürpriz"
  senaryolarına çevirir; tek bir "kesin skor" iddiasında bulunmaz.
- **`surprise_engine.py`** — İY/MS kombinasyonlarını olasılık, sürpriz
  potansiyeli, taktik destek, istatistiksel destek, veri kalitesi ve risk
  boyutlarında skorlar. **Rutin 1/1, X/X, 2/2 sonuçları asla sürpriz olarak
  sıralanmaz** (bkz. `ROUTINE_COMBINATIONS`).
- **`sanity_engine.py`** — Takım/maç kimlik tutarsızlıklarını, örneklem
  büyüklüğü sorunlarını ve **model-market çelişkilerini** (örn. modelin
  7.00-8.00 oranlı bir outsider'ı %65-75 "banko" ilan etmesi) tespit eder.
- **`market_cross_check.py`** — Tahmin zaten üretildikten SONRA çalışır;
  market verisi yoksa "market verisi yok" der ve tahmini hiçbir şekilde
  değiştirmez.
- **`confidence_engine.py`** — Olasılık, veri kalitesi, model uyumu, market
  uyumu ve sanity uyarılarını birleştirip dürüst bir güven/risk raporu
  üretir. Güven skoru asla `MAX_PUBLIC_CONFIDENCE_LABEL` (varsayılan 0.90)
  değerini aşmaz — sistem hiçbir zaman "%100 garanti" iddia etmez.
- **`prediction_engine.py`** — Yukarıdaki tüm modülleri sırayla çağıran ana
  orkestratör; nihai `MatchPrediction` nesnesini üretir.

---

## Provider / Adapter Mimarisi

Tüm iş mantığı (`DataIntelligenceLayer` ve üzeri) yalnızca
`app/providers/base.py` içindeki **`BaseFootballDataProvider`** arayüzüne
bağımlıdır. Hangi gerçek veri kaynağının kullanıldığı, iş mantığı
katmanları için tamamen görünmezdir.

### Mevcut Provider'lar

| Provider adı (`ACTIVE_PROVIDER` / `DATA_PROVIDER`) | Sınıf | Açıklama |
|---|---|---|
| `none` (varsayılan) | `NullProvider` | Hiçbir veri kaynağı yok. Her çağrıda `ProviderNotConfiguredError` fırlatır — **asla veri uydurmaz.** |
| `sample-dev-only` | `SampleDataProvider` | Deterministik örnek veri. **Sadece** `ENVIRONMENT=development` veya `test` iken çalışır; production'da otomatik reddedilir. |
| `rest-generic` | `RestFootballDataProvider` | Gerçek, üretime hazır REST adapter (aşağıda detaylı). |

### Genişletilebilirlik: Dar Kapsamlı Interface'ler

`app/providers/interfaces.py` içinde, tek bir dev kaynağına sıkı bağımlılığı
önlemek için ayrı ayrı arayüzler tanımlıdır:

- `FixtureProviderInterface` — sadece fikstür/program verisi
- `TeamStatisticsProviderInterface` — takım istatistikleri, form, puan durumu
- `MatchStatisticsProviderInterface` — H2H ve maç bazlı istatistikler
- `MatchProgramProviderInterface` — RSS/maç programı tarzı feed'ler
- `OddsProviderInterface` — market/oran verisi (her zaman opsiyonel)

`app/providers/composite_provider.py` içindeki **`CompositeFootballDataProvider`**,
bu arayüzlerin farklı vendor'lardan gelen implementasyonlarını tek bir
`BaseFootballDataProvider` arkasında birleştirir. Örnek:

```python
provider = CompositeFootballDataProvider(
    fixture_provider=MyFixturesVendorAdapter(...),
    team_stats_provider=MyStatsVendorAdapter(...),
    match_stats_provider=MyStatsVendorAdapter(...),   # aynı örnek olabilir
    program_provider=MyRssProgramAdapter(...),         # opsiyonel
    odds_provider=MyOddsVendorAdapter(...),            # opsiyonel
)
```

Yapılandırılmamış herhangi bir bileşen çağrıldığında **veri uydurmak yerine**
`ProviderNotConfiguredError` fırlatır (odds hariç — odds provider yoksa bu
geçerli bir durumdur, boş liste döner).

### Gerçek Veri Kaynağı Bağlama (`RestFootballDataProvider`)

`app/providers/rest_football_provider.py`, gerçek bir HTTP istemcisi
(`httpx`), gerçek kimlik doğrulama (Bearer token), gerçek hata yönetimi
(401/403/429/timeout/network hatası ayrımı) ve gerçek JSON→model eşleme
mantığı içeren, **üretime hazır** bir adapter'dır.

Bu ortamda ağ erişimi ve gerçek bir API anahtarı olmadığı için, JSON alan
eşlemesi (`_map_fixture`, `_map_team_dataset`, vb.) dokümante edilmiş,
yaygın bir REST futbol-API şablonunu varsayar. Gerçek bir vendor'a
bağlamak için:

1. `.env` dosyasında `FOOTBALL_DATA_API_KEY` ve `FOOTBALL_DATA_API_BASE_URL`
   değerlerini gerçek değerlerle doldurun.
2. Vendor'ın endpoint path'leri varsayılanlardan farklıysa
   `FOOTBALL_DATA_FIXTURES_PATH`, `FOOTBALL_DATA_TEAM_STATS_PATH`,
   `FOOTBALL_DATA_STANDINGS_PATH`, `FOOTBALL_DATA_H2H_PATH` değerlerini
   güncelleyin.
3. `_map_*` fonksiyonlarındaki alan adlarını (örn. `raw["home_goals"]`)
   seçtiğiniz vendor'ın gerçek JSON şemasına göre düzenleyin.
4. `ACTIVE_PROVIDER=rest-generic` (veya `DATA_PROVIDER=rest-generic`) olarak
   ayarlayın.

Bunların dışında **hiçbir** katman (`DataIntelligenceLayer`, `TeamStrengthEngine`,
`PredictionEngine`, API endpoint'leri, Chat Agent) değişmeye ihtiyaç duymaz.

**API anahtarı/erişim bilgisi yokken bu adapter hiçbir veri üretmez** —
constructor'da hemen `ProviderNotConfiguredError` fırlatır.

---

## Kurulum Adımları

```bash
# 1. Sanal ortam oluştur
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. Bağımlılıkları kur
pip install -r requirements.txt

# 3. Ortam değişkenlerini ayarla
cp .env.example .env
# .env dosyasını düzenleyin (gerçek API anahtarları vb.)
```

## Environment Variables

Tüm değişkenler `.env.example` içinde açıklamalarıyla birlikte listelenmiştir.
Öne çıkanlar:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` / `test` / `production` |
| `ACTIVE_PROVIDER` (alias: `DATA_PROVIDER`) | `none` | `none` / `sample-dev-only` / `rest-generic` |
| `FOOTBALL_DATA_API_KEY` | *(boş)* | Gerçek veri sağlayıcı API anahtarı |
| `FOOTBALL_DATA_API_BASE_URL` | *(boş)* | Gerçek veri sağlayıcı base URL |
| `FOOTBALL_DATA_FIXTURES_PATH` vb. | `/fixtures` vb. | Vendor'a özel endpoint path'leri |
| `ODDS_API_KEY` / `ODDS_API_BASE_URL` | *(boş)* | Market verisi — her zaman opsiyonel |
| `MODEL_WEIGHT_POISSON_DIXON_COLES` / `_ELO_STRENGTH` / `_FORM_BASED` | `0.55` / `0.30` / `0.15` | Ensemble ağırlıkları |
| `SANITY_MARKET_DISAGREEMENT_THRESHOLD` | `0.35` | Model-market sapma eşiği |
| `MAX_PUBLIC_CONFIDENCE_LABEL` | `0.90` | Güven skoru üst sınırı |
| `LLM_PROVIDER` | `none` | Chat Agent için opsiyonel LLM (`none`/`anthropic`) |

## Uygulamayı Çalıştırma

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Sağlık kontrolü: `GET /health`
İnteraktif dokümantasyon: `http://localhost:8000/docs`

Testleri çalıştırmak için:

```bash
pytest
```

> **Not:** Bu geliştirme ortamında ağ erişimi kapalı olduğundan
> bağımlılıklar kurulup testler henüz bu konteyner içinde çalıştırılamadı
> (bkz. [Bilinen Sınırlamalar](#bilinen-sınırlamalar)).

---

## API Endpoint Listesi

| Method | Path | Açıklama |
|---|---|---|
| `GET` | `/health` | Servis sağlık kontrolü |
| `GET` | `/api/v1/matches?date=&league_id=` | Belirli tarihteki fikstürleri listeler |
| `GET` | `/api/v1/matches/{match_id}` | Tek bir fikstürün özeti |
| `GET` | `/api/v1/matches/{match_id}/analysis` | Tam tahmin analizi (önbellekli) |
| `POST` | `/api/v1/matches/{match_id}/analyze` | Analizi önbelleği atlayarak yeniden hesaplar |
| `GET` | `/api/v1/predictions?date=&league_id=` | Belirli tarihteki tüm maçların analizini döner |
| `GET` | `/api/v1/surprises?match_id=` | Bir maç için sürpriz İY/MS adaylarını döner |
| `POST` | `/api/v1/chat` | Genel/maça özel sohbet mesajı gönderir |
| `POST` | `/api/v1/matches/{match_id}/chat` | Belirli bir maça bağlı sohbet mesajı gönderir |

## Örnek Request/Response

### Maç analizi isteği

```
GET /api/v1/matches/M1/analysis
```

```json
{
  "match_id": "M1",
  "one_x_two": {"home_win": 0.52, "draw": 0.26, "away_win": 0.22},
  "expected_goals": {"home_xg": 1.68, "away_xg": 1.12, "total_xg": 2.80},
  "btts_yes_probability": 0.54,
  "scenarios": [
    {"scenario_type": "favorite", "label": "Sample United favored", "probability": 0.52, "tempo": "balanced"}
  ],
  "surprises": [
    {"combination": "X/1", "description": "Level at half-time, home team pulls away...", "composite_score": 0.31, "risk": "medium"}
  ],
  "confidence": {"confidence": 0.71, "risk": "medium", "data_quality": "high", "model_agreement": 0.83},
  "market_comparison": {"market_available": false, "notes": ["No market/odds data available -- prediction stands on its own analysis."]},
  "disclaimers": ["This analysis is generated from statistical modeling and available data. It is not a guaranteed outcome..."]
}
```

### Sohbet isteği

```
POST /api/v1/chat
{"session_id": "abc-123", "message": "Bu maçta en çok neye güveniyorsun?", "match_id": "M1"}
```

```json
{
  "session_id": "abc-123",
  "reply": "En yüksek olasılıklı sonuca güvenim: %71.0 (risk seviyesi: medium, veri kalitesi: high).",
  "intent": "match_analysis",
  "match_id": "M1",
  "used_prediction_engine": true,
  "grounded_in_analysis": true
}
```

---

## Chat Agent Mimarisi

`ChatOrchestrator`, gelen mesajı iki yoldan birine yönlendirir:

1. **General Football Expert** (`football_expert.py`) — taktik, formasyon,
   pres, xG, Poisson/Elo gibi genel futbol konularını, kural tabanlı bir
   bilgi tabanı (+ opsiyonel LLM ile daha doğal ifade) üzerinden yanıtlar.
   **Asla belirli bir maç için tahmin uydurmaz.**
2. **Match / Prediction Request** — mesaj belirli bir maça bağlıysa
   (`match_id` verilmiş veya bağlam üzerinden bir maç zaten bağlıysa),
   `AnalysisService` üzerinden `Prediction Engine` çalıştırılır ve **sadece
   zaten hesaplanmış sayılar** doğal dile çevrilir (`_narrate_*`
   fonksiyonları). Yanıt isteğe bağlı olarak bir LLM'e "yeniden ifade et"
   için gönderilebilir, ancak LLM'e **yeni sayı/olasılık üretme izni
   verilmez** — sistem promptu bunu açıkça yasaklar.

LLM opsiyoneldir (`LLM_PROVIDER=none` varsayılan). LLM yapılandırılmamışsa
veya çağrı başarısız olursa (`llm_client.py`), sistem sessizce yapılandırılmış
şablon yanıtına döner — **asla "ulaşılamadı" diyerek çökmez.**

## Context Memory

`context_memory.py`, oturum (`session_id`) bazında şunları saklar:

- Aktif maç kimliği ve takım isimleri (`active_match_id`, `home_team_name`, ...)
- En son üretilen `MatchPrediction` (tekrar hesaplama gerektirmeden takip
  sorularını yanıtlamak için)
- Son konuşma turları (`recent_turns`, `CHAT_CONTEXT_MAX_TURNS` ile sınırlı)
- Önceki niyet (`previous_intent`)

Bu sayede kullanıcı "Peki ilk yarı?" gibi bir takip sorusu sorduğunda, maç
adını tekrar belirtmesi gerekmez. Bellek işlemi süreç içi (in-memory) ve
2 saatlik hareketsizlik sonrası otomatik temizlenir; çoklu-instance
production dağıtımlarında bu depoyu Redis/DB tabanlı bir implementasyonla
değiştirmek yeterlidir (arayüz aynı kalır).

## Market Cross-Check Mantığı

**Kesin kural:** `Football Intelligence → Prediction/olasılık → (varsa) market
karşılaştırması`. **Asla:** `market yoksa → tahmin yok`.

`market_cross_check.py`:

1. Prediction Engine, market verisinden **tamamen bağımsız** olarak
   `ensemble_1x2` olasılığını zaten üretmiştir.
2. Market verisi varsa (`OddsMarket` listesi), bookmaker "overround"u
   (kâr payı) çıkarılarak gerçek "implied probability" hesaplanır.
3. Model olasılığı ile market'in ima ettiği olasılık karşılaştırılır
   (`model_vs_market_divergence`, toplam varyasyon mesafesi).
4. Bu karşılaştırma **tahmini asla değiştirmez** — sadece bilgilendirme ve
   `SanityContradictionEngine`'in çelişki kontrolü için girdi sağlar.
5. Model, market'e göre aşırı sapan bir "banko" iddiasında bulunuyorsa
   (`SANITY_MARKET_DISAGREEMENT_THRESHOLD` üzeri), `SanityFlag` üretilir ve
   `ConfidenceEngine` güven skorunu buna göre sert şekilde düşürür.

---

## Production Kullanım Notları

- **Cache**: Varsayılan olarak process-içi (in-memory) TTL cache kullanılır
  (`app/cache/cache.py`). Çoklu-worker/çoklu-instance dağıtımlarda paylaşımlı
  bir cache (Redis vb.) gerekir — arayüz zaten bunu destekleyecek şekilde
  soyutlanmıştır.
- **Sample provider güvenliği**: `sample-dev-only` provider'ı,
  `ENVIRONMENT` `development`/`test` dışında bir değere ayarlıysa
  `registry.py` tarafından otomatik olarak reddedilir — production'da
  yanlışlıkla örnek veri sunulması engellenir.
- **API anahtarları**: `.env` dosyası asla versiyon kontrolüne veya dağıtım
  paketine dahil edilmemelidir (`.gitignore` bunu zaten kapsar).
- **Güven skoru tavanı**: `MAX_PUBLIC_CONFIDENCE_LABEL` (varsayılan 0.90)
  ile sistem hiçbir zaman "%100 garanti" iddia etmez; bu değer bilinçli
  olarak production'da da düşürülebilir ama yükseltilmesi önerilmez.
- **Loglama**: `LOG_JSON=true` ile yapılandırılmış JSON log çıktısı elde
  edilebilir (log aggregation araçları için).

## Bilinen Sınırlamalar

- **Bu geliştirme ortamında ağ erişimi kapalıdır.** `pip install` ile
  bağımlılıklar bu konteyner içinde kurulamadı, bu yüzden `pytest` test
  paketi henüz **bu ortamda** çalıştırılamadı. Kod, standart bir
  Python/FastAPI ortamında (`pip install -r requirements.txt && pytest`)
  çalıştırılmak üzere yazılmıştır.
- **`rest-generic` provider'ın JSON alan eşlemesi placeholder'dır** —
  gerçek bir vendor'a bağlanmadan önce `_map_*` fonksiyonlarının o
  vendor'ın gerçek şemasına göre güncellenmesi gerekir (yukarıda adım
  adım açıklanmıştır).
- **HT/FT olasılıkları basitleştirilmiş bir modelle hesaplanır** —
  ilk yarı gollerinin toplam golün sabit bir oranı (`FIRST_HALF_GOAL_SHARE`)
  olduğu ve ikinci yarının ilk yarıdan koşullu olarak bağımsız olduğu
  varsayılır. Dakika-dakika maç içi veri (in-play event data) olmadan
  daha hassas bir model uydurma anlamına geleceğinden bilinçli olarak
  tercih edilmemiştir.
- **Lig ortalaması gol sayısı** (`LEAGUE_AVERAGE_GOALS_PER_MATCH`) şu anda
  sabit, genel kabul görmüş bir değerdir; lige özel gerçek bir ortalama,
  `Data Intelligence Layer`'a bağlanacak gerçek veri kaynağından
  hesaplanarak ileride bu sabitin yerine konulabilir.
- **Chat Agent'ın LLM entegrasyonu** şu an yalnızca Anthropic API için
  somutlaştırılmıştır (`llm_client.py`); `LLM_PROVIDER=none` varsayılan
  olduğu için bu tamamen opsiyoneldir ve sistemin çekirdek işlevselliğini
  etkilemez.
- **Composite provider henüz registry'ye otomatik kayıtlı değildir** —
  `CompositeFootballDataProvider`, farklı vendor adapter'larının elle
  (kod içinde) birleştirilmesini gerektirir; tek bir env değişkeniyle
  otomatik kurulum şu an desteklenmemektedir (bilinçli bir sonraki adım
  olarak bırakılmıştır).
