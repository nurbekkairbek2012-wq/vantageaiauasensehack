"""Справка для нового участника: PDF на три страницы.

Зачем отдельный документ, если есть README. README читают, когда уже
решили разбираться. Эту справку отправляют человеку, который ещё не
знает, что за проект и стоит ли в него влезать: ссылки, суть, список
задач — всё в одном файле, без прокрутки по репозиторию.

Вёрстка идёт потоком с автоматическим переносом страниц. Ручное
управление страницами тут уже приводило к тому, что текст молча
уезжал под подвал: reportlab не считает это ошибкой.

Запуск:
    python scripts/make_handoff.py
Результат:
    docs/examples/VANTAGE_справка.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from vantage.act import register_cyrillic_font

REPO_URL = "https://github.com/k41270075-stack/hakathon"
SITE_URL = "https://hakathon-amber-three.vercel.app/"

PAPER = (0.969, 0.957, 0.937)
INK = (0.086, 0.075, 0.059)
INK_2 = (0.29, 0.267, 0.235)
INK_3 = (0.522, 0.49, 0.447)
RUST = (0.722, 0.290, 0.122)
RULE = (0.867, 0.835, 0.784)


def build() -> Path:
    from reportlab.lib.colors import Color
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    font, bold = register_cyrillic_font()
    out = REPO / "docs" / "examples" / "VANTAGE_справка.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)

    width, height = A4
    pdf = canvas.Canvas(str(out), pagesize=A4)
    pdf.setTitle("VANTAGE — справка для нового участника")

    margin = 18 * mm
    right = width - margin
    top = height - margin
    floor = 20 * mm          # ниже этой отметки начинается подвал

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
        pdf.drawString(margin, 12 * mm, "VANTAGE · Future Minds Hackathon 2026 · трек EcoFin")
        pdf.drawRightString(right, 12 * mm, str(state["page"]))

    def new_page():
        footer()
        pdf.showPage()
        state["page"] += 1
        state["y"] = top
        paint_page()

    def need(space_mm: float):
        """Перенести на новую страницу, если блок не помещается целиком."""
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

    def para(body: str, *, size=9.6, color=INK_2, indent=0.0, use_bold=False, lead=4.6):
        lines = wrap(body, size, right - margin - indent, use_bold)
        need(len(lines) * lead + 2)
        pdf.setFillColor(Color(*color))
        pdf.setFont(bold if use_bold else font, size)
        for line in lines:
            pdf.drawString(margin + indent, state["y"], line)
            state["y"] -= lead * mm
        state["y"] -= 1 * mm

    def heading(label: str, number: str = ""):
        need(16)
        state["y"] -= 3 * mm
        if number:
            pdf.setFillColor(Color(*RUST))
            pdf.setFont(bold, 8)
            pdf.drawString(margin, state["y"] + 4.6 * mm, number)
        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 13)
        pdf.drawString(margin, state["y"], label)
        state["y"] -= 2.6 * mm
        pdf.setStrokeColor(Color(*INK))
        pdf.setLineWidth(1)
        pdf.line(margin, state["y"], right, state["y"])
        state["y"] -= 6 * mm

    def subheading(label: str, color=INK_3):
        # Резервируем место не только под сам подзаголовок, но и под первый
        # блок под ним: иначе заголовок остаётся сиротой в самом низу
        # страницы, а то, что он озаглавливает, уезжает на следующую.
        need(30)
        state["y"] -= 1 * mm
        pdf.setFillColor(Color(*color))
        pdf.setFont(bold, 8.6)
        pdf.drawString(margin, state["y"], label)
        state["y"] -= 6.5 * mm

    def link_row(label: str, url: str):
        need(8)
        pdf.setFillColor(Color(*INK_3))
        pdf.setFont(font, 8.6)
        pdf.drawString(margin, state["y"], label)
        pdf.setFillColor(Color(*RUST))
        pdf.setFont(bold, 10)
        pdf.drawString(margin + 30 * mm, state["y"], url)
        pdf.linkURL(url, (margin + 30 * mm, state["y"] - 2, right, state["y"] + 10), relative=0)
        state["y"] -= 6.4 * mm

    def bullet(title: str, detail: str):
        lines = wrap(detail, 8.4, right - margin - 5 * mm)
        need(len(lines) * 3.9 + 6)
        pdf.setFillColor(Color(*RUST))
        pdf.circle(margin + 1.4 * mm, state["y"] + 1 * mm, 1.1 * mm, stroke=0, fill=1)
        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 9)
        pdf.drawString(margin + 5 * mm, state["y"], title)
        state["y"] -= 4 * mm
        pdf.setFillColor(Color(*INK_2))
        pdf.setFont(font, 8.4)
        for line in lines:
            pdf.drawString(margin + 5 * mm, state["y"], line)
            state["y"] -= 3.9 * mm
        state["y"] -= 1.8 * mm

    def component(name: str, detail: str):
        lines = wrap(detail, 8.6, right - margin - 4 * mm)
        need(len(lines) * 3.9 + 7)
        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 9.4)
        pdf.drawString(margin, state["y"], name)
        state["y"] -= 4.2 * mm
        pdf.setFillColor(Color(*INK_2))
        pdf.setFont(font, 8.6)
        for line in lines:
            pdf.drawString(margin + 4 * mm, state["y"], line)
            state["y"] -= 3.9 * mm
        state["y"] -= 2 * mm

    def task(number: str, title: str, detail: str, where: str = "", blocking=False):
        lines = wrap(detail, 8.8, right - margin - 8 * mm)
        need(len(lines) * 3.9 + (4 if where else 0) + 10)
        box_top = state["y"] + 4 * mm

        pdf.setFillColor(Color(*(RUST if blocking else INK_3)))
        pdf.setFont(bold, 9)
        pdf.drawString(margin, state["y"], number)
        pdf.setFillColor(Color(*INK))
        pdf.setFont(bold, 9.8)
        pdf.drawString(margin + 8 * mm, state["y"], title)
        state["y"] -= 4.4 * mm

        pdf.setFillColor(Color(*INK_2))
        pdf.setFont(font, 8.8)
        for line in lines:
            pdf.drawString(margin + 8 * mm, state["y"], line)
            state["y"] -= 3.9 * mm

        if where:
            pdf.setFillColor(Color(*INK_3))
            pdf.setFont(font, 8)
            pdf.drawString(margin + 8 * mm, state["y"], where)
            state["y"] -= 3.9 * mm

        if blocking:
            pdf.setStrokeColor(Color(*RUST))
            pdf.setLineWidth(1.6)
            pdf.line(margin - 2 * mm, box_top, margin - 2 * mm, state["y"] + 2 * mm)

        state["y"] -= 2.4 * mm

    # ================================================================== #
    #  Содержание
    # ================================================================== #

    paint_page()

    pdf.setFillColor(Color(*RUST))
    pdf.setFont(bold, 8)
    pdf.drawString(margin, state["y"], "СПРАВКА ДЛЯ НОВОГО УЧАСТНИКА")
    state["y"] -= 11 * mm

    pdf.setFillColor(Color(*INK))
    pdf.setFont(bold, 30)
    pdf.drawString(margin, state["y"], "VANTAGE")
    state["y"] -= 8 * mm
    pdf.setFillColor(Color(*INK_2))
    pdf.setFont(font, 13)
    pdf.drawString(margin, state["y"], "Поиск несанкционированных свалок по спутниковым снимкам")
    state["y"] -= 11 * mm

    pdf.setStrokeColor(Color(*RUST))
    pdf.setLineWidth(2)
    pdf.line(margin, state["y"], margin + 40 * mm, state["y"])
    state["y"] -= 9 * mm

    link_row("Репозиторий", REPO_URL)
    link_row("Сайт", SITE_URL)
    state["y"] -= 2 * mm

    # ---------- О чём проект ---------- #
    heading("О чём проект", "01")
    para("Вокруг любого города есть свалки, о которых власти не знают: их никто не считал, "
         "потому что никто не видел. Обойти степь пешком невозможно, дроны не покрывают "
         "такую площадь, а находят объект обычно по жалобе — то есть поздно.")
    para("Мы берём бесплатные спутниковые снимки за восемь лет и ищем места, где поверхность "
         "изменилась необратимо: растительность исчезла и не вернулась, появился открытый грунт. "
         "Для каждой находки считаем координаты, месяц возникновения, массу отходов, стоимость "
         "уборки в тенге и применимую статью КоАП — и складываем это в готовый черновик акта.")
    para("Отдельно система предсказывает, где свалка появится в следующие 12 месяцев. "
         "Убрать свалку стоит миллионы; не дать ей появиться — стоит дорожного знака.",
         use_bold=True, color=INK)

    # ---------- Пять признаков ---------- #
    heading("Главная сложность", "02")
    para("На снимке с разрешением 10 метров свалка выглядит как серое пятно — ровно как карьер, "
         "стройплощадка или отвал грунта. Различает их не картинка, а комбинация пяти "
         "физических признаков:")

    for name, detail in [
        ("Падение NDVI без возврата",
         "растительность гибнет навсегда · отсекает пашню: там NDVI возвращается каждую весну"),
        ("Рост индекса открытого грунта",
         "поверхность становится минеральной · отсекает плотную застройку"),
        ("Отклик в коротковолновом ИК",
         "полимеры имеют характерное поглощение · отсекает чистый грунт"),
        ("Нестабильность по радару",
         "поверхность меняется от съёмки к съёмке · отсекает КАРЬЕР: его стенки стабильны неделями"),
        ("Тепловая аномалия",
         "гниющая органика греет тело свалки · отсекает СНЕГОСВАЛКУ: она холоднее фона"),
    ]:
        bullet(name, detail)

    para("Модель не выдаёт вердикт «свалка / не свалка». Она показывает, какие признаки "
         "сработали и насколько сильно. Решение принимает человек.",
         use_bold=True, color=INK)

    # ---------- Из чего состоит ---------- #
    heading("Из чего состоит система", "03")

    component("Карта",
              "лендинг и приложение: список объектов, снимки «было и стало» за разные годы, "
              "таймлайн появления, зоны риска, обучающий тур. Работает без интернета.")
    component("Telegram-бот",
              "двусторонний. Наружу — оповещения службе о новых объектах. Внутрь — сообщения "
              "жителей с фото и геолокацией. Каждое сверяется со спутниковыми находками: "
              "совпало — независимое подтверждение, не совпало — объект, которого спутник не "
              "увидел. Именно это закрывает главное ограничение системы: слепоту к объектам "
              "меньше 30–50 квадратных метров. Идентификатор отправителя не хранится, "
              "только необратимый хеш.")
    component("HTTP-сервис",
              "ролевой доступ. Житель видит зону риска без координат, служба — точку и акт. "
              "Оператор вывоза не может подтверждать акты: у него коммерческий интерес "
              "в объёме работ. Каждое обращение к адресным данным пишется в журнал.")
    component("Генератор актов",
              "готовый PDF со статусом «черновик». Официальным документ становится только "
              "после подтверждения человеком — с именем и должностью.")

    # ---------- Состояние ---------- #
    heading("В каком состоянии проект сейчас", "04")
    para("Код написан целиком: 495 тестов, все модули на месте, сайт развёрнут, "
         "экономические цифры имеют источники.", color=INK)
    para("Но на карте сейчас ВЫДУМАННЫЕ объекты — генератор случайных точек для отладки "
         "интерфейса. Они помечены красной плашкой «демо-данные». Чтобы появились настоящие "
         "находки, нужно разметить обучающую выборку и запустить полный прогон по Астане. "
         "Это первые две задачи ниже.", color=INK, use_bold=True)

    # ---------- Задачи ---------- #
    heading("Что не сделано", "05")
    para("Полный список с примерами кода — в файле docs/TODO.md")

    subheading("БЛОКИРУЕТ ВСЁ ОСТАЛЬНОЕ", RUST)

    task("1", "Разметить обучающую выборку", blocking=True,
         detail="Часть меток берётся из OpenStreetMap автоматически: полигоны ТБО как "
                "положительные примеры, карьеры и стройки как отрицательные. Остальное надо "
                "просмотреть глазами и проставить 0 или 1.",
         where="src/vantage/labels.py · harvest_labels и manual_queue")

    task("2", "Запустить настоящий прогон по Астане", blocking=True,
         detail="Пайплайн собран, но полный прогон по области не запускался: нужна потайловая "
                "обработка. Метод AOI.tiles(20000) уже есть, оркестрации по плиткам нет.",
         where="src/vantage/pipeline.py")

    task("3", "Сделать страницу для ручной разметки",
         detail="Самое полезное, что можно добавить прямо сейчас: показывать два снимка «до и "
                "после», три кнопки — свалка, не свалка, непонятно — и писать результат в файл. "
                "Ускорит задачу 1 в разы.",
         where="новая страница в web/")

    subheading("МОДУЛИ ГОТОВЫ, НО НЕ СОЕДИНЕНЫ В ЦЕПОЧКУ")

    task("4", "Радар и тепло не влияют на решение",
         detail="Оба признака считаются, но детектор изменений использует только два оптических. "
                "Радар и тепло попадают только в панель объяснимости.",
         where="src/vantage/sar.py, thermal.py, change.py")

    task("5", "Доверификация не вызывается из пайплайна",
         detail="Модуль умеет тянуть снимки высокого разрешения от нескольких провайдеров и "
                "оценивать текстуру, но на реальных объектах не запускался ни разу.",
         where="src/vantage/verify.py")

    task("6", "Контроль устранения не получает данных",
         detail="Логика различения «убрали» и «засыпали грунтом» написана и покрыта тестами, "
                "но пайплайн не собирает историю наблюдений после даты обнаружения.",
         where="src/vantage/removal.py")

    subheading("РАЗВЁРНУТО НЕ ВСЁ")

    task("7", "Запустить Telegram-бота",
         detail="Код готов и покрыт тестами, но бот нигде не работает: нужен токен от @BotFather. "
                "Скрипт настройки читает токен так, что он не попадает ни в файлы, ни в историю "
                "команд. Fly.io выбран потому, что бесплатные хостинги усыпляют контейнер без "
                "входящих запросов, а бот их не получает: он сам ходит в Telegram.",
         where="deploy/setup-bot.ps1 · инструкция в deploy/README.md")

    task("8", "Развернуть HTTP-сервис (по желанию)",
         detail="Отложено осознанно: сервис отдаёт точные координаты и суммы ущерба, публиковать "
                "их в открытый доступ до защиты незачем. Кнопка «черновик акта» на сайте пока "
                "печатает документ прямо в браузере, без сервера.",
         where="deploy/Dockerfile, цель api")

    subheading("ДАННЫЕ, КОТОРЫЕ НЕ ДОБЫТЬ КОДОМ")

    task("9", "Узнать тариф вывоза за тонну по Астане",
         detail="82% разброса в оценке ущерба даёт именно этот параметр. Один звонок оператору "
                "сузит все цифры сильнее, чем уточнение всех остальных величин вместе взятых. "
                "Сейчас стоит оценка, выведенная из тарифа Алматы.",
         where="config/economics_astana.yaml")

    task("10", "Съездить на найденную координату и снять видео",
         detail="Кадр «вот спутниковый снимок, а вот я стою на этой точке» — самый сильный слайд "
                "презентации. Три часа времени и цена такси.")

    # ---------- С чего начать ---------- #
    heading("С чего начать", "06")
    para("1.  Открыть сайт по ссылке на первой странице и пройти обучающий тур — "
         "пять минут, показывает весь функционал.")
    para("2.  Прочитать docs/ARCHITECTURE.md — карта системы за десять минут.")
    para("3.  Открыть docs/TODO.md, выбрать задачу и отметить её галочкой прямо в файле, "
         "чтобы было видно, кто что взял.")
    para("4.  Установка: python -m venv .venv, затем pip install -e \".[dev,ml,service]\". "
         "Подробности и особенности Windows — в README.")

    footer()
    pdf.showPage()
    pdf.save()
    return out


if __name__ == "__main__":
    path = build()
    print(f"Готово: {path}")
