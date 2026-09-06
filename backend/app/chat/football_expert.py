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
    "You are the football-expert voice of BAY TAHMİN, a football intelligence platform. "
    "Answer general football questions (tactics, formations, pressing systems, team "
    "structures, leagues, football statistics literacy) knowledgeably and concisely, "
    "in the same language the user writes in. Never invent a specific match prediction, "
    "score, or probability -- if asked about a specific upcoming match, say the "
    "Intelligence Engine handles match-specific analysis and ask which match they mean."
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
    "hücum/savunma organizasyonları, istatistikler (xG, Poisson, Elo gibi modeller) ya da "
    "ligler hakkında soru sorabilirsin. Belirli bir maçın tahmini için maçı belirtirsen "
    "Intelligence Engine üzerinden analiz getirebilirim."
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
