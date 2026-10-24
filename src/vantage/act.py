"""Акт о выявлении несанкционированного размещения отходов.

Здесь проходит граница между «интересной аналитикой» и инструментом,
которым можно пользоваться завтра. Карта находок требует, чтобы кто-то
вручную переписал координаты, посчитал ущерб и нашёл статью кодекса.
Готовый документ не требует ничего.

Главное правило этого модуля
----------------------------
**Модель предлагает, человек подтверждает.**

Документ, сформированный автоматически на основе вероятностной модели,
не может быть официальным. Поэтому акт проходит два состояния:

    ЧЕРНОВИК   — сформирован моделью. На каждой странице печатается
                 предупреждение, документ помечен как непроверенный,
                 выгрузка как официального запрещена программно.

    ПРОВЕРЕН   — человек нажал кнопку подтверждения, указал своё имя
                 и должность. Только после этого документ получает
                 статус официального и отметку с именем проверяющего.

Переход возможен только явным вызовом :meth:`ActDraft.approve`, и попытка
отрендерить официальную версию без подтверждения падает с исключением,
а не печатает документ «на всякий случай».

Это не бюрократия ради бюрократии. Автоматически сгенерированный
юридический документ на основе ML-модели — прямая ответственность,
и на защите этот вопрос задают почти всегда.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

ActStatus = Literal["draft", "approved"]

#: Шрифты Windows и Linux, содержащие кириллицу. Встроенные шрифты
#: reportlab (Helvetica, Times) кириллицу НЕ содержат — без регистрации
#: TTF документ выйдет с пустыми квадратами вместо русского текста.
FONT_CANDIDATES = (
    ("VantageSans", r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    ("VantageSans", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("VantageSans", r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"),
    ("VantageSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("VantageSans", "/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)

#: Шрифт служебного документа — Times New Roman, как требуют правила
#: документирования для официальной переписки. Liberation Serif и DejaVu
#: Serif — замена с теми же метриками (Linux, CI). Если нет ни одного,
#: берётся рубленый шрифт из FONT_CANDIDATES: кириллица важнее гарнитуры.
DOCUMENT_FONT_CANDIDATES = (
    ("VantageSerif", r"C:\Windows\Fonts\times.ttf", r"C:\Windows\Fonts\timesbd.ttf"),
    ("VantageSerif", "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
    ("VantageSerif", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ("VantageSerif", "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
     "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"),
)

#: Названия месяцев в именительном падеже.
#: Явная таблица, а не locale: setlocale меняет состояние всего процесса,
#: русская локаль может быть не установлена на машине, а на Windows её
#: имя отличается от POSIX. Тихий результат такого сбоя — «May 2022»
#: в официальном документе на русском языке.
#:
#: Именительный, а не родительный: месяц стоит без числа («Дата
#: возникновения: май 2022»). Родительный («мая 2022») верен только с
#: днём, и в акте до 27 сентября стояло именно это — ошибка в документе,
#: который подписывает должностное лицо.
MONTHS_RU = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def format_month_year(value: date) -> str:
    """«май 2022»: месяц без числа — в именительном падеже."""
    return f"{MONTHS_RU[value.month - 1]} {value.year}"


DRAFT_WARNING = (
    "ЧЕРНОВИК. Документ сформирован автоматически системой VANTAGE на основе "
    "вероятностной модели и НЕ является официальным. Требуется проверка и "
    "подтверждение уполномоченным лицом."
)

DISCLAIMER = (
    "Результаты получены методом дистанционного зондирования и представляют собой "
    "оценку вероятности, а не юридическое доказательство. Решение о статусе объекта, "
    "размере ущерба и применении санкций принимается уполномоченным лицом по итогам "
    "выездной проверки."
)


class ActNotApprovedError(RuntimeError):
    """Попытка выгрузить непроверенный документ как официальный."""


@dataclass
class Approval:
    """Подтверждение человеком."""

    reviewer_name: str
    reviewer_position: str
    approved_at: datetime = field(default_factory=datetime.now)
    note: str = ""

    def __post_init__(self) -> None:
        if not self.reviewer_name.strip():
            raise ValueError("нельзя подтвердить документ без имени проверяющего")
        if not self.reviewer_position.strip():
            raise ValueError("нельзя подтвердить документ без должности проверяющего")


@dataclass
class ActDraft:
    """Акт о выявлении объекта.

    Собирается из результатов пайплайна: геометрия и дата от детектора,
    доказательства от слоя объяснимости, суммы от денежного слоя,
    статья и штраф из конфигурации экономики.
    """

    candidate_id: str
    latitude: float
    longitude: float
    area_m2: float
    detected_date: date | None
    appeared_date: date | None

    evidence_text: str
    signals: dict[str, float]
    model_probability: float | None

    damage_p10_kzt: float
    damage_p50_kzt: float
    damage_p90_kzt: float
    mass_t_p50: float
    co2e_t_p50: float

    penalty_article: str
    penalty_article_title: str
    penalty_mrp: int
    penalty_kzt: float

    verification_providers: int = 0
    verification_texture: float | None = None
    #: Сколько CO₂-экв уже ушло в атмосферу за годы, что объект лежит.
    #: В акте это отдельная строка: вред уже причинённый и вред, который
    #: ещё можно предотвратить, — разные основания, и путать их нельзя.
    co2e_emitted_t_p50: float = 0.0
    age_years: float = 0.0

    approval: Approval | None = None
    created_at: datetime = field(default_factory=datetime.now)

    # ------------------------------------------------------------------ #

    @property
    def status(self) -> ActStatus:
        return "approved" if self.approval else "draft"

    @property
    def is_official(self) -> bool:
        return self.approval is not None

    def approve(self, reviewer_name: str, reviewer_position: str, note: str = "") -> ActDraft:
        """Подтвердить документ человеком.

        Единственный способ сделать акт официальным. Имя и должность
        обязательны: подпись «система» юридически бессмысленна.
        """
        if self.approval is not None:
            raise RuntimeError(f"акт {self.candidate_id} уже подтверждён")
        self.approval = Approval(reviewer_name.strip(), reviewer_position.strip(), note=note)
        log.info("Акт %s подтверждён: %s (%s)", self.candidate_id, reviewer_name, reviewer_position)
        return self

    def coordinates_text(self) -> str:
        """Координаты в формате, пригодном для навигатора."""
        return f"{self.latitude:.6f}, {self.longitude:.6f}"

    def damage_text(self) -> str:
        return (
            f"{_kzt(self.damage_p10_kzt)} – {_kzt(self.damage_p90_kzt)} ₸ "
            f"(медианная оценка {_kzt(self.damage_p50_kzt)} ₸)"
        )

    @classmethod
    def from_pipeline(
        cls,
        candidate_row,
        assessment,
        evidence,
        economics,
        *,
        article_key: str | None = None,
    ) -> ActDraft:
        """Собрать акт из выходов пайплайна.

        Координаты берутся из геометрии в WGS84 — акт читает человек
        с телефоном, а не GIS-система.
        """
        penalty = economics.section("penalty")
        key = article_key or penalty["default_article"]
        article = penalty["articles"][key]

        point = candidate_row.geometry.representative_point()
        appeared = candidate_row.get("break_date")

        return cls(
            candidate_id=str(candidate_row.get("candidate_id", "?")),
            latitude=float(point.y),
            longitude=float(point.x),
            area_m2=float(candidate_row.get("area_m2", 0.0)),
            detected_date=date.today(),
            appeared_date=_as_date(appeared),
            evidence_text=evidence.to_text() if evidence else "",
            signals=dict(evidence.strength) if evidence else {},
            model_probability=_opt_float(candidate_row.get("probability")),
            damage_p10_kzt=assessment.net_damage_kzt.p10,
            damage_p50_kzt=assessment.net_damage_kzt.p50,
            damage_p90_kzt=assessment.net_damage_kzt.p90,
            mass_t_p50=assessment.mass_t.p50,
            co2e_t_p50=assessment.co2e_t.p50,
            co2e_emitted_t_p50=assessment.co2e_emitted_t.p50,
            age_years=assessment.age_years,
            penalty_article=str(article["article"]),
            penalty_article_title=str(article["title"]),
            penalty_mrp=int(assessment.penalty_mrp),
            penalty_kzt=float(assessment.penalty_kzt),
            verification_providers=int(candidate_row.get("verify_providers", 0) or 0),
            verification_texture=_opt_float(candidate_row.get("verify_texture")),
        )


# --------------------------------------------------------------------------- #
#  Рендеринг
# --------------------------------------------------------------------------- #


def register_cyrillic_font() -> tuple[str, str]:
    """Зарегистрировать TTF с кириллицей и вернуть (обычный, жирный).

    Встроенные шрифты reportlab кириллицу не содержат: без этого шага
    весь русский текст в PDF выйдет пустыми квадратами. Ошибка тихая —
    документ создастся, просто окажется нечитаемым.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, regular, bold in FONT_CANDIDATES:
        if Path(regular).exists():
            bold_name = f"{name}-Bold"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, regular))
                pdfmetrics.registerFont(TTFont(bold_name, bold if Path(bold).exists() else regular))
            return name, bold_name

    raise RuntimeError(
        "не найден шрифт с поддержкой кириллицы. Установите DejaVu Sans "
        "или укажите путь к TTF в FONT_CANDIDATES."
    )


def register_document_font() -> tuple[str, str]:
    """Шрифт служебного документа: Times New Roman или замена с теми же метриками."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, regular, bold in DOCUMENT_FONT_CANDIDATES:
        if Path(regular).exists():
            bold_name = f"{name}-Bold"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, regular))
                pdfmetrics.registerFont(TTFont(bold_name, bold if Path(bold).exists() else regular))
            return name, bold_name
    return register_cyrillic_font()


def render_pdf(act: ActDraft, path: str | Path, *, allow_draft: bool = True,
               sample: bool = False) -> Path:
    """Отрендерить акт в PDF.

    ``allow_draft=False`` включает строгий режим выгрузки официального
    документа: непроверенный акт вызовет исключение, а не напечатается
    с оговоркой мелким шрифтом.

    ``sample=True`` — образец заполнения: поверх листа печатается
    «ОБРАЗЕЦ». Нужен для документов, которые показывают заказчику, как
    выглядит акт: подтверждённый акт без такой пометки с вымышленной
    подписью читался бы как настоящий.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    if not act.is_official and not allow_draft:
        raise ActNotApprovedError(
            f"акт {act.candidate_id} не подтверждён человеком и не может быть "
            "выгружен как официальный документ. Вызовите approve() с именем "
            "и должностью проверяющего."
        )

    # Оформление служебного документа: только чёрный, Times New Roman 12,
    # поля 30/15/20/20 мм, таблицы тонкой линией. До 27 сентября акт был
    # цветным (красная и зелёная лента, золотые разделители, розовая
    # плашка) — на проекторе красиво, в папке с документами чужеродно, и
    # на чёрно-белом принтере половина выходила серыми пятнами.
    font, font_bold = register_document_font()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    black = colors.black
    body = ParagraphStyle("body", fontName=font, fontSize=12, leading=14.5, textColor=black)
    small = ParagraphStyle("small", parent=body, fontSize=10, leading=12)
    cell = ParagraphStyle("cell", parent=body, fontSize=11, leading=13)
    head = ParagraphStyle("head", parent=body, fontName=font_bold, spaceBefore=6, spaceAfter=2)
    title = ParagraphStyle("title", parent=body, fontName=font_bold, fontSize=14, leading=17,
                           alignment=TA_CENTER)
    subtitle = ParagraphStyle("subtitle", parent=body, alignment=TA_CENTER)
    mark = ParagraphStyle("mark", parent=body, fontName=font_bold, alignment=TA_RIGHT)

    def esc(text: str) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def table(rows: list[tuple[str, str]]) -> Table:
        data = [[Paragraph(esc(k), cell), Paragraph(esc(v), cell)] for k, v in rows]
        tbl = Table(data, colWidths=[62 * mm, None])
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return tbl

    def tenge(value: float) -> str:
        return f"{_kzt(value)} тенге"

    story: list = []
    # Пометка вместо цветной ленты: так помечают проекты и образцы
    # служебных документов.
    if sample:
        story.append(Paragraph("ОБРАЗЕЦ", mark))
    if not act.is_official:
        story.append(Paragraph("ПРОЕКТ", mark))
    story += [Spacer(1, 2 * mm), Paragraph("АКТ", title),
              Paragraph("о выявлении несанкционированного размещения отходов", subtitle),
              Spacer(1, 4 * mm)]

    when = (act.approval.approved_at.strftime("%d.%m.%Y") if act.approval
            else "«___» ____________ 20___ г.")
    place = Table([[Paragraph("г. Астана", body),
                    Paragraph(f"{esc(when)}&nbsp;&nbsp;&nbsp;№ {esc(act.candidate_id)}", mark)]],
                  colWidths=[None, 95 * mm])
    place.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [place, Spacer(1, 4 * mm),
              Paragraph("Основание: результаты дистанционного мониторинга территории "
                        "(система Vantage AI; снимки Sentinel-2, Sentinel-1, Landsat 8/9 "
                        "и снимки высокого разрешения).", body)]

    story += [Paragraph("1. Сведения об объекте", head), table([
        ("Координаты (WGS 84)", act.coordinates_text()),
        ("Площадь", f"{_kzt(act.area_m2)} м²"),
        ("Дата возникновения (по снимкам)",
         format_month_year(act.appeared_date) if act.appeared_date else "не определена"),
        ("Дата выявления",
         act.detected_date.strftime("%d.%m.%Y") if act.detected_date else "—"),
        ("Оценка массы отходов", f"{_kzt(act.mass_t_p50)} т"),
    ])]

    grounds: list[tuple[str, str]] = []
    if act.evidence_text:
        grounds.append(("Признаки по снимкам", _evidence_short(act.evidence_text)))
    if act.verification_providers:
        grounds.append(("Независимых источников съёмки", str(act.verification_providers)))
    if act.model_probability is not None:
        # Не проценты: оценка не откалибрована, и «37%» читается как
        # вероятность, которой она не является (AI_RESULTS.md, 1с).
        score = f"{act.model_probability:.2f}".replace(".", ",")
        grounds.append(("Оценка модели по снимку", f"{score} из 1 (подсказка, не вероятность)"))
    if grounds:
        story += [Paragraph("2. Основания выявления", head), table(grounds)]

    emissions = f"{_kzt(act.co2e_t_p50)} т CO₂-экв."
    if act.co2e_emitted_t_p50 > 0 and act.co2e_t_p50:
        # Уже причинённый вред — отдельно от прогнозного: возмещению
        # подлежит причинённый, и должностное лицо видит их порознь.
        share = act.co2e_emitted_t_p50 / act.co2e_t_p50 * 100
        emissions += f", из них уже выброшено {_kzt(act.co2e_emitted_t_p50)} т ({share:.0f} %)"
    story += [Paragraph("3. Оценка ущерба (метод Монте-Карло)", head), table([
        ("Диапазон оценки (P10–P90)",
         f"{_kzt(act.damage_p10_kzt)} – {tenge(act.damage_p90_kzt)}"),
        ("Медианная оценка", tenge(act.damage_p50_kzt)),
        ("Выбросы метана за 20 лет", emissions),
    ])]

    story += [Paragraph("4. Применимая норма", head), table([
        ("Статья", f"{act.penalty_article}. {act.penalty_article_title}"),
        ("Размер санкции", f"{act.penalty_mrp} МРП = {tenge(act.penalty_kzt)}"),
    ])]

    # Итог выезда заполняет человек: именно эта графа делает проект актом.
    story += [Paragraph("5. Результаты выездной проверки", head), table([
        ("Отходы на месте", "□ обнаружены   □ не обнаружены   □ не установлено"),
        ("Вид отходов", "□ бытовые   □ строительные   □ грунт   □ иное: ________"),
        ("Фотографии с координатами", "прилагаются, ____ шт."),
    ])]

    story.append(Spacer(1, 6 * mm))
    if act.approval:
        sign_rows = [["Проверил:", esc(act.approval.reviewer_position), "подпись",
                      esc(act.approval.reviewer_name)]]
        if act.approval.note:
            story += [Paragraph(f"Примечание: {esc(act.approval.note)}", small),
                      Spacer(1, 4 * mm)]
    else:
        sign_rows = [["Составил:", "должность", "подпись", "Ф. И. О."],
                     ["Проверил:", "должность", "подпись", "Ф. И. О."]]
    signs = Table([[Paragraph(c, small) for c in row] for row in sign_rows],
                  colWidths=[22 * mm, 60 * mm, 30 * mm, None], rowHeights=10 * mm)
    signs.setStyle(TableStyle([
        ("LINEABOVE", (1, 0), (-1, -1), 0.5, black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(signs)

    story += [Spacer(1, 3 * mm), Paragraph(
        "Оценка получена дистанционно и не является юридическим доказательством. "
        "Статус объекта, размер ущерба и применение санкций устанавливает "
        "уполномоченное лицо по итогам выездной проверки.", small)]

    def footer(canvas_, doc_) -> None:
        canvas_.saveState()
        canvas_.setFont(font, 9)
        canvas_.setFillColor(black)
        canvas_.drawString(30 * mm, 12 * mm, f"Акт № {act.candidate_id} · Vantage AI")
        canvas_.drawRightString(A4[0] - 15 * mm, 12 * mm, f"стр. {doc_.page}")
        canvas_.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=30 * mm, rightMargin=15 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm,
                            title=f"Акт {act.candidate_id}", author="Vantage AI")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    log.info("Акт %s сохранён (%s): %s", act.candidate_id, act.status, path)
    return path


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #


def _evidence_short(text: str) -> str:
    """«сработало признаков: 2 из 5. падение … — 100%; …» → без процентов.

    Проценты силы признака в акте читаются как вероятность, а это доля от
    насыщения шкалы. В документе нужно, КАКИЕ признаки совпали, а не их
    внутренняя шкала.
    """
    import re

    head, sep, rest = text.partition(". ")
    if not sep:
        return text
    names = [re.sub(r"\s*—\s*\d+%$", "", s.strip()) for s in rest.split(";") if s.strip()]
    head = head.replace("сработало признаков", "совпало признаков")
    return f"{head} ({', '.join(names)})" if names else head


def _kzt(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _wrap(text: str, width: int) -> list[str]:
    """Разбить строку по словам — reportlab сам перенос не делает."""
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 <= width:
            current = f"{current} {word}".strip()
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _as_date(value) -> date | None:
    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
        return pd.Timestamp(value).date()
    except Exception:
        return None


def _opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        import math

        result = float(value)
        return None if math.isnan(result) else result
    except (TypeError, ValueError):
        return None


__all__ = [
    "DISCLAIMER",
    "DRAFT_WARNING",
    "MONTHS_RU",
    "ActDraft",
    "ActNotApprovedError",
    "ActStatus",
    "Approval",
    "format_month_year",
    "register_cyrillic_font",
    "register_document_font",
    "render_pdf",
]
