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

#: Названия месяцев в родительном падеже.
#: Явная таблица, а не locale: setlocale меняет состояние всего процесса,
#: русская локаль может быть не установлена на машине, а на Windows её
#: имя отличается от POSIX. Тихий результат такого сбоя — «May 2022»
#: в официальном документе на русском языке.
MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def format_month_year(value: date) -> str:
    """«май 2022» → «мая 2022»: как это читается в документе."""
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


def render_pdf(act: ActDraft, path: str | Path, *, allow_draft: bool = True) -> Path:
    """Отрендерить акт в PDF.

    ``allow_draft=False`` включает строгий режим выгрузки официального
    документа: непроверенный акт вызовет исключение, а не напечатается
    с оговоркой мелким шрифтом.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    if not act.is_official and not allow_draft:
        raise ActNotApprovedError(
            f"акт {act.candidate_id} не подтверждён человеком и не может быть "
            "выгружен как официальный документ. Вызовите approve() с именем "
            "и должностью проверяющего."
        )

    font, font_bold = register_cyrillic_font()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    page_width, page_height = A4
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle(f"Акт {act.candidate_id}")

    margin = 18 * mm
    y = page_height - margin

    def line(text: str, *, size: int = 10, bold: bool = False, gap: float = 5.5 * mm,
             color=colors.black, indent: float = 0.0) -> None:
        nonlocal y
        pdf.setFont(font_bold if bold else font, size)
        pdf.setFillColor(color)
        pdf.drawString(margin + indent, y, text)
        y -= gap

    def field_row(label: str, value: str) -> None:
        nonlocal y
        pdf.setFont(font, 10)
        pdf.setFillColor(colors.HexColor("#555555"))
        pdf.drawString(margin, y, label)
        pdf.setFont(font_bold, 10)
        pdf.setFillColor(colors.black)
        pdf.drawString(margin + 62 * mm, y, value)
        y -= 5.5 * mm

    def separator() -> None:
        nonlocal y
        y -= 1 * mm
        pdf.setStrokeColor(colors.HexColor("#C9A227"))
        pdf.setLineWidth(0.8)
        pdf.line(margin, y, page_width - margin, y)
        y -= 6 * mm

    # --- Шапка со статусом ---------------------------------------------- #
    if act.is_official:
        band_color = colors.HexColor("#1F6B3B")
        band_text = "ПРОВЕРЕНО ЧЕЛОВЕКОМ"
    else:
        band_color = colors.HexColor("#B03A3A")
        band_text = "ЧЕРНОВИК — НЕ ЯВЛЯЕТСЯ ОФИЦИАЛЬНЫМ ДОКУМЕНТОМ"

    pdf.setFillColor(band_color)
    pdf.rect(0, page_height - 14 * mm, page_width, 14 * mm, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont(font_bold, 11)
    pdf.drawCentredString(page_width / 2, page_height - 9 * mm, band_text)

    y = page_height - 26 * mm
    line("АКТ", size=17, bold=True, gap=7 * mm)
    line("о выявлении несанкционированного размещения отходов", size=11, gap=5 * mm)
    line(
        f"№ {act.candidate_id} от {act.created_at.strftime('%d.%m.%Y')}",
        size=9, gap=7 * mm, color=colors.HexColor("#555555"),
    )
    separator()

    # --- Объект ---------------------------------------------------------- #
    line("1. СВЕДЕНИЯ ОБ ОБЪЕКТЕ", size=11, bold=True, gap=7 * mm)
    field_row("Координаты (WGS84)", act.coordinates_text())
    field_row("Площадь", f"{act.area_m2:,.0f} м²".replace(",", " "))
    field_row(
        "Дата возникновения",
        format_month_year(act.appeared_date) if act.appeared_date else "не определена",
    )
    field_row("Дата выявления", act.detected_date.strftime("%d.%m.%Y") if act.detected_date else "—")
    field_row("Оценка массы отходов", f"{act.mass_t_p50:,.0f} т".replace(",", " "))
    separator()

    # --- Основания ------------------------------------------------------- #
    line("2. ОСНОВАНИЯ ВЫЯВЛЕНИЯ", size=11, bold=True, gap=7 * mm)
    line("Дистанционное зондирование: Sentinel-2, Sentinel-1, Landsat 8/9.", size=9, gap=5 * mm)
    if act.evidence_text:
        for chunk in _wrap(act.evidence_text, 95):
            line(chunk, size=9, gap=4.5 * mm)
    if act.model_probability is not None:
        field_row("Оценка модели", f"{act.model_probability:.0%}")
    if act.verification_providers:
        field_row(
            "Доверификация",
            f"подтверждено независимыми источниками: {act.verification_providers}",
        )
    separator()

    # --- Ущерб ----------------------------------------------------------- #
    line("3. ОЦЕНКА УЩЕРБА", size=11, bold=True, gap=7 * mm)
    field_row("Диапазон оценки", act.damage_text())
    field_row("Эмиссия за 20 лет", f"{act.co2e_t_p50:,.0f} т CO₂-экв.".replace(",", " "))
    if act.co2e_emitted_t_p50 > 0:
        # Строка про уже причинённый вред стоит отдельно от прогнозной.
        # Для акта это принципиально: возмещению подлежит причинённый вред,
        # а не предотвращённый, и должностное лицо должно видеть их порознь.
        share = act.co2e_emitted_t_p50 / act.co2e_t_p50 * 100 if act.co2e_t_p50 else 0
        field_row(
            "Из них уже выброшено",
            # Пробел в разрядах и запятая в дроби ставятся раздельно:
            # общий replace превратил бы «2,3 года» в «2 3 года».
            f"{act.co2e_emitted_t_p50:,.0f}".replace(",", " ")
            + f" т CO₂-экв. — {share:.0f}% за "
            + f"{act.age_years:.1f}".replace(".", ",")
            + " года с момента возникновения",
        )
    line(
        "Диапазон отражает неопределённость исходных допущений и получен методом Монте-Карло.",
        size=8, gap=6 * mm, color=colors.HexColor("#555555"),
    )
    separator()

    # --- Правовые основания ---------------------------------------------- #
    line("4. ПРИМЕНИМАЯ НОРМА", size=11, bold=True, gap=7 * mm)
    field_row("Статья", act.penalty_article)
    for chunk in _wrap(act.penalty_article_title, 95):
        line(chunk, size=9, gap=4.5 * mm)
    y -= 1 * mm
    field_row("Размер санкции", f"{act.penalty_mrp} МРП = {_kzt(act.penalty_kzt)} ₸")
    separator()

    # --- Подтверждение --------------------------------------------------- #
    line("5. СТАТУС ДОКУМЕНТА", size=11, bold=True, gap=7 * mm)
    if act.approval:
        field_row("Проверил", act.approval.reviewer_name)
        field_row("Должность", act.approval.reviewer_position)
        field_row("Дата проверки", act.approval.approved_at.strftime("%d.%m.%Y %H:%M"))
        if act.approval.note:
            for chunk in _wrap(f"Примечание: {act.approval.note}", 95):
                line(chunk, size=9, gap=4.5 * mm)
    else:
        pdf.setFillColor(colors.HexColor("#FCF1F1"))
        pdf.rect(margin, y - 14 * mm, page_width - 2 * margin, 17 * mm, stroke=0, fill=1)
        y -= 2 * mm
        for chunk in _wrap(DRAFT_WARNING, 92):
            line(chunk, size=9, gap=4.5 * mm, color=colors.HexColor("#B03A3A"), indent=3 * mm)
        y -= 6 * mm

    # --- Подвал ---------------------------------------------------------- #
    pdf.setFont(font, 7.5)
    pdf.setFillColor(colors.HexColor("#777777"))
    footer_y = 20 * mm
    for chunk in _wrap(DISCLAIMER, 118):
        pdf.drawString(margin, footer_y, chunk)
        footer_y -= 3.6 * mm
    pdf.drawString(margin, 10 * mm, "VANTAGE · Future Minds Hackathon 2026 · трек EcoFin")

    # Водяной знак черновика — поверх всего содержимого
    if not act.is_official:
        pdf.saveState()
        pdf.setFillColor(colors.Color(0.69, 0.23, 0.23, alpha=0.10))
        pdf.setFont(font_bold, 68)
        pdf.translate(page_width / 2, page_height / 2)
        pdf.rotate(38)
        pdf.drawCentredString(0, 0, "ЧЕРНОВИК")
        pdf.restoreState()

    pdf.showPage()
    pdf.save()
    log.info("Акт %s сохранён (%s): %s", act.candidate_id, act.status, path)
    return path


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #


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
    "render_pdf",
]
