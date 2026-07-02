"""Scenario bank for daily micro-sessions.

Each scenario: Mongolian setup (what the situation is, what to do),
an English opener from the coach, and target phrases for feedback focus.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    level: str          # beginner | intermediate | advanced
    title_mn: str
    setup_mn: str
    opener_en: str
    focus: str          # what the LLM should listen for


SCENARIOS: list[Scenario] = [
    # ---------- beginner ----------
    Scenario(
        id="intro",
        level="beginner",
        title_mn="Өөрийгөө танилцуулах",
        setup_mn="Та шинэ хүнтэй танилцаж байна. Нэр, ажил/сургууль, хобби-гоо англиар хэлээрэй.",
        opener_en="Hi! I'm Tamir. Nice to meet you! What's your name, and what do you do?",
        focus="self-introduction: name, job/study, hobbies; present simple",
    ),
    Scenario(
        id="coffee",
        level="beginner",
        title_mn="Кофе захиалах",
        setup_mn="Та кафед байна. Кофе болон идэх зүйл захиалаарай. Хэмжээ, суух эсвэл авч явахаа хэлээрэй.",
        opener_en="Hello, welcome to Blue Sky Coffee! What can I get for you today?",
        focus="ordering: I'd like..., size, here or to go, polite requests",
    ),
    Scenario(
        id="taxi",
        level="beginner",
        title_mn="Такси дуудах",
        setup_mn="Та таксинд суулаа. Хаашаа явахаа хэлж, үнийг асуугаарай.",
        opener_en="Good afternoon! Where would you like to go?",
        focus="directions, asking price, numbers",
    ),
    Scenario(
        id="shopping",
        level="beginner",
        title_mn="Дэлгүүрт үнэ асуух",
        setup_mn="Та дэлгүүрт хувцас үзэж байна. Үнэ, өнгө, хэмжээг нь асуугаарай.",
        opener_en="Hi there! Can I help you find anything?",
        focus="how much, sizes, colors, do you have...",
    ),
    Scenario(
        id="weekend",
        level="beginner",
        title_mn="Амралтын өдрийн төлөвлөгөө",
        setup_mn="Найз тань таны амралтын өдрийн төлөвлөгөөг асууж байна. Юу хийхээ ярьж өгөөрэй.",
        opener_en="Hey! Do you have any plans for the weekend?",
        focus="future plans: going to / will, days, activities",
    ),
    # ---------- intermediate ----------
    Scenario(
        id="job_interview",
        level="intermediate",
        title_mn="Ажлын ярилцлага",
        setup_mn="Та ажлын ярилцлагад орж байна. Туршлага, давуу талаа ярьж, асуултад хариулаарай.",
        opener_en="Thanks for coming in today. Could you tell me a little about yourself and your experience?",
        focus="past experience, strengths, professional vocabulary, past tense",
    ),
    Scenario(
        id="hotel",
        level="intermediate",
        title_mn="Буудалд асуудал шийдэх",
        setup_mn="Таны буудлын өрөөний халаалт ажиллахгүй байна. Ресепшнд гомдол гаргаж, шийдэл хүсээрэй.",
        opener_en="Front desk, good evening. How can I help you?",
        focus="polite complaints, explaining problems, requests",
    ),
    Scenario(
        id="doctor",
        level="intermediate",
        title_mn="Эмчид үзүүлэх",
        setup_mn="Та эмчид үзүүлж байна. Хаана өвдөж байгаа, хэзээнээс эхэлснийг тайлбарлаарай.",
        opener_en="Hello, please have a seat. What seems to be the problem today?",
        focus="symptoms, duration (for/since), body vocabulary",
    ),
    Scenario(
        id="hometown",
        level="intermediate",
        title_mn="Төрсөн нутгаа дүрслэх (IELTS хэв маяг)",
        setup_mn="IELTS ярианы шалгалтын хэв маягаар: төрсөн нутгаа дүрслээрэй — хаана байдаг, юугаараа онцлог, яагаад дуртай вэ?",
        opener_en="Let's talk about where you're from. Can you describe your hometown for me?",
        focus="description, adjectives, giving reasons, extended answers",
    ),
    Scenario(
        id="opinion_city",
        level="intermediate",
        title_mn="Хотын амьдралын тухай санал бодол",
        setup_mn="Хотын амьдрал ба хөдөөний амьдралын аль нь дээр вэ? Саналаа хэлж, шалтгаанаа тайлбарлаарай.",
        opener_en="Some people love city life, others prefer the countryside. What do you think?",
        focus="opinions: I think/in my view, comparisons, because/so",
    ),
    # ---------- advanced ----------
    Scenario(
        id="negotiation",
        level="advanced",
        title_mn="Цалингийн хэлэлцээр",
        setup_mn="Та цалин нэмүүлэх хүсэлтээ менежертээ тайлбарлаж байна. Үндэслэлээ гаргаж, эсэргүүцэлд хариулаарай.",
        opener_en="You wanted to discuss your compensation. I have about ten minutes — what's on your mind?",
        focus="persuasion, hedging language, conditionals, professional register",
    ),
    Scenario(
        id="presentation",
        level="advanced",
        title_mn="Төслөө танилцуулах",
        setup_mn="Та багтаа шинэ төслийн санаагаа 1 минутад танилцуулж байна. Асуудал, шийдэл, үр ашгийг нь хэлээрэй.",
        opener_en="Alright, the floor is yours — tell us about your idea.",
        focus="structuring: first/then/finally, problem-solution, signposting",
    ),
]


def pick_scenario(level: str, sessions_done: int) -> Scenario:
    """Rotate deterministically through the user's level pool."""
    pool = [s for s in SCENARIOS if s.level == level] or SCENARIOS
    return pool[sessions_done % len(pool)]


def by_id(scenario_id: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    return SCENARIOS[0]
