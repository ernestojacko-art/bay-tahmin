"""
Football Expert Chat Agent -- general football knowledge (spec section 12).

Handles conversational football topics (tactics, formations, pressing
systems, attacking/defensive structures, statistics literacy, leagues)
that are NOT about a specific match prediction. When a question is about
a specific match/prediction, the orchestrator routes it to the
Intelligence Engine instead -- this module never invents a match
prediction of its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.chat.llm_client import LLMClient

_SYSTEM_PROMPT = (
    "You are BAY TAHMİN, a confident, knowledgeable football expert and match analyst -- "
    "the tone of an experienced football pundit/tipster, not a customer-support chatbot. "
    "Answer in the same language the user writes in, in a natural, engaged voice: give a "
    "real opinion, back it with reasoning, and avoid corporate hedging phrases like 'bu "
    "konuda size yardımcı olabilirim' or repeating the same redirect sentence every time. "
    "Your expertise covers: tactics, formations, pressing systems, team structures, leagues, "
    "football history, player roles, football statistics literacy (xG, xGA, PPDA, "
    "possession, shots), and betting/prediction market terminology (1X2, Double Chance / "
    "Çifte Şans, Draw No Bet / Beraberlik İadeli, Over/Under / Alt-Üst, Both Teams To Score / "
    "Karşılıklı Gol, Half Time/Full Time / İY-MS, Correct Score / Doğru Skor, Asian Handicap / "
    "Asya Handikapı, odds/implied probability, and 'value'/'edge'). "
    "Hard boundary you must never cross: never invent a specific match's prediction, score, "
    "probability, or odds yourself -- those numbers only come from the Intelligence Engine's "
    "real analysis. If asked for a prediction on a specific match, say so plainly and ask "
    "which match they mean so the Engine can run it. Never claim any bet is safe, guaranteed, "
    "or risk-free. Within that boundary, speak like a real expert with a point of view, not "
    "a disclaimer generator."
)

_TOPICS: dict[str, str] = {
    r"\bpres\b|\bgegenpress|pressing": (
        "Pressing sistemleri, topu kaybettikten hemen sonra rakibe erken ve organize şekilde "
        "baskı kurmayı amaçlar. Yüksek pres (high press) rakibi kendi yarı sahasında sıkıştırır, "
        "orta pres (mid-block) daha dengeli bir risk/ödül dengesi sunar, düşük pres ise alanı "
        "daraltıp geçiş hücumlarına (counter-attack) güvenir. Etkili pres; oyuncular arası "
        "mesafenin (compactness) korunmasına ve tetikleme anının (pressing trigger) doğru "
        "seçilmesine bağlıdır."
    ),
    r"formasyon|4-3-3|4-4-2|3-5-2|dizili": (
        "Formasyonlar takımın hücum ve savunma organizasyonunun iskeletidir. 4-3-3 kanat "
        "genişliği ve yüksek pres için elverişlidir; 4-2-3-1 orta sahada sayısal denge ve "
        "10 numara rolüne alan açar; 3-5-2/5-3-2 kanat beklerin (wing-back) hem hücumda hem "
        "savunmada iş yükünü artırır. Modern takımlar genelde topa sahipken bir, topu "
        "kaybettiğinde başka bir yapıya geçen (positional fluidity) hibrit sistemler kullanır."
    ),
    r"hücum organizasyon|attacking structure|hücum yap": (
        "Hücum organizasyonunda kilit kavramlar: build-up (ilk çıkış), progression (topu ileri "
        "taşıma), ve final third yaratıcılığı (son bölge). İyi bir hücum yapısı sahada genişlik "
        "(width) ve derinlik (depth) dengesini korur, oyuncular arasında üçgenler (passing "
        "triangles) oluşturur ve rakip savunma hattını farklı bölgelerden esnetir (overloads)."
    ),
    r"savunma organizasyon|defensive structure|savunma yap": (
        "Savunma organizasyonu; hat arası mesafe (compactness), bölgesel sorumluluk (zonal "
        "marking) ile adam adama (man-marking) dengesi, ve geçiş anındaki (transition) hızlı "
        "toparlanma üzerine kuruludur. Modern savunmalar genelde 'orta sahayı kapatma' ve "
        "'rakip kanat oyuncusunu içeri sıkıştırma' gibi kolektif prensiplerle çalışır."
    ),
    r"xg|expected goals|beklenen gol": (
        "xG (expected goals), bir şutun gol olma olasılığını; şut mesafesi, açısı, vücut "
        "pozisyonu ve önceki aksiyon gibi faktörlere göre 0-1 arası bir değerle ifade eder. "
        "Bir takımın topladığı toplam xG, gerçek gol sayısından daha istikrarlı bir performans "
        "göstergesi olarak kabul edilir; kısa vadede gerçek gol sayısı şans faktörüyle sapabilir."
    ),
    r"dixon.?coles|poisson": (
        "Poisson modeli, bir takımın belirli bir maçta atacağı gol sayısını, o takımın hücum "
        "gücü ile rakibin savunma gücünden türetilen bir 'beklenen gol' (lambda) parametresiyle "
        "modelleyen istatistiksel bir yaklaşımdır. Dixon-Coles düzeltmesi ise düşük skorlu "
        "sonuçlar (0-0, 1-0, 0-1, 1-1) arasındaki gerçek hayattaki korelasyonu hesaba katarak "
        "saf Poisson modelinin bu bölgede yaptığı küçük sapmaları düzeltir."
    ),
    r"elo": (
        "Elo tabanlı derecelendirme, bir takımın gücünü rakiplerine karşı aldığı sonuçlara göre "
        "güncellenen tek bir sayısal reytingle özetler. İki takım arasındaki reyting farkı, "
        "lojistik bir fonksiyon aracılığıyla galibiyet olasılığına dönüştürülür; futbolda ev "
        "sahibi avantajı genellikle bu farka sabit bir bonus olarak eklenir."
    ),
    r"çifte şans|double chance|1x/x2": (
        "Çifte şans (Double Chance), tek bir bilette iki olası sonucu birleştiren bir pazardır: "
        "1X (ev sahibi kazanır veya berabere biter), X2 (berabere biter veya deplasman kazanır), "
        "12 (ev sahibi veya deplasman kazanır, yani beraberlik hariç). Beraberlik ihtimalini de "
        "kapsadığı için 1X2'nin tek bir seçeneğinden daha yüksek olasılıklı ama genelde daha "
        "düşük oranlı bir pazardır."
    ),
    r"beraberlik iadeli|draw no bet|\bdnb\b": (
        "Beraberlik İadeli (Draw No Bet), maç berabere biterse yatırılan bahsin iade edildiği "
        "bir pazardır. Böylece beraberlik riski ortadan kalkar; olasılıklar sadece ev sahibi ve "
        "deplasman galibiyeti arasında yeniden normalize edilir, bu yüzden düz 1X2'deki galibiyet "
        "oranından biraz daha düşük bir oran sunar."
    ),
    r"asya handikap|asian handicap|el handikap": (
        "Asya handikapı, taraflardan birine (genelde favoriye) sanal bir gol dezavantajı ya da "
        "avantajı vererek maçı 'eşitlemeye' çalışan bir pazardır. Örneğin -1 handikaplı bir "
        "favorinin bahsi kazanması için en az 2 farkla kazanması gerekir. Tam sayı çizgilerde "
        "(-1, 0, +1 gibi) tam o farkla biten maçlarda bahis iade edilir (push); yarım çizgilerde "
        "(-0.5, +0.5 gibi) iade imkansızdır, sonuç kesin olarak belli olur."
    ),
    r"iy/?ms|ht/?ft|ilk yarı.{0,15}maç sonucu": (
        "İY/MS (İlk Yarı/Maç Sonucu), hem ilk yarının hem de maçın genel sonucunun birlikte "
        "doğru tahmin edilmesini gerektiren bir pazardır (örn. X/1: ilk yarı berabere, maç "
        "sonucu ev sahibi kazanır). Toplamda 9 kombinasyon vardır (1/1, 1/X, 1/2, X/1, X/X, "
        "X/2, 2/1, 2/X, 2/2) ve tek sonuçlu 1X2'ye göre çok daha yüksek oranlı ama isabet "
        "olasılığı çok daha düşük bir pazardır."
    ),
    r"alt.?üst|over.?under|gol.{0,10}(alt|üst)": (
        "Alt/Üst (Over/Under), maçtaki toplam gol sayısının belirlenen bir çizginin altında mı "
        "üstünde mi kalacağını konu alan pazardır (örn. 2.5 Üst: 3 veya daha fazla gol; 2.5 Alt: "
        "2 veya daha az gol). Çizginin .5 ile bitmesi, tam sayıda biten maçlarda 'push' "
        "(iade) ihtimalini ortadan kaldırmak içindir."
    ),
    r"karşılıklı gol|both teams to score|\bbtts\b|\bkg\b var.{0,5}yok": (
        "Karşılıklı Gol (Both Teams To Score / BTTS), her iki takımın da maçta en az bir gol "
        "atıp atmayacağını konu alan pazardır. 'Var' (Yes), her iki takım da gol atarsa; 'Yok' "
        "(No), en az bir takım gol atamazsa kazanır. Skorun kimin kazandığıyla doğrudan ilgisi "
        "yoktur -- 3-1 de 1-1 de 'Var' sonucunu getirir."
    ),
    r"doğru skor|correct score|kaç.?kaç": (
        "Doğru Skor (Correct Score), maçın tam skorunun (örn. 2-1) baştan tahmin edilmesini "
        "gerektiren en zor ve en yüksek oranlı pazarlardan biridir. Poisson tabanlı modeller "
        "her olası skora bir olasılık atar; en olası skorun bile genelde düşük bir yüzdeyle "
        "(örn. %10-15 civarı) çıkması normaldir, çünkü çok sayıda skor olasılığı birbiriyle "
        "yarışır."
    ),
    r"value bet|edge|oran okuma|implied probability|market.{0,10}(olasılık|fark)": (
        "'Value' (değer) kavramı, bir modelin hesapladığı olasılık ile bahis oranının ima ettiği "
        "olasılık (implied probability) arasındaki farktan doğar. Oranın ima ettiği olasılık "
        "kabaca 1/oran şeklinde hesaplanır (bookmaker marjı hariç). Model olasılığı piyasanın "
        "ima ettiğinden belirgin şekilde yüksekse buna 'edge' (avantaj) denir -- ama bu, o "
        "bahsin kazanacağının garantisi değildir, sadece uzun vadeli istatistiksel bir "
        "avantaj sinyalidir."
    ),
}


@dataclass
class ExpertAnswer:
    text: str
    grounded_in_knowledge_base: bool


def _match_topic(message: str) -> str | None:
    lowered = message.lower()
    for pattern, answer in _TOPICS.items():
        if re.search(pattern, lowered):
            return answer
    return None


_FALLBACK_ANSWER = (
    "Bu konuda genel bir futbol sohbeti yapabilirim -- taktik, formasyonlar, pres sistemleri, "
    "hücum/savunma organizasyonları, istatistikler (xG, Poisson, Elo gibi modeller), ligler ya "
    "da bahis/tahmin pazarlarının ne anlama geldiği (1X2, çifte şans, Asya handikapı, İY/MS, "
    "alt/üst, karşılıklı gol, doğru skor) hakkında soru sorabilirsin. Belirli bir maçın tahmini "
    "için maçı belirtirsen Intelligence Engine üzerinden gerçek analiz getirebilirim."
)


class FootballExpertAgent:
    def __init__(self, llm_client: LLMClient):
        self._llm_client = llm_client

    async def answer(self, message: str) -> ExpertAnswer:
        llm_answer = await self._llm_client.generate(_SYSTEM_PROMPT, message)
        if llm_answer:
            return ExpertAnswer(text=llm_answer, grounded_in_knowledge_base=False)

        topic_answer = _match_topic(message)
        if topic_answer:
            return ExpertAnswer(text=topic_answer, grounded_in_knowledge_base=True)

        return ExpertAnswer(text=_FALLBACK_ANSWER, grounded_in_knowledge_base=True)
