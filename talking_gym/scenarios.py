"""Scenario bank for daily micro-sessions.

Each scenario: Mongolian setup, the coach's English opener with Mongolian
translation, and a model answer (EN + MN) the learner can read aloud and
adapt — beginners should never face a question they can't answer.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    level: str          # beginner | intermediate | advanced
    title_mn: str
    setup_mn: str
    opener_en: str
    opener_mn: str      # translation of the opener
    example_en: str     # model answer the learner reads aloud / adapts
    example_mn: str     # its Mongolian meaning
    focus: str          # what the LLM should listen for


SCENARIOS: list[Scenario] = [
    # ---------- beginner ----------
    Scenario(
        id="intro",
        level="beginner",
        title_mn="Өөрийгөө танилцуулах",
        setup_mn="Та шинэ хүнтэй танилцаж байна.",
        opener_en="Hi! I'm Tamir. Nice to meet you! What's your name, and what do you do?",
        opener_mn="Сайн уу! Би Тамир. Танилцахад таатай байна! Таныг хэн гэдэг вэ, юу хийдэг вэ?",
        example_en="My name is Bat. I'm a student at university. I like playing basketball and listening to music.",
        example_mn="Намайг Бат гэдэг. Би их сургуулийн оюутан. Би сагсан бөмбөг тоглох, хөгжим сонсох дуртай.",
        focus="self-introduction: name, job/study, hobbies; present simple",
    ),
    Scenario(
        id="coffee",
        level="beginner",
        title_mn="Кофе захиалах",
        setup_mn="Та кафед байна. Кофе болон идэх зүйл захиалаарай.",
        opener_en="Hello, welcome to Blue Sky Coffee! What can I get for you today?",
        opener_mn="Сайн байна уу, Blue Sky кофе шопт тавтай морил! Танд юу авч өгөх вэ?",
        example_en="I'd like a medium latte, please. And one chocolate cookie. For here, please.",
        example_mn="Би дунд хэмжээний латте авъя. Бас нэг шоколадтай жигнэмэг. Энд ууна.",
        focus="ordering: I'd like..., size, here or to go, polite requests",
    ),
    Scenario(
        id="taxi",
        level="beginner",
        title_mn="Такси дуудах",
        setup_mn="Та таксинд суулаа. Хаашаа явахаа хэлж, үнийг асуугаарай.",
        opener_en="Good afternoon! Where would you like to go?",
        opener_mn="Өдрийн мэнд! Та хаашаа явах вэ?",
        example_en="Hello! Can you take me to the State Department Store, please? How much will it cost?",
        example_mn="Сайн байна уу! Намайг Улсын их дэлгүүр рүү хүргэж өгнө үү? Хэд болох вэ?",
        focus="directions, asking price, numbers",
    ),
    Scenario(
        id="shopping",
        level="beginner",
        title_mn="Дэлгүүрт үнэ асуух",
        setup_mn="Та дэлгүүрт хувцас үзэж байна. Үнэ, өнгө, хэмжээг нь асуугаарай.",
        opener_en="Hi there! Can I help you find anything?",
        opener_mn="Сайн байна уу! Танд юм олоход туслах уу?",
        example_en="Yes, please. How much is this T-shirt? Do you have it in black, size medium?",
        example_mn="Тийм ээ. Энэ футболк ямар үнэтэй вэ? Хар өнгийн, M хэмжээтэй нь бий юу?",
        focus="how much, sizes, colors, do you have...",
    ),
    Scenario(
        id="weekend",
        level="beginner",
        title_mn="Амралтын өдрийн төлөвлөгөө",
        setup_mn="Найз тань таны амралтын өдрийн төлөвлөгөөг асууж байна.",
        opener_en="Hey! Do you have any plans for the weekend?",
        opener_mn="Хөөе! Амралтын өдрөөр ямар нэг төлөвлөгөө бий юу?",
        example_en="Yes! On Saturday, I'm going to visit my parents. On Sunday, I will watch a movie with my friends.",
        example_mn="Тийм! Бямба гарагт эцэг эх дээрээ очно. Ням гарагт найзуудтайгаа кино үзнэ.",
        focus="future plans: going to / will, days, activities",
    ),
    # ---------- intermediate ----------
    Scenario(
        id="job_interview",
        level="intermediate",
        title_mn="Ажлын ярилцлага",
        setup_mn="Та ажлын ярилцлагад орж байна. Туршлага, давуу талаа яриарай.",
        opener_en="Thanks for coming in today. Could you tell me a little about yourself and your experience?",
        opener_mn="Өнөөдөр ирсэнд баярлалаа. Өөрийнхөө тухай болон туршлагаасаа ярьж өгөхгүй юу?",
        example_en="Thank you for inviting me. I graduated from university two years ago, and I have been working as a sales assistant. I'm good at talking with customers.",
        example_mn="Урьсанд баярлалаа. Би хоёр жилийн өмнө их сургууль төгссөн, одоо борлуулалтын туслахаар ажиллаж байгаа. Би үйлчлүүлэгчидтэй харилцахдаа сайн.",
        focus="past experience, strengths, professional vocabulary, past tense",
    ),
    Scenario(
        id="hotel",
        level="intermediate",
        title_mn="Буудалд асуудал шийдэх",
        setup_mn="Таны буудлын өрөөний халаалт ажиллахгүй байна. Гомдол гаргаж, шийдэл хүсээрэй.",
        opener_en="Front desk, good evening. How can I help you?",
        opener_mn="Ресепшн байна, оройн мэнд. Танд яаж туслах вэ?",
        example_en="Good evening. I'm in room 305. The heating is not working and the room is very cold. Could you send someone to fix it, please?",
        example_mn="Оройн мэнд. Би 305 тоот өрөөнд байна. Халаалт ажиллахгүй, өрөө их хүйтэн байна. Засах хүн явуулж өгнө үү?",
        focus="polite complaints, explaining problems, requests",
    ),
    Scenario(
        id="doctor",
        level="intermediate",
        title_mn="Эмчид үзүүлэх",
        setup_mn="Та эмчид үзүүлж байна. Шинж тэмдгээ тайлбарлаарай.",
        opener_en="Hello, please have a seat. What seems to be the problem today?",
        opener_mn="Сайн байна уу, сууна уу. Өнөөдөр юу болоод байна вэ?",
        example_en="Hello, doctor. I have had a headache and a sore throat for three days. I also feel very tired.",
        example_mn="Сайн байна уу, эмч ээ. Гурав хоног толгой өвдөж, хоолой сөөж байна. Бас их ядарч байна.",
        focus="symptoms, duration (for/since), body vocabulary",
    ),
    Scenario(
        id="hometown",
        level="intermediate",
        title_mn="Төрсөн нутгаа дүрслэх (IELTS хэв маяг)",
        setup_mn="IELTS ярианы шалгалтын хэв маягаар: төрсөн нутгаа дүрслээрэй.",
        opener_en="Let's talk about where you're from. Can you describe your hometown for me?",
        opener_mn="Таны төрсөн нутгийн тухай ярилцъя. Нутгаа дүрсэлж өгөхгүй юу?",
        example_en="I'm from Ulaanbaatar, the capital of Mongolia. It's a busy city with about 1.5 million people. I love it because my family and friends live there, but the traffic is terrible.",
        example_mn="Би Монголын нийслэл Улаанбаатараас ирсэн. 1.5 сая орчим хүнтэй, завгүй хот. Гэр бүл, найзууд минь тэнд амьдардаг болохоор би хотдоо хайртай, гэхдээ замын түгжрэл нь аймшигтай.",
        focus="description, adjectives, giving reasons, extended answers",
    ),
    Scenario(
        id="opinion_city",
        level="intermediate",
        title_mn="Хотын амьдралын тухай санал бодол",
        setup_mn="Хотын амьдрал ба хөдөөний амьдралын аль нь дээр вэ? Саналаа хэлээрэй.",
        opener_en="Some people love city life, others prefer the countryside. What do you think?",
        opener_mn="Зарим хүн хотын амьдралд дуртай, зарим нь хөдөөг илүүд үздэг. Та юу гэж боддог вэ?",
        example_en="In my opinion, city life is better for young people because there are more jobs and schools. However, the countryside is quieter and the air is cleaner.",
        example_mn="Миний бодлоор залууст хотын амьдрал илүү тохиромжтой, учир нь ажил, сургууль олон. Гэхдээ хөдөө нам гүм, агаар нь цэвэр.",
        focus="opinions: I think/in my view, comparisons, because/so",
    ),
    # ---------- advanced ----------
    Scenario(
        id="negotiation",
        level="advanced",
        title_mn="Цалингийн хэлэлцээр",
        setup_mn="Та цалин нэмүүлэх хүсэлтээ менежертээ тайлбарлаж байна. Үндэслэлээ гаргаарай.",
        opener_en="You wanted to discuss your compensation. I have about ten minutes — what's on your mind?",
        opener_mn="Та цалингийн асуудлаар ярилцахыг хүссэн. Надад 10 минут байна — сонсъё.",
        example_en="Thank you for your time. Over the past year, I have taken on more responsibilities and my results have grown. Based on that, I would like to discuss a salary increase of about 15 percent.",
        example_mn="Цаг гаргасанд баярлалаа. Өнгөрсөн жил би илүү их үүрэг хариуцлага авч, үр дүн маань өссөн. Үүн дээр үндэслэн 15 хувийн цалингийн нэмэгдэл ярилцмаар байна.",
        focus="persuasion, hedging language, conditionals, professional register",
    ),
    Scenario(
        id="presentation",
        level="advanced",
        title_mn="Төслөө танилцуулах",
        setup_mn="Та багтаа шинэ төслийн санаагаа нэг минутад танилцуулж байна.",
        opener_en="Alright, the floor is yours — tell us about your idea.",
        opener_mn="За, таны ээлж — санаагаа танилцуулна уу.",
        example_en="Thank you. First, let me describe the problem: our customers wait too long for replies. My idea is a simple chatbot that answers common questions. Finally, this could save us about ten hours every week.",
        example_mn="Баярлалаа. Эхлээд асуудлыг хэлье: үйлчлүүлэгчид маань хариу хэт удаан хүлээдэг. Миний санаа бол түгээмэл асуултад хариулдаг энгийн чатбот. Эцэст нь энэ нь долоо хоногт арав орчим цаг хэмнэнэ.",
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
