# Sürpriz Veri

**Bay Tahmin – Tarihsel Oran ve Sürpriz Analiz Sistemi**

Sürpriz Veri, futbol maçlarının açılış ve kapanış bahis oranlarını geçmiş maçlarla karşılaştırarak benzer oranlara sahip karşılaşmaları incelemek için geliştirilmiş bağımsız bir analiz modülüdür.

Amaç, geçmiş maç verilerinden hareketle sürpriz sonuçların hangi koşullarda gerçekleştiğini incelemek ve bu bulguları anlaşılır analizlerle sunmaktır.

## 1. Projenin Amaçları

* Günlük ve haftalık gerçek maç programını sunmak.
* Açılış ve kapanış oranlarını ayrı ayrı değerlendirmek.
* Benzer oranlara sahip geçmiş maçları tespit etmek.
* Tarihsel maçların gerçek skorlarını ve İY/MS sonuçlarını incelemek.
* Sürpriz sonuçlara ilişkin tarihsel kanıtları değerlendirmek.
* Analiz sonuçlarını Türkçe ve anlaşılır biçimde sunmak.

Sürpriz Veri bir kupon oluşturma aracı değildir. Sonuçları kesin olarak tahmin ettiğini veya kazanç garantisi verdiğini iddia etmez.

## 2. Analiz Havuzları

### Açılış Oranı Havuzu

Güncel karşılaşmaların açılış oranlarıyla benzerlik gösteren geçmiş maçları inceler.

### Kapanış Oranı Havuzu

Güncel karşılaşmaların kapanış oranlarıyla benzerlik gösteren geçmiş maçları ayrı olarak değerlendirir.

### Ortak Geçmiş Maçlar

Açılış ve kapanış havuzlarında ortak bulunan tarihsel karşılaşmaların karşılaştırılmasını sağlar.

Oran benzerliği, belirlenen tolerans değerlerine göre değerlendirilir. Yeterli tarihsel örneklem bulunmadığında sonuçların güven düzeyi buna göre ele alınmalıdır.

## 3. İncelenen Bahis Türleri

Sistemin analiz kapsamındaki başlıca bahis türleri şunlardır:

* Maç sonucu (1X2)
* İlk yarı sonucu
* İlk yarı / maç sonucu (İY/MS)
* Karşılıklı gol (KG Var / Yok)
* Alt / üst gol seçenekleri
* İlk yarı ve maç skorları

İY/MS analizinde dokuz olası kombinasyonun tamamı dikkate alınır:

| İY/MS | Açıklama                                      |
| ----- | --------------------------------------------- |
| 1/1   | İlk yarı ve maç ev sahibi galibiyeti          |
| 1/X   | İlk yarı ev sahibi, maç beraberlik            |
| 1/2   | İlk yarı ev sahibi, maç deplasman galibiyeti  |
| X/1   | İlk yarı beraberlik, maç ev sahibi galibiyeti |
| X/X   | İlk yarı ve maç beraberlik                    |
| X/2   | İlk yarı beraberlik, maç deplasman galibiyeti |
| 2/1   | İlk yarı deplasman, maç ev sahibi galibiyeti  |
| 2/X   | İlk yarı deplasman, maç beraberlik            |
| 2/2   | İlk yarı ve maç deplasman galibiyeti          |

## 4. Tarihsel Veri Tablosu

Tarihsel karşılaştırmalarda hedeflenen veri alanları:

| Alan          | Açıklama                          |
| ------------- | --------------------------------- |
| Tarih         | Karşılaşmanın oynandığı tarih     |
| Lig           | Organizasyon veya lig adı         |
| Karşılaşma    | Ev sahibi ve deplasman takımları  |
| Açılış oranı  | Maç öncesi açılış oranları        |
| Kapanış oranı | Maç öncesi kapanış oranları       |
| İlk Yarı-Skor | İlk yarıda gerçekleşen skor       |
| Maç Skoru     | Karşılaşmanın bitiş skoru         |
| İY/MS         | Gerçekleşen ilk yarı / maç sonucu |
| Toplam Gol    | Karşılaşmada atılan toplam gol    |

Yalnızca kaynağından doğrulanabilen veriler gerçek tarihsel sonuç olarak sunulmalıdır. Eksik veriler tahmin edilerek doldurulmamalıdır.

## 5. Veri Kaynağı

Sürpriz Veri, mevcut futbol veri altyapısı olan **5DollarFootballAPI** ile çalışacak şekilde tasarlanmıştır.

API adresi:

`https://api.5dollarfootballapi.com/v1`

API anahtarı Render ortam değişkenleri üzerinden sağlanır.

Veri kaynağından alınamayan bilgiler gerçek veri gibi gösterilmemelidir.

## 6. Kurulum

Proje, ana Bay Tahmin deposu içerisindeki `surpriz_veri/` klasöründe bulunur.

Gerekli Python paketlerini yüklemek için:

```bash
pip install -r surpriz_veri/requirements.txt
```

## 7. Render Dağıtımı

Sürpriz Veri, ayrı bir Render Web Service olarak çalıştırılabilir.

**Build Command**

```bash
pip install -r surpriz_veri/requirements.txt
```

**Start Command**

```bash
uvicorn surpriz_veri.api:app --host 0.0.0.0 --port 10000
```

**Environment Variable**

```text
FIVE_DOLLAR_API_KEY=API_ANAHTARINIZ
```

Render üzerindeki servis adresi:

https://surpriz-veri-api.onrender.com

Sağlık kontrolü:

https://surpriz-veri-api.onrender.com/health

## 8. Haftalık Maç Programı

Haftalık maç programı, seçilen tarihten itibaren yedi günlük bir zaman aralığını kapsayacak şekilde tasarlanmıştır.

Maçlar başlangıç saatine göre sıralanır. Maç programının doğruluğu, kullanılan veri sağlayıcısının sunduğu güncel fikstür verilerine bağlıdır.

## 9. Güvenilirlik ve Sınırlamalar

* Tarihsel örneklem sayısı analizle birlikte değerlendirilmelidir.
* Açılış ve kapanış oranları birbirinin yerine kullanılmamalıdır.
* Eksik veya doğrulanamayan veriler açıkça belirtilmelidir.
* Veri sağlayıcısının istek sınırları ve erişim hataları analiz sonuçlarını etkileyebilir.
* Tarihsel benzerlik, gelecekte aynı sonucun gerçekleşeceği anlamına gelmez.
* Analiz sonuçları kesin tahmin veya kazanç garantisi değildir.

## 10. Proje Yapısı

```text
surpriz_veri/
├── api.py
├── config.py
├── data_provider.py
├── closing_pool.py
├── kickoff_pool.py
├── historical_*
├── evidence_engine.py
├── htft_analyzer.py
├── market_profile.py
├── models.py
└── requirements.txt
```

Bu liste, projenin temel bileşenlerini gösterir. `historical_*` ifadesi, tarihsel analizle ilgili modülleri temsil eder.

## 11. Projenin Temel İlkesi

**Gerçek veri, tarihsel kanıt ve şeffaf analiz.**

Sürpriz Veri'nin temel yaklaşımı, oran benzerliklerini tarihsel sonuçlarla karşılaştırmak ve yeterli kanıt bulunmadığında bunu açıkça belirtmektir.

