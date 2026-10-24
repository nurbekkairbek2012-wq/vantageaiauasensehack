"""Речь для видео-демо: PDF, с которого можно учить и в который можно смотреть.

Markdown хорош, пока текст читают с экрана. Речь учат иначе: лист
лежит рядом, взгляд соскакивает на него на полсекунды и возвращается.
Поэтому здесь другая вёрстка, чем в docs/VIDEO_SCRIPT.md, — реплики
набраны крупно, а всё, что не произносится вслух (что на экране, каким
темпом говорить), уведено в мелкий серый шрифт слева от текста.
Спутать одно с другим на записи невозможно.

Вёрстка идёт потоком с автопереносом страниц: ручное управление
страницами в make_handoff.py уже приводило к тому, что текст молча
уезжал под подвал, а reportlab не считает это ошибкой.

Запуск:
    python scripts/make_speech_pdf.py
Результат:
    docs/examples/VANTAGE_речь.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from vantage.act import register_cyrillic_font  # noqa: E402

WPM = 132  # спокойный темп русской речи при записи

PAPER = (0.969, 0.957, 0.937)
INK = (0.086, 0.075, 0.059)
INK_2 = (0.29, 0.267, 0.235)
INK_3 = (0.522, 0.49, 0.447)
RUST = (0.722, 0.290, 0.122)
RULE = (0.867, 0.835, 0.784)

SKELETON = [
    "Свалок нет в реестре, потому что их никто не видел.",
    "Вот что известно публично — а вот что есть на самом деле.",
    "Различает их не картинка, а физика. Пять признаков.",
    "Дальше деньги: не одна цифра, а диапазон.",
    "Но главное не это. Мы знаем, когда свалка появилась — значит, "
    "можем сказать, где появится следующая.",
    "Убрать — миллионы. Не дать появиться — дорожный знак.",
    "Где спутник слеп, работает житель.",
    "Границы системы мы называем сами.",
]

NUMBERS = [
    ("Блок 2", "найдено объектов", "на экране, вслух не звучит"),
    ("Блок 4", "суммарный ущерб, P50", "________ млрд ₸"),
    ("Блок 5", "точность прогноза", "PR-AUC ________"),
    ("Блок 5", "горизонт прогноза", "12 месяцев"),
]

# Блок: (от, до, название, [реплики], что на экране, как говорить)
BLOCKS = [
    (
        "0:00", "0:18", "Проблема",
        [
            "Вокруг Астаны есть свалки, которых нет ни в одном реестре.",
            "Их никто не считал — потому что никто не видел. Обойти степь "
            "пешком невозможно. Находят их по жалобе, когда убирать уже дорого.",
        ],
        "Карта области, на ней несколько синих контуров — то, что размечено "
        "в открытых данных.",
        "Первые две фразы — медленно. Это установка задачи, зритель ещё "
        "настраивается на голос.",
    ),
    (
        "0:18", "0:36", "Что мы нашли",
        [
            "Мы взяли бесплатные спутниковые снимки за восемь лет и научились "
            "находить такие объекты автоматически.",
            "Вот что известно публично.",
            "@ПАУЗА — две секунды. Нажимаете «Дальше».",
            "А вот что есть на самом деле.",
        ],
        "Вспыхивают найденные объекты.",
        "Цифру вслух не называйте. Она видна на панели, зритель прочитает её "
        "сам — и это подействует сильнее, чем если её произнести.",
    ),
    (
        "0:36", "1:09", "Пять признаков",
        [
            "На десяти метрах свалка выглядит как серое пятно. Ровно как карьер "
            "или стройка. Различает их не картинка, а физика.",
            "Растительность погибла и не вернулась — это отсекает пашню: там "
            "зелень возвращается каждую весну.",
            "Радар видит, что поверхность нестабильна — это отсекает карьер: "
            "его стенки стоят неделями.",
            "Тепло выдаёт гниющую органику — и отсекает снегосвалку: она "
            "холоднее фона.",
            "Модель не выносит вердикт. Она показывает, какие признаки сработали.",
        ],
        "Карточка объекта, панель доказательной цепочки. Курсором по шкалам "
        "сверху вниз, ровно в такт словам.",
        "Три признака — три одинаковых по ритму фразы. Держите одинаковую паузу "
        "между ними, это слышно как структура.",
    ),
    (
        "1:09", "1:40", "Деньги и акт",
        [
            "Дальше — деньги. Масса отходов, стоимость вывоза, метан за "
            "двадцать лет.",
            "Не одна цифра, а диапазон: восемь допущений, у каждого свой "
            "источник. Точечная оценка не пережила бы вопроса «откуда».",
            "Статья триста сорок четыре КоАП: сто МРП физлицу, тысяча — "
            "крупному бизнесу.",
            "Одна кнопка — готовый акт. Со статусом «черновик»: официальным он "
            "станет, когда человек подтвердит его своим именем.",
        ],
        "Блок ущерба с полосой P10–P90, затем нажатие на «черновик акта» "
        "и появившийся PDF.",
        "Фраза про черновик означает: мы не подменяем инспектора, мы снимаем "
        "с него бумажную работу. Скажите её без извинения в голосе — это "
        "преимущество, а не ограничение.",
    ),
    (
        "1:40", "2:14", "Прогноз — кульминация",
        [
            "Но главное не это.",
            "Детектор знает не только где свалка, но и когда она появилась. "
            "Значит, у нас есть готовая история за восемь лет.",
            "По ней модель предсказывает, где свалка появится в следующие "
            "двенадцать месяцев.",
            "Убрать свалку стоит миллионы. Не дать ей появиться — стоит "
            "дорожного знака.",
            "И проверяли мы не случайным разбиением, а по времени: учились на "
            "прошлом, проверялись на будущем. Цифра скромнее. Зато честная.",
        ],
        "Вкладка «Прогноз», затем зоны риска на карте.",
        "Здесь говорите медленнее всего. После «Но главное не это» — пауза "
        "обязательно: фраза работает как поворот, ей нужно место.",
    ),
    (
        "2:14", "2:34", "Житель",
        [
            "У спутника есть предел: объекты меньше сорока метров он не видит. "
            "Это закрывает бот.",
            "Житель присылает точку и фото, система сверяет её с находками. "
            "Не совпало — значит человек нашёл то, чего спутник не увидел.",
        ],
        "Переписка с ботом, ответ системы.",
        "",
    ),
    (
        "2:34", "2:52", "Границы",
        [
            "Границы своей системы мы называем сами.",
            "Десять метров на пиксель. Проверка снимками высокого разрешения — "
            "выборочная, не по всему массиву.",
            "И главное: это оценка вероятности, а не юридическое доказательство. "
            "Решение о статусе объекта принимает инспектор после выезда.",
        ],
        "Раздел «Чего система не может» на лендинге.",
        "Тон здесь решает всё. Это не оправдание, а демонстрация того, что вы "
        "понимаете свой инструмент. Говорите так же ровно, как про прогноз: "
        "команда, которая знает свои пределы, выглядит сильнее той, которая "
        "утверждает, что их нет.",
    ),
    (
        "2:52", "3:00", "Финал",
        [
            "Система работает. Код открыт. Карта работает без интернета.",
            "VANTAGE.",
        ],
        "Слайдер «было и стало» — перетащите шторку. Затем логотип и ссылка "
        "на репозиторий.",
        "Последнее слово — название, и после него тишина. Не добавляйте "
        "«спасибо за внимание»: это гасит концовку.",
    ),
]

FORBIDDEN = [
    ("Не читайте с листа", "Слышно сразу. Выучите скелет из восьми фраз, дальше "
     "говорите своими словами — текст здесь опора, а не диктант."),
    ("Не перечисляйте технологии", "«Мы использовали Python, PyTorch, FastAPI» — "
     "это не аргумент, это список. Жюри интересует, что система делает, "
     "а не из чего собрана."),
    ("Не говорите «уникальный» и «не имеет аналогов»", "Эти слова означают, "
     "что сказать по существу нечего."),
    ("Не показывайте код", "В трёхминутном видео это потерянные секунды. Код "
     "есть в репозитории, ссылка в конце."),
    ("Не ускоряйтесь, чтобы всё влезло", "Если не помещаетесь — выкидывайте "
     "блок целиком. Скороговорка звучит как неуверенность."),
]

LEARNING = [
    ("День 1", "Скелет", "Восемь фраз подряд без опоры на текст. Это тридцать "
     "секунд, но это каркас всей речи."),
    ("День 2", "Блоки", "К каждой фразе скелета добавьте её блок. Не заучивайте "
     "дословно: важно, чтобы держались переходы — именно на них сбиваются."),
    ("День 3", "Прогон", "Под экран, с секундомером. Запишите на телефон и "
     "послушайте: свой голос со стороны показывает, где вы частите и где "
     "проглатываете конец фразы."),
    ("День 4", "Запись", "К этому моменту текст должен звучать так, будто вы "
     "придумываете его прямо сейчас."),
]

BEFORE = [
    "запись экрана 1920×1080, курсор видимый",
    "браузер в режиме презентации (клавиша P) — крупные шрифты",
    "сценарий на сайте открыт заранее и прощёлкан один раз",
    "микрофон не встроенный в ноутбук — гудение вентилятора слышно",
    "говорит один человек: три голоса в трёхминутном видео — это каша",
    "телефон в авиарежим, уведомления на компьютере выключены",
]

CHECKLIST = [
    "Видео короче трёх минут — проверьте, а не прикиньте",
    "Цифры в речи совпадают с тем, что на экране",
    "Звук ровный, без гудения и щелчков",
    "Ни одной ошибки на экране: красных сообщений, пустых панелей",
    "Ссылка на репозиторий видна в кадре в конце",
    "Файл открывается на чужом компьютере",
    "Загружено на YouTube или Google Диск с открытым доступом",
    "Доступ проверен из режима инкогнито",
]


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """«31 секунда», а не «31 секунд» — в документе, который читают вслух."""
    if n % 100 in range(11, 15):
        return forms[2]
    return {1: forms[0], 2: forms[1], 3: forms[1], 4: forms[1]}.get(n % 10, forms[2])


def word_count() -> tuple[int, float]:
    """Слова только из реплик: пометки «@ПАУЗА» вслух не произносятся."""
    words = sum(
        len(line.split())
        for _, _, _, lines, _, _ in BLOCKS
        for line in lines
        if not line.startswith("@")
    )
    return words, words / WPM * 60


def build() -> Path:
    from reportlab.lib.colors import Color
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    font, bold = register_cyrillic_font()
    out = REPO / "docs" / "examples" / "VANTAGE_речь.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)

    width, height = A4
    pdf = canvas.Canvas(str(out), pagesize=A4)
    pdf.setTitle("VANTAGE — речь для видео-демо")

    margin = 18 * mm
    gutter = 26 * mm        # левая колонка под тайминг и пометки
    text_left = margin + gutter
    right = width - margin
    top = height - margin
    floor = 20 * mm

    state = {"y": top, "page": 1}

    # ------------------------------------------------------------------ #
    #  Примитивы вёрстки
    # ------------------------------------------------------------------ #

    def paint_page():
        pdf.setFillColor(Color(*PAPER))
        pdf.rect(0, 0, width, height, stroke=0, fill=1)

    def footer():
        pdf.setFillColor(Color(*INK_3))
        pdf.setFont(font, 7.4)
        pdf.drawString(margin, 12 * mm, "VANTAGE · речь для видео-демо · 3 минуты")
        pdf.drawRightString(right, 12 * mm, str(state["page"]))

    def new_page():
        footer()
        pdf.showPage()
        state["page"] += 1
        state["y"] = top
        paint_page()

    def need(space_mm: float):
        if state["y"] - space_mm * mm < floor:
            new_page()

    def wrap(text: str, size: float, max_width: float, use_bold=False) -> list[str]:
        face = bold if use_bold else font
        words, lines, cur = text.split(), [], ""
        for w in words:
            probe = f"{cur} {w}".strip()
            if pdf.stringWidth(probe, face, size) <= max_width:
                cur = probe
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def para(body, *, size=9.4, color=INK_2, left=None, lead=4.5, use_bold=False):
        x = margin if left is None else left
        lines = wrap(body, size, right - x, use_bold)
        need(len(lines) * lead + 2)
        pdf.setFillColor(Color(*color))
        pdf.setFont(bold if use_bold else font, size)
        for line in lines:
            pdf.drawString(x, state["y"], line)
            state["y"] -= lead * mm
        state["y"] -= 1 * mm

    def heading(label: str):
        need(20)
        state["y"] -= 3 * mm
        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 13)
        pdf.drawString(margin, state["y"], label)
        state["y"] -= 2.6 * mm
        pdf.setStrokeColor(Color(*INK))
        pdf.setLineWidth(1)
        pdf.line(margin, state["y"], right, state["y"])
        state["y"] -= 7 * mm

    # ------------------------------------------------------------------ #
    #  Блоки речи
    # ------------------------------------------------------------------ #

    def block_height(lines, screen, tempo) -> float:
        """Высота блока в миллиметрах — чтобы перенести его целиком."""
        h = 2 + 8
        for line in lines:
            if line.startswith("@"):
                h += len(wrap(line[1:], 8.4, right - text_left, True)) * 4 + 2.4
            else:
                h += len(wrap(line, 11.4, right - text_left)) * 5.5 + 2.6
        h += 1
        for body in (screen, tempo):
            if body:
                h += len(wrap(body, 7.8, right - text_left)) * 3.7 + 1.6
        return h + 3

    def speech_block(t_from, t_to, title, lines, screen, tempo):
        # Блок переносится целиком. Речь читают с листа во время записи:
        # перевернуть страницу посреди фразы — значит сбиться.
        need(block_height(lines, screen, tempo))
        state["y"] -= 2 * mm

        block_top = state["y"] + 4 * mm

        pdf.setFillColor(Color(*RUST))
        pdf.setFont(bold, 9.6)
        pdf.drawString(margin, state["y"], t_from)
        pdf.setFillColor(Color(*INK_3))
        pdf.setFont(font, 7.6)
        pdf.drawString(margin, state["y"] - 4.2 * mm, f"до {t_to}")

        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 11)
        pdf.drawString(text_left, state["y"], title)
        state["y"] -= 8 * mm

        for line in lines:
            if line.startswith("@"):
                body = line[1:]
                sub = wrap(body, 8.4, right - text_left, True)
                need(len(sub) * 4 + 4)
                pdf.setFillColor(Color(*RUST))
                pdf.setFont(bold, 8.4)
                for s in sub:
                    pdf.drawString(text_left, state["y"], s)
                    state["y"] -= 4 * mm
                state["y"] -= 2.4 * mm
                continue

            sub = wrap(line, 11.4, right - text_left)
            need(len(sub) * 5.5 + 4)
            pdf.setFillColor(Color(*INK))
            pdf.setFont(font, 11.4)
            for s in sub:
                pdf.drawString(text_left, state["y"], s)
                state["y"] -= 5.5 * mm
            state["y"] -= 2.6 * mm

        state["y"] -= 1 * mm
        for label, body in (("НА ЭКРАНЕ", screen), ("КАК ГОВОРИТЬ", tempo)):
            if not body:
                continue
            sub = wrap(body, 7.8, right - text_left)
            need(len(sub) * 3.7 + 4)
            pdf.setFillColor(Color(*INK_3))
            pdf.setFont(bold, 6.4)
            pdf.drawString(margin, state["y"], label)
            pdf.setFont(font, 7.8)
            for s in sub:
                pdf.drawString(text_left, state["y"], s)
                state["y"] -= 3.7 * mm
            state["y"] -= 1.6 * mm

        # Вертикальная линейка слева — видно, где блок начался и кончился.
        # Рисуем только если блок не переехал на другую страницу.
        if state["y"] < block_top:
            pdf.setStrokeColor(Color(*RULE))
            pdf.setLineWidth(0.8)
            pdf.line(margin + 20 * mm, block_top, margin + 20 * mm, state["y"] + 3 * mm)

        state["y"] -= 3 * mm

    # ------------------------------------------------------------------ #
    #  Страница 1
    # ------------------------------------------------------------------ #

    paint_page()
    words, seconds = word_count()

    pdf.setFillColor(Color(*RUST))
    pdf.setFont(bold, 8)
    pdf.drawString(margin, state["y"], "FUTURE MINDS HACKATHON 2026 · ECOFIN")
    state["y"] -= 11 * mm

    pdf.setFillColor(Color(*INK))
    pdf.setFont(bold, 27)
    pdf.drawString(margin, state["y"], "Речь для видео")
    state["y"] -= 9 * mm

    pdf.setFillColor(Color(*INK_2))
    pdf.setFont(font, 11)
    mins, secs = divmod(int(seconds), 60)
    spare = 180 - int(seconds)
    pdf.drawString(
        margin, state["y"],
        f"{words} слов · {mins}:{secs:02d} речи · {spare} "
        f"{plural(spare, ('секунда', 'секунды', 'секунд'))} на паузы",
    )
    state["y"] -= 5.5 * mm

    pdf.setFillColor(Color(*INK_3))
    pdf.setFont(font, 9)
    pdf.drawString(margin, state["y"], "Лимит по Положению — 3 минуты. Темп 132 слова в минуту.")
    state["y"] -= 9 * mm

    pdf.setStrokeColor(Color(*INK))
    pdf.setLineWidth(1.6)
    pdf.line(margin, state["y"], right, state["y"])
    state["y"] -= 10 * mm

    heading("Скелет: выучите сначала это")
    para(
        "Восемь фраз. Если помните их — не собьётесь, даже забыв формулировку. "
        "Всё остальное достраивается на ходу.",
        size=9, color=INK_3,
    )
    state["y"] -= 2 * mm

    for i, phrase in enumerate(SKELETON, 1):
        lines = wrap(phrase, 10.4, right - margin - 9 * mm)
        need(len(lines) * 5 + 4)
        pdf.setFillColor(Color(*RUST))
        pdf.setFont(bold, 10.4)
        pdf.drawString(margin, state["y"], str(i))
        pdf.setFillColor(Color(*INK))
        pdf.setFont(font, 10.4)
        for line in lines:
            pdf.drawString(margin + 9 * mm, state["y"], line)
            state["y"] -= 5 * mm
        state["y"] -= 2 * mm

    state["y"] -= 3 * mm
    heading("Числа впишите до заучивания")
    para(
        "В речи четыре места, где звучит результат прогона. Заполните их прежде, "
        "чем начнёте учить, — иначе придётся переучивать.",
        size=9, color=INK_3,
    )
    state["y"] -= 2 * mm

    for where, what, value in NUMBERS:
        need(7)
        pdf.setFillColor(Color(*INK_3))
        pdf.setFont(bold, 8.2)
        pdf.drawString(margin, state["y"], where)
        pdf.setFillColor(Color(*INK))
        pdf.setFont(font, 9.2)
        pdf.drawString(margin + 18 * mm, state["y"], what)
        pdf.setFillColor(Color(*RUST))
        pdf.setFont(bold, 9.2)
        pdf.drawRightString(right, state["y"], value)
        state["y"] -= 3 * mm
        pdf.setStrokeColor(Color(*RULE))
        pdf.setLineWidth(0.6)
        pdf.line(margin, state["y"], right, state["y"])
        state["y"] -= 4.4 * mm

    # ------------------------------------------------------------------ #
    #  Речь
    # ------------------------------------------------------------------ #

    new_page()
    heading("Текст")

    for block in BLOCKS:
        speech_block(*block)

    # ------------------------------------------------------------------ #
    #  Хвост: всё, что не произносится вслух
    # ------------------------------------------------------------------ #

    # Без разрыва страницы: блоки речи переносятся целиком, поэтому после
    # последнего из них внизу остаётся полстраницы пустоты. Пусть её займёт
    # то, что вслух не произносится.
    heading("Что делать нельзя")
    for title, detail in FORBIDDEN:
        lines = wrap(detail, 8.6, right - margin - 4 * mm)
        need(len(lines) * 4 + 8)
        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 9.4)
        pdf.drawString(margin, state["y"], title)
        state["y"] -= 4.4 * mm
        pdf.setFillColor(Color(*INK_2))
        pdf.setFont(font, 8.6)
        for line in lines:
            pdf.drawString(margin + 4 * mm, state["y"], line)
            state["y"] -= 4 * mm
        state["y"] -= 2.4 * mm

    heading("Как учить")
    for day, what, detail in LEARNING:
        lines = wrap(detail, 8.6, right - text_left)
        need(len(lines) * 4 + 8)
        pdf.setFillColor(Color(*RUST))
        pdf.setFont(bold, 8.6)
        pdf.drawString(margin, state["y"], day)
        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 9.4)
        pdf.drawString(text_left, state["y"], what)
        state["y"] -= 4.4 * mm
        pdf.setFillColor(Color(*INK_2))
        pdf.setFont(font, 8.6)
        for line in lines:
            pdf.drawString(text_left, state["y"], line)
            state["y"] -= 4 * mm
        state["y"] -= 2.4 * mm

    heading("Перед записью")
    for item in BEFORE:
        lines = wrap(item, 8.8, right - margin - 5 * mm)
        need(len(lines) * 4.2 + 3)
        pdf.setFillColor(Color(*RUST))
        pdf.circle(margin + 1.4 * mm, state["y"] + 1 * mm, 1.1 * mm, stroke=0, fill=1)
        pdf.setFillColor(Color(*INK_2))
        pdf.setFont(font, 8.8)
        for line in lines:
            pdf.drawString(margin + 5 * mm, state["y"], line)
            state["y"] -= 4.2 * mm
        state["y"] -= 1.4 * mm

    heading("Чек-лист перед отправкой")
    for item in CHECKLIST:
        lines = wrap(item, 8.8, right - margin - 7 * mm)
        need(len(lines) * 4.2 + 3)
        pdf.setStrokeColor(Color(*INK_3))
        pdf.setLineWidth(0.7)
        pdf.rect(margin, state["y"] - 0.4 * mm, 3 * mm, 3 * mm, stroke=1, fill=0)
        pdf.setFillColor(Color(*INK_2))
        pdf.setFont(font, 8.8)
        for line in lines:
            pdf.drawString(margin + 7 * mm, state["y"], line)
            state["y"] -= 4.2 * mm
        state["y"] -= 1.6 * mm

    footer()
    pdf.save()
    return out


if __name__ == "__main__":
    words, seconds = word_count()
    path = build()
    mins, secs = divmod(int(seconds), 60)
    print(f"Речь: {words} слов, {mins}:{secs:02d} — запас {180 - int(seconds)} с")
    print(f"Готово: {path.relative_to(REPO)}  ({path.stat().st_size / 1024:.0f} КБ)")
