"""Паспорт объекта: одна страница на объект — для выезда и для акимата.

── Зачем ───────────────────────────────────────────────────────────────

Карта хороша за столом. На выезде и на встрече в акимате нужна бумага:
одна страница, на которой видно, что это за место, как оно выглядело до и
после, сколько стоит, чем подтверждено и что проверить на месте.

Всё на листе — из выгрузки прогона и кэша снимков, ни одного числа руками:

  * снимки Esri Wayback «до» (последний релиз раньше даты разрыва) и
    «сейчас», контур объекта обведён;
  * статус проверки: опознан человеком по снимку / ждёт выезда /
    подтверждён выездом с фотографией;
  * деньги из economy.json — те же, что на экране «Экономика»;
  * физические признаки и оценки моделей;
  * что проверить на месте — список, который превращает выезд в
    подтверждение, а не в прогулку.

    python scripts/make_passports.py        → docs/passports/*.pdf
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

DATA = ROOT / "web-next/public/data"
OUT = ROOT / "docs/passports"
LIVE = "https://hakathon-lyart.vercel.app"

STATUS = {
    "ground": ("ПОДТВЕРЖДЁН ВЫЕЗДОМ", "#1f7a3a"),
    "landfill": ("ОПОЗНАН ПО СНИМКУ КАК СВАЛКА", "#5b21b6"),
    "unclear": ("ЖДЁТ ВЫЕЗДА: ПО СНИМКУ НЕ РАЗОБРАТЬ", "#b45309"),
}
SIGNALS = (
    ("ndvi_drop", "падение растительности"),
    ("bsi_rise", "рост открытого грунта"),
    ("pmli_response", "отклик полимеров (SWIR)"),
    ("sar_incoherence", "нестабильность по радару"),
    ("thermal_anomaly", "тепловая аномалия"),
)
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")


def ru(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def mln(value: float) -> str:
    return f"{ru(value / 1e6, 1)} млн ₸"


def month(value) -> str:
    text = str(value or "")[:7]
    if len(text) < 7:
        return "—"
    return f"{MONTHS[int(text[5:7]) - 1]} {text[:4]}"


def images_for(row):
    """(до, после, подпись до, подпись после) — снимки с контуром."""
    from gemini_screen import WAYBACK, fetch, outline, releases

    point = row.geometry.representative_point()
    lat, lon = point.y, point.x
    history = releases()
    now_date, now_release = history[-1]

    def get(release):
        image, zoom = fetch(lat, lon, WAYBACK.format(release=release, x="{x}", y="{y}", z="{z}"),
                            f"wb{release}")
        return None if image is None else outline(image, row.geometry, lat, lon, zoom)

    after = get(now_release)
    broke = str(row.get("break_date") or "")[:10]
    earlier = [(d, r) for d, r in history if broke and d < broke]
    before, before_date = None, None
    if earlier:
        before_date, release = earlier[-1]
        before = get(release)
    return before, after, before_date, now_date


def as_reader(array):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    # Снимок на листе — 88 мм; 560 пикселей это 160 точек на дюйм, больше
    # принтер не покажет, а файл уйдёт на сайт и в мессенджер.
    buffer = io.BytesIO()
    Image.fromarray(array).resize((560, 560)).save(buffer, format="JPEG", quality=80)
    buffer.seek(0)
    return ImageReader(buffer)


def passport(pdf, row, econ: dict | None, gemini: dict | None, fonts, page_no: int, total: int):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    font, bold = fonts
    width, height = A4
    margin = 14 * mm
    y = height - margin

    cid = str(row["candidate_id"])
    source = "ground" if row.get("check_source") == "ground" else str(row.get("visual_check"))
    label, tone = STATUS.get(source, ("СТАТУС НЕ ОПРЕДЕЛЁН", "#555555"))
    point = row.geometry.representative_point()

    pdf.setFont(bold, 18)
    pdf.setFillColor(colors.black)
    pdf.drawString(margin, y - 4 * mm, f"Паспорт объекта {cid}")
    pdf.setFont(font, 9)
    pdf.setFillColor(colors.HexColor("#666666"))
    pdf.drawRightString(width - margin, y - 3 * mm, f"Vantage AI · лист {page_no} из {total}")
    y -= 11 * mm
    pdf.setFillColor(colors.HexColor(tone))
    pdf.roundRect(margin, y - 6.5 * mm, width - 2 * margin, 8 * mm, 2 * mm, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont(bold, 10.5)
    pdf.drawString(margin + 3 * mm, y - 4.2 * mm, label)
    y -= 12 * mm

    # Снимки
    before, after, before_date, after_date = images_for(row)
    box = (width - 2 * margin - 6 * mm) / 2
    # Дата — выпуск архива Esri, а не день съёмки: снимок в выпуске бывает
    # старше самого выпуска, и называть его «снимком августа» было бы неточно.
    for i, (img, title) in enumerate(((before, f"До: архив Esri, выпуск {month(before_date)}"),
                                      (after, f"Сейчас: выпуск {month(after_date)}"))):
        x = margin + i * (box + 6 * mm)
        if img is not None:
            pdf.drawImage(as_reader(img), x, y - box, width=box, height=box)
        else:
            pdf.setFillColor(colors.HexColor("#eeeeee"))
            pdf.rect(x, y - box, box, box, stroke=0, fill=1)
            pdf.setFillColor(colors.HexColor("#777777"))
            pdf.setFont(font, 9)
            pdf.drawCentredString(x + box / 2, y - box / 2, "снимка нет")
        pdf.setFillColor(colors.black)
        pdf.setFont(font, 8.5)
        pdf.drawString(x, y - box - 4 * mm, title + " · ~0,4 м на пиксель")
    y -= box + 12 * mm

    def rows(title, items, col_x, top):
        yy = top
        pdf.setFont(bold, 10)
        pdf.setFillColor(colors.black)
        pdf.drawString(col_x, yy, title)
        yy -= 5 * mm
        for k, v in items:
            pdf.setFont(font, 8.8)
            pdf.setFillColor(colors.HexColor("#555555"))
            pdf.drawString(col_x, yy, k)
            pdf.setFillColor(colors.black)
            pdf.drawRightString(col_x + (width - 2 * margin) / 2 - 6 * mm, yy, v)
            yy -= 4.6 * mm
        return yy

    col2 = margin + (width - 2 * margin) / 2 + 3 * mm
    place = [
        ("Координаты", f"{point.y:.5f}, {point.x:.5f}"),
        ("Площадь", f"{ru(float(row.get('area_m2') or 0))} м²"),
        ("Возник", month(row.get("break_date"))),
        ("До дороги", f"{ru(float(row.get('dist_road_m') or 0))} м"),
        ("До жилья", f"{ru(float(row.get('dist_settlement_m') or 0))} м"),
        ("Контроль устранения", {"active": "объект на месте", "possibly_removed": "вероятно убран",
                                 "possibly_covered": "возможно засыпан",
                                 "insufficient_data": "данных мало"}.get(
                                     str(row.get("removal_status")), "—")),
    ]
    money = []
    if econ:
        money = [
            ("Масса отходов, оценка", f"{ru(econ['mass_t'])} т"),
            ("Вывезти как есть", mln(econ["plain_kzt"])),
            ("Вывезти с разбором", mln(econ["sorted_kzt"])),
            ("  из них вернётся сырьём", mln(econ["recyclable_kzt"])),
            ("Чистый ущерб, P10–P90", f"{ru(econ['damage_p10'] / 1e6, 1)}–"
                                      f"{ru(econ['damage_p90'] / 1e6, 1)} млн ₸"),
            ("Штраф, ст. 344 КоАП", mln(econ["penalty_kzt"]) if econ["penalty_kzt"] >= 1e6
             else f"{ru(econ['penalty_kzt'])} ₸"),
        ]
    end1 = rows("Место", place, margin, y)
    end2 = rows("Деньги (медианы)", money, col2, y)
    y = min(end1, end2) - 3 * mm

    evidence = []
    for key, name in SIGNALS:
        value = row.get(key)
        if value is not None and value == value:
            evidence.append((name, f"{float(value):.3f}"))
    score = row.get("highres_score")
    evidence.append(("Модель по снимку (AerialWaste)",
                     f"{float(score):.0%}" if score is not None and score == score else "—"))
    checks = [("Человек по снимку", {"landfill": "свалка", "unclear": "не разобрать",
                                     "not_landfill": "не свалка"}.get(
                                         str(row.get("visual_check")), "—"))]
    if gemini:
        checks.append(("Gemini по паре снимков", {"dump": "свалка", "not_dump": "не свалка",
                                                  "unclear": "не разобрать"}.get(
                                                      gemini.get("verdict"), "—")
                       + f", {float(gemini.get('confidence', 0)):.0%}"))
    checks.append(("Независимых источников снимков", str(int(row.get("verify_providers") or 0))))
    checks.append(("Выезд", "подтверждён с фото" if source == "ground" else "не было"))
    end1 = rows("Физические признаки", evidence, margin, y)
    end2 = rows("Проверки", checks, col2, y)
    y = min(end1, end2) - 2 * mm
    if gemini and gemini.get("reasoning"):
        pdf.setFont(font, 8.3)
        pdf.setFillColor(colors.HexColor("#444444"))
        for chunk in _wrap(f"Gemini: {gemini['reasoning']}", 120):
            pdf.drawString(margin, y, chunk)
            y -= 3.8 * mm
        y -= 1 * mm

    pdf.setFont(bold, 10)
    pdf.setFillColor(colors.black)
    pdf.drawString(margin, y, "Что проверить на месте")
    y -= 5 * mm
    todo = [
        "Есть ли отходы и какие: бытовые, строительные, грунт — от этого зависят масса и статья.",
        "Свежие ли следы ссыпки и колеи: возят ли сюда до сих пор.",
        "Четыре кадра с включённой геометкой: общий план, вблизи, подъезд, экран с координатами.",
        "Кадры копировать с телефона файлом (мессенджер вырезает координаты), "
        "положить в data/field/" + cid.split(":")[-1] + "/.",
    ]
    pdf.setFont(font, 8.8)
    for item in todo:
        for i, chunk in enumerate(_wrap(item, 112)):
            pdf.drawString(margin + (0 if i == 0 else 4 * mm), y, ("☐ " if i == 0 else "") + chunk)
            y -= 4.4 * mm

    pdf.setFont(font, 7.5)
    pdf.setFillColor(colors.HexColor("#777777"))
    pdf.drawString(margin, 12 * mm,
                   f"Карта: {LIVE}/map.html?object={cid}   ·   "
                   f"Google Maps: https://www.google.com/maps?q={point.y:.5f},{point.x:.5f}")
    pdf.drawString(margin, 8 * mm,
                   "Оценка по спутниковым данным, не юридическое доказательство. Статус объекта "
                   "устанавливает уполномоченное лицо по итогам выездной проверки.")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


def main() -> int:
    import geopandas as gpd
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from vantage.act import register_cyrillic_font

    site = gpd.read_file(DATA / "candidates.geojson").to_crs(4326)
    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    econ = {o["id"]: o for o in economy["objects"]}
    order = [p["id"] for p in economy["priority"]]
    site["_order"] = site["candidate_id"].astype(str).map(
        lambda c: order.index(c) if c in order else 999)
    site = site.sort_values("_order")

    gemini_path = ROOT / "data/gemini/site_pair.json"
    gemini = json.loads(gemini_path.read_text(encoding="utf-8")) if gemini_path.exists() else {}

    fonts = register_cyrillic_font()
    OUT.mkdir(parents=True, exist_ok=True)
    combined = canvas.Canvas(str(OUT / "все_объекты.pdf"), pagesize=A4)
    combined.setTitle("Паспорта объектов Vantage AI")
    total = len(site)
    for n, (_, row) in enumerate(site.iterrows(), start=1):
        cid = str(row["candidate_id"])
        answer = gemini.get(f"site:{cid}")
        single = canvas.Canvas(str(OUT / f"{cid}.pdf"), pagesize=A4)
        single.setTitle(f"Паспорт объекта {cid}")
        passport(single, row, econ.get(cid), answer, fonts, 1, 1)
        single.showPage()
        single.save()
        passport(combined, row, econ.get(cid), answer, fonts, n, total)
        combined.showPage()
        print(f"   {cid}: {row.get('visual_check')}")
    combined.save()
    # Общий файл — ещё и на сайт, рядом с выгрузкой реестра: ссылку на него
    # отправляют в акимат, а не папку с пятнадцатью файлами.
    public = DATA / "export" / "passports.pdf"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_bytes((OUT / "все_объекты.pdf").read_bytes())
    print(f"── Паспорта: {total} объектов → {OUT.relative_to(ROOT)} и {public.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
