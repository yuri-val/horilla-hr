"""The three standard questionnaires of the survey library, in Russian (SOF-2197).

The same surveys as surveys.library.STANDARD - eNPS, an engagement pulse and a
new hire check-in - worded for SCG. Statements are rated 1-5 and yes/no is a
choice with its own options, not the "agreement" and "yes / no" types: those
show their labels in the language of the employee's browser, which would mix
languages under Russian questions.
"""

from django.db import migrations

RATING, NPS, CHOICE, TEXTAREA = "rating", "nps", "choice", "textarea"


def rate(text):
    return {"type": RATING, "question": text, "options": [], "is_required": True}


def write(text):
    return {"type": TEXTAREA, "question": text, "options": [], "is_required": False}


QUESTIONNAIRES = [
    {
        "code": "scg-ru-enps",
        "title": "eNPS — готовность рекомендовать компанию",
        "description": (
            "Один вопрос о том, готовы ли вы рекомендовать компанию как место "
            "работы, и два открытых — почему. Займёт две минуты. Ответы анонимны."
        ),
        "is_anonymous": True,
        "questions": [
            {
                "type": NPS,
                "question": (
                    "Насколько вероятно, что вы порекомендуете нашу компанию как "
                    "место работы другу или знакомому? "
                    "(0 — точно не порекомендую, 10 — точно порекомендую)"
                ),
                "options": [],
                "is_required": True,
            },
            write("Что главным образом повлияло на вашу оценку?"),
            write("Что одно нам стоит изменить, чтобы в компании работалось лучше?"),
        ],
    },
    {
        "code": "scg-ru-engagement",
        "title": "Вовлечённость сотрудников — пульс-опрос",
        "description": (
            "Десять утверждений о повседневной работе: понятность задач, ресурсы, "
            "обратная связь, признание, развитие и команда. Оцените каждое по "
            "шкале от 1 до 5, где 1 — полностью не согласен, 5 — полностью "
            "согласен. Ответы анонимны."
        ),
        "is_anonymous": True,
        "questions": [
            rate("Я понимаю, каких результатов от меня ждут на моей должности."),
            rate(
                "У меня есть инструменты, доступы и информация, чтобы хорошо "
                "делать свою работу."
            ),
            rate(
                "Руководитель регулярно говорит мне, что у меня получается хорошо "
                "и что можно улучшить."
            ),
            rate("Когда я хорошо делаю работу, это замечают."),
            rate(
                "За последние полгода у меня появились новые знания или навыки, "
                "полезные в работе."
            ),
            rate(
                "Моё мнение учитывают, когда принимают решения, которые касаются "
                "моей работы."
            ),
            rate("Я вижу, как моя работа влияет на цели компании."),
            rate(
                "Моя нагрузка позволяет работать качественно без постоянных "
                "переработок."
            ),
            rate(
                "В моей команде помогают друг другу и относятся друг к другу "
                "с уважением."
            ),
            rate("Я рассчитываю работать в компании через год."),
            write("Что сильнее всего улучшило бы вашу работу в компании?"),
        ],
    },
    {
        "code": "scg-ru-new-hire-30",
        "title": "Адаптация новичка — первые 30 дней",
        "description": (
            "Для сотрудников, которые работают в компании около месяца: как "
            "прошли первые недели и чего не хватило. Утверждения оцените по шкале "
            "от 1 до 5, где 1 — полностью не согласен, 5 — полностью согласен. "
            "Опрос не анонимный: так HR сможет помочь с тем, о чём вы напишете."
        ),
        "is_anonymous": False,
        "questions": [
            rate(
                "Как вы в целом оцениваете свой первый месяц в компании? "
                "(1 — плохо, 5 — отлично)"
            ),
            rate("Работа соответствует тому, что мне рассказывали при найме."),
            rate(
                "Первый день был хорошо организован: было понятно, куда идти и "
                "кто меня встретит."
            ),
            rate(
                "В первые дни мне выдали технику, учётные записи и доступы, "
                "нужные для работы."
            ),
            rate(
                "У меня есть человек, к которому можно обратиться с вопросами "
                "по работе."
            ),
            rate("Я понимаю свои задачи и то, чего от меня ждут в ближайшие месяцы."),
            rate("В команде меня приняли хорошо."),
            {
                "type": CHOICE,
                "question": (
                    "Вы получили всю нужную информацию о правилах и процессах "
                    "компании?"
                ),
                "options": ["Да", "Нет"],
                "is_required": True,
            },
            write("Чего не хватило или что было непонятно в первые недели?"),
            write("Что стоит сохранить, а что сделать иначе для следующих новичков?"),
        ],
    },
]


def add_questionnaires(apps, schema_editor):
    Questionnaire = apps.get_model("surveys", "Questionnaire")
    for item in QUESTIONNAIRES:
        if not Questionnaire.objects.filter(code=item["code"]).exists():
            Questionnaire.objects.create(**item)


def remove_questionnaires(apps, schema_editor):
    Questionnaire = apps.get_model("surveys", "Questionnaire")
    Questionnaire.objects.filter(
        code__in=[item["code"] for item in QUESTIONNAIRES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scg_overrides", "0003_bot_notifications"),
        ("surveys", "0002_survey_library"),
    ]

    operations = [migrations.RunPython(add_questionnaires, remove_questionnaires)]
