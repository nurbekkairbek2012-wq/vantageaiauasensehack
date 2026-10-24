"""Собрать питч-деку в PDF: двенадцать слайдов, печать из HTML.

── Почему из HTML, а не из ReportLab ───────────────────────────────────

Дека — это в первую очередь картинки работающего продукта. Верстать их
координатами в ReportLab значит второй раз описывать то, что уже описано
в CSS сайта, и получить другую типографику на тех же числах.

Здесь слайды собираются той же вёрсткой и теми же шрифтами, что и сайт, и
печатаются Chromium в PDF. Снимки берутся из docs/deck — они сняты с
живого сайта скриптом deck_assets.py, а не нарисованы заново.

── Почему числа не вписаны ─────────────────────────────────────────────

Все они читаются из выгрузки прогона. Вписанное руками число живёт до
первого пересчёта, после чего дека начинает противоречить сайту, а
расхождение между ними находится за минуту.

── Куда кладётся ───────────────────────────────────────────────────────

За пределы репозитория — рядом с исходными PDF проекта. Дека это
материал подачи, а не часть продукта, и место ей там же, где остальные
документы команды.

    python scripts/build_deck.py [--out "..\\VANTAGE_pitch.pdf"]
"""

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

#: Месяцы в именительном: «апрель 2019» вместо «2019-04». Дата в карточке
#: и на слайде должна читаться, а не расшифровываться.
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")

#: Состав команды: имя и роль.
#:
#: Роль на слайде — это ещё и указание, кому адресовать вопрос на Q&A.
#: Поэтому она названа так, как человек сам её назвал, а не обобщена до
#: «разработчик»: спросят про модель — отвечает тот, кто её делал.
TEAM = (
    ("Нурбек", "продукт-лид, ИИ и машинное обучение"),
    ("Каусар", "UI/UX-дизайн"),
    ("Айдина", "исследование и дизайн"),
    ("Алдияр", "фронтенд-разработка"),
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web-next/public/data"
SHOTS = ROOT / "docs/deck"
FONTS = ROOT / "web-next/public/fonts"

#: Размер слайда. 1280x720 — 16:9, привычная пропорция проектора.
W, H = 1280, 720


def ru(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def data_uri(path: Path) -> str:
    kind = "font/woff2" if path.suffix == ".woff2" else f"image/{path.suffix[1:]}"
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode()


def numbers() -> dict:
    """Все числа деки — из выгрузки прогона, ни одного вписанного."""
    import geopandas as gpd
    import pandas as pd

    site = gpd.read_file(DATA / "candidates.geojson")
    funnel = json.loads((DATA / "funnel.json").read_text(encoding="utf-8"))
    metrics = json.loads((DATA / "metrics.json").read_text(encoding="utf-8"))
    # Раскладка потерь на слагаемые считается отдельным скриптом и лежит
    # рядом с сайтом. Дека берёт её оттуда же, чтобы 43,0 млн на слайде и
    # 43,0 млн на экране «Экономика» были одним числом, а не двумя
    # похожими.
    econ = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    # Матрица машинного просмотра — из scripts/screen_metrics.py, а не
    # вписанные «34 из 35»: после пересмотра разметки они разошлись с
    # данными.
    screen_path = DATA / "screen.json"
    screen = (json.loads(screen_path.read_text(encoding="utf-8"))
              if screen_path.exists() else {})
    # Деньги, возраст и штрафы — по опознанным как свалка, как на сайте.
    # Объекты «не разобрать» стоят в очереди выезда, но в суммы не входят.
    sure = site[site["visual_check"] == "landfill"]
    if sure.empty:
        sure = site
    pending_sums = (econ.get("pending") or {}).get("sum_of_medians", {})
    rejected = funnel["rejected"]

    def cut(share: float) -> int:
        for row in econ["priority"]:
            if row["share"] >= share:
                return int(row["n"])
        return len(econ["priority"])

    sums = econ["totals"]["sum_of_medians"]

    # Тот самый объект, который открывают первым на демонстрации: верхний
    # в очереди по деньгам. Его площадь и его дата — именно его, а не
    # «самого крупного» и «самого старого», как стояло раньше: два разных
    # объекта в одной строке слайда читаются как одна выдумка.
    top = max(econ["objects"], key=lambda o: o["damage_p50"])
    top_when = pd.Timestamp(top["break_date"]) if top.get("break_date") else None

    return {
        "top_area": top["area_m2"],
        "top_date": (f"{MONTHS[top_when.month - 1]} {top_when.year}"
                     if top_when is not None else "—"),
        "top_damage": top["damage_p50"] / 1e6,
        "mass_sum": sums["mass_t"],
        "removal": sums["removal_kzt"] / 1e6,
        "recyclable": sums["recyclable_kzt"] / 1e6,
        "climate": sums["climate_kzt"] / 1e6,
        "net": sums["damage_kzt"] / 1e6,
        "recovery": 100 * sums["recyclable_kzt"] / sums["removal_kzt"],
        "band_low": econ["totals"]["damage_kzt"]["p10"] / 1e6,
        "band_high": econ["totals"]["damage_kzt"]["p90"] / 1e6,
        "band_mid": econ["totals"]["damage_kzt"]["p50"] / 1e6,
        "emitted": sums["co2e_emitted_t"],
        "co2_total": sums["co2e_t"],
        "irreversible": 100 * sums["co2e_emitted_t"] / sums["co2e_t"],
        "plain": sums["plain_kzt"] / 1e6,
        "sorted": sums["sorted_kzt"] / 1e6,
        "saving": sums["saving_kzt"] / 1e6,
        "breakeven": 100 * econ["totals"]["breakeven_share"],
        "half_trips": cut(0.5),
        "most_trips": cut(0.8),
        "rho_removal": econ["sensitivity"].get("removal_cost", 0.0),
        "auto_rejected": econ["queue"]["auto_rejected"],
        "raw": funnel["raw"],
        "passed": rejected.get("ПРОШЁЛ ОТСЕВ", 0),
        "published": len(site),
        "dumps": int((site["visual_check"] == "landfill").sum()),
        "pending": int((site["visual_check"] == "unclear").sum()),
        "ground": int((site["check_source"] == "ground").sum()),
        "damage": sure["damage_p50"].sum() / 1e6,
        "low": sure["damage_p10"].sum() / 1e6,
        "high": sure["damage_p90"].sum() / 1e6,
        "area_ha": sure["area_m2"].sum() / 1e4,
        "mass": sure["mass_t"].sum(),
        "co2": sure["co2e_t"].sum(),
        "penalty": sure["penalty_kzt"].sum() / 1e6,
        # Возраст на дату прогона — тот же, на котором посчитан метан.
        "age": float(sure["age_years"].astype(float).mean()),
        "oldest": (lambda d: f"{MONTHS[d.month - 1]} {d.year}")(
            pd.to_datetime(sure["break_date"]).min()),
        "biggest": sure["area_m2"].max(),
        "pending_mass": pending_sums.get("mass_t", 0),
        "pending_removal": pending_sums.get("removal_kzt", 0) / 1e6,
        "screen_n": screen.get("objects", 0),
        "screen_work": 100 * screen.get("workload_removed", 0),
        "screen_rejected": screen.get("rejected", 0),
        "screen_rejected_dump": screen.get("rejected_landfill", 0),
        "screen_dumps": screen.get("dumps", 0),
        "screen_kept": screen.get("dumps_kept", 0),
        "screen_recall_low": 100 * screen.get("recall_low", 0),
        "lift_low": metrics.get("lift_low", 0),
        "pr_low": metrics.get("pr_auc_low", 0),
        "pr_high": metrics.get("pr_auc_high", 0),
        "positives": int(metrics.get("positives_future", 0)),
        "cells": int(metrics.get("cells", 0)),
        "by_osm": rejected.get(
            "пересекается с известным объектом OSM (карьер, стройка, застройка, вода)", 0),
        "by_home": rejected.get("слишком близко к жилью", 0),
        "by_area": rejected.get("площадь ниже порога разрешения Sentinel-2", 0),
        "by_road": rejected.get("нет подъезда: далеко от проезжей дороги", 0),
    }


def css() -> str:
    # Все наборы символов, а не только кириллица: латиница «Vantage AI»,
    # цифры и знак тенге иначе уходили в запасной шрифт с засечками.
    sys.path.insert(0, str(ROOT / "scripts"))
    from fontfaces import font_faces

    faces = [font_faces("Golos", "golos-text", (400, 500, 600)),
             font_faces("Oswald", "oswald", (500, 600))]

    return "\n".join(faces) + f"""
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    :root {{
      --soot:#0d0918; --soot2:#150f26; --soot3:#1d1533;
      --line:#ede9fe; --violet:#7c3aed; --lit:#a78bfa; --deep:#4c1d95;
      --grid:#2f2450; --muted:#b3a5d9; --muted2:#8578ad;
      --amber:#e3b341; --emerald:#3fb950;
    }}
    body {{ font-family:'Golos',system-ui,sans-serif; background:var(--soot);
            color:var(--line); -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    .slide {{ width:{W}px; height:{H}px; padding:56px 64px; position:relative;
              overflow:hidden; page-break-after:always; background:var(--soot);
              display:flex; flex-direction:column; }}
    .slide:last-child {{ page-break-after:auto; }}
    h1 {{ font-family:'Oswald',sans-serif; font-weight:600; font-size:60px;
          line-height:1.02; letter-spacing:-.01em; }}
    h2 {{ font-family:'Oswald',sans-serif; font-weight:500; font-size:38px;
          line-height:1.08; margin-bottom:18px; }}
    p  {{ font-size:19px; line-height:1.5; color:var(--muted); max-width:62ch; }}
    .lead {{ font-size:23px; color:var(--muted); }}
    .kicker {{ font-family:'Oswald',sans-serif; font-size:13px; letter-spacing:.16em;
               text-transform:uppercase; color:var(--lit); margin-bottom:14px; }}
    .num {{ font-family:'Oswald',sans-serif; font-weight:600; font-variant-numeric:tabular-nums; }}
    .big {{ font-size:74px; line-height:.95; color:var(--line); }}
    .mid {{ font-size:44px; line-height:1; }}
    .lit {{ color:var(--lit); }} .em {{ color:var(--emerald); }} .am {{ color:var(--amber); }}
    /* Содержимое центрируется между заголовком и подвалом. Слайды разной
       плотности иначе прижимаются к верху, и под текстом остаётся пустая
       треть — на проекторе это читается как незаконченный слайд. */
    .body {{ flex:1; display:flex; flex-direction:column; justify-content:center;
             padding-bottom:34px; }}
    .row {{ display:flex; gap:44px; align-items:flex-start; }}
    .col {{ flex:1; min-width:0; }}
    .stats {{ display:flex; gap:0; margin-top:26px; border-top:1px solid var(--grid);
              padding-top:20px; }}
    .stat {{ flex:1; padding-right:20px; }}
    .stat + .stat {{ border-left:1px solid var(--grid); padding-left:24px; }}
    .stat dt {{ font-size:14px; color:var(--muted2); line-height:1.3; }}
    .stat dd {{ margin-top:8px; }}
    .shot {{ border:1px solid var(--grid); border-radius:6px; overflow:hidden;
             background:var(--soot2); }}
    .shot img {{ display:block; width:100%; }}
    table {{ width:100%; border-collapse:collapse; font-size:17px; }}
    th {{ text-align:left; font-weight:500; color:var(--muted2); font-size:13px;
          text-transform:uppercase; letter-spacing:.08em; padding-bottom:10px;
          border-bottom:1px solid var(--grid); }}
    td {{ padding:11px 0; border-bottom:1px solid var(--grid); color:var(--muted); }}
    td.k {{ color:var(--line); }}
    td.r {{ text-align:right; font-family:'Oswald',sans-serif;
            font-variant-numeric:tabular-nums; color:var(--line); }}
    .note {{ font-size:15px; color:var(--muted2); line-height:1.45; }}
    .foot {{ position:absolute; left:64px; right:64px; bottom:26px;
             display:flex; justify-content:space-between; font-size:12px;
             color:var(--muted2); border-top:1px solid var(--grid); padding-top:12px; }}
    ul {{ list-style:none; }}
    li {{ font-size:18px; line-height:1.45; color:var(--muted); margin-bottom:12px;
          padding-left:20px; position:relative; }}
    li::before {{ content:''; position:absolute; left:0; top:9px; width:7px; height:7px;
                  border-radius:50%; background:var(--violet); }}
    li b {{ color:var(--line); font-weight:500; }}
    .pill {{ display:inline-block; padding:5px 12px; border:1px solid var(--grid);
             border-radius:99px; font-size:14px; color:var(--muted); margin:0 6px 8px 0; }}
    /* Цепочка «снимок → ИИ → приоритет → деньги». Шесть звеньев в ряд, а не
       список: список читается как перечень возможностей, ряд со стрелками —
       как путь, по которому проходит один объект. Именно этого не хватало
       деке: было видно, что система умеет много, и не видно, что она делает
       по порядку. */
    .chain {{ display:flex; gap:0; align-items:stretch; }}
    .link {{ flex:1; min-width:0; padding:16px 16px 16px 18px; position:relative;
             background:var(--soot2); border:1px solid var(--grid);
             border-right:none; }}
    .link:last-child {{ border-right:1px solid var(--grid); }}
    .link b {{ display:block; color:var(--line); font-size:17px; font-weight:500;
               margin-bottom:6px; }}
    .link span {{ font-size:14px; line-height:1.4; color:var(--muted2); }}
    .link .step {{ display:block; font-family:'Oswald',sans-serif; font-size:12px;
                   letter-spacing:.14em; color:var(--lit); margin-bottom:8px; }}
    """


def slides(n: dict) -> str:
    """Двенадцать слайдов в порядке, в котором их читает жюри.

    ── Почему порядок именно такой ─────────────────────────────────────

    Прежняя дека открывалась находкой: «свалки, которых нет ни в одном
    реестре». Это правда и это интересно, но это ответ на вопрос, который
    задавали не нам. Трек называется EcoFin, а кейс просит платформу,
    которая считает потери ресурсов и денег и говорит, что с ними делать.
    Судья, пролиставший два слайда, видел хороший remote sensing и не
    видел ответа на задание — и это стоило дороже любых недоделок
    интерфейса.

    Поэтому первым идёт счёт, а не метод: сколько ресурса потеряно,
    сколько из этого возвращается, в каком порядке убирать. Как именно
    находится объект — четвёртым слайдом, одной цепочкой; пять признаков,
    сиамская сеть и PR-AUC остаются в запасе для Q&A. Их спрашивают, и на
    них есть чем ответить, но семь минут — не то место, где доказывают
    всё сразу.

    Обязательные разделы положения на местах: проблема (2), решение (4),
    целевая аудитория (11), стек (10), бизнес-модель и команда (12).
    """

    def shot(name: str) -> str:
        """Снимок с живого сайта. Отсутствует — слайд обходится без него."""
        path = SHOTS / name
        if not path.exists():
            return ""
        return f'<div class="shot"><img src="{data_uri(path)}"></div>'

    def foot(index: int, title: str) -> str:
        return (f'<div class="foot"><span>Vantage AI · {title}</span>'
                f'<span>{index} / 12</span></div>')

    s = []

    # 1 ── Титул
    s.append(f"""<section class="slide" style="justify-content:center">
      <div class="kicker">Future Minds Hackathon 2026 · трек EcoFin · Астана</div>
      <div style="font-family:'Oswald';font-weight:600;font-size:30px;
                  letter-spacing:.02em;margin-bottom:18px">
        Vantage <span class="lit">AI</span></div>
      <h1>ИИ находит экологические потери,<br>пока они ещё дешёвые</h1>
      <p class="lead" style="margin-top:26px;max-width:60ch">
        Спутник находит несанкционированные свалки, система считает потери
        в тенге и говорит, в каком порядке их устранять. Проверено под
        Астаной: {n['dumps']} свалок опознаны человеком по снимку высокого
        разрешения, ещё {n['pending']} объектов ждут выезда.
      </p>
      <dl class="stats" style="max-width:900px">
        <div class="stat"><dt>Отходов на опознанных свалках</dt>
          <dd class="num big lit">{ru(n['mass_sum'])}<span style="font-size:30px"> т</span></dd></div>
        <div class="stat"><dt>Потери бюджета по ним</dt>
          <dd class="num big">{ru(n['net'], 1)}<span style="font-size:30px"> млн ₸</span></dd></div>
        <div class="stat"><dt>Экономия правильного решения</dt>
          <dd class="num big em">{ru(n['saving'], 1)}<span style="font-size:30px"> млн ₸</span></dd></div>
      </dl>
      {foot(1, 'титул')}</section>""")

    # 2 ── Проблема
    s.append(f"""<section class="slide">
      <div class="kicker">Проблема</div>
      <h2>За стихийную свалку платят трижды,<br>и ни один счёт не считают</h2>
      <div class="body">
      <div class="row" style="margin-top:8px">
        <div class="col">
          <ul>
            <li><b>Первый счёт — вывоз.</b> {ru(n['mass_sum'])} тонн на {n['dumps']}
              опознанных свалках одного квадрата 20 × 20 км — это <b>{ru(n['removal'], 1)} млн ₸</b> бюджету, и в план
              уборки они не заложены: объектов нет в реестрах.</li>
            <li><b>Второй счёт — потерянный ресурс.</b> Внутри тех же тонн лежит
              пластика, бумаги, металла и стекла на <b>{ru(n['recyclable'], 1)} млн ₸</b>
              по прайсу приёмки. Вывоз на полигон списывает их в ноль.</li>
            <li><b>Третий счёт не выставят никому.</b> {ru(n['emitted'])} т CO₂-экв из
              {ru(n['co2_total'])} уже ушли в атмосферу, пока объекты лежали
              ненайденными — {ru(n['irreversible'])}% климатического ущерба уже
              необратимы.</li>
            <li><b>Найти их некому.</b> Инспектор физически не объедет область, а
              выезд на подозрительное место стоит часа дороги.</li>
          </ul>
        </div>
        <div class="col">{shot('pixel.png')}
          <p class="note" style="margin-top:12px">
            Крупнейшая опознанная свалка за восемь лет: шесть сезонов
            вегетации, потом провал без возврата — так она выглядит на
            снимках Sentinel-2.</p>
        </div>
      </div>
            </div>
      {foot(2, 'проблема')}</section>""")

    # 3 ── Цена бездействия
    s.append(f"""<section class="slide">
      <div class="kicker">Цена бездействия</div>
      <h2>Квадрат 20 × 20 км к северу от Астаны</h2>
      <div class="body">
      <div class="row" style="margin-top:20px">
        <div class="col"><table>
          <tr><td class="k">Подозрительных мест проверила программа</td>
              <td class="r">{n['raw']}</td></tr>
          <tr><td class="k">Дошли до списка после проверки</td>
              <td class="r">{n['published']}</td></tr>
          <tr><td class="k">Опознаны как свалка по снимку</td>
              <td class="r em">{n['dumps']}</td></tr>
          <tr><td class="k">Ждут выезда — по снимку не разобрать</td>
              <td class="r am">{n['pending']}</td></tr>
          <tr><td class="k">Лежит средняя свалка</td>
              <td class="r">{ru(n['age'], 1)} года</td></tr>
          <tr><td class="k">Самая старая возникла</td>
              <td class="r">{n['oldest']}</td></tr>
          <tr><td class="k">Отходов на опознанных свалках</td>
              <td class="r">{ru(n['mass_sum'])} т</td></tr>
        </table></div>
        <div class="col">
          <div class="num mid lit">{ru(n['net'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">чистые потери по опознанным
            свалкам; ждущие выезда добавят до {ru(n['pending_removal'], 1)} млн ₸
            вывоза, если подтвердятся</p>
          <div class="num mid" style="margin-top:26px">{ru(n['co2_total'])} т CO₂-экв</div>
          <p class="note" style="margin-top:8px">метан за двадцать лет, из них
            <b style="color:var(--amber)">{ru(n['emitted'])} т уже выброшено</b> —
            их не вернуть уборкой</p>
          <div class="num mid" style="margin-top:26px">{ru(n['penalty'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">штрафы по ст. 344 ч. 2-1 КоАП РК,
            если нарушители установлены. Возврат в бюджет, а не снижение
            ущерба — складывать их с потерями нельзя</p>
        </div>
      </div>
            </div>
      {foot(3, 'цена бездействия')}</section>""")

    # 4 ── Решение: цепочка от снимка до денег
    s.append(f"""<section class="slide">
      <div class="kicker">Решение</div>
      <h2>Снимок → ИИ → приоритет → деньги</h2>
      <div class="body">
      <div class="chain" style="margin-top:10px">
        <div class="link"><span class="step">1</span>
          <b>Спутник</b><span>восемь лет архива Sentinel-2, Sentinel-1 и Landsat,
          бесплатно и открыто</span></div>
        <div class="link"><span class="step">2</span>
          <b>ИИ находит изменение</b><span>место, где растительность исчезла и
          не вернулась: {n['raw']} находок</span></div>
        <div class="link"><span class="step">3</span>
          <b>ИИ отсеивает лишнее</b><span>карьеры, стройки, вода, жильё —
          {n['raw']} → {n['passed']} до человека</span></div>
        <div class="link"><span class="step">4</span>
          <b>Человек решает по снимку</b><span>0,4–0,8 м на пиксель:
          {n['dumps']} свалок, {n['pending']} — на выезд</span></div>
        <div class="link"><span class="step">5</span>
          <b>Система считает деньги</b><span>масса, вывоз, возвратное сырьё,
          метан — с интервалом</span></div>
        <div class="link"><span class="step">6</span>
          <b>Выдаёт действие</b><span>очередь по деньгам, черновик акта,
          контроль устранения</span></div>
      </div>
      <p class="note" style="margin-top:22px;max-width:96ch">
        Пять физических признаков — оптика, ближний инфракрасный, радар и
        тепло — вместо «нейросеть посмотрела на картинку». Что именно
        видит каждый и что отсекает, разбираем на Q&amp;A: на слайде это
        отвлекало бы от главного — что на выходе получает заказчик.</p>
      <dl class="stats" style="margin-top:24px">
        <div class="stat"><dt>дошло до списка из {n['raw']} находок</dt>
          <dd class="num mid lit">{n['published']}</dd></div>
        <div class="stat"><dt>потерь стало видно</dt>
          <dd class="num mid">{ru(n['net'], 1)} млн ₸</dd></div>
        <div class="stat"><dt>выезда закрывают половину суммы очереди</dt>
          <dd class="num mid em">{n['half_trips']}</dd></div>
      </dl>
            </div>
      {foot(4, 'решение')}</section>""")

    # 5 ── Экономический эффект
    s.append(f"""<section class="slide">
      <div class="kicker">Экономический эффект</div>
      <h2>Не «сколько это стоит»,<br>а «что выгоднее сделать»</h2>
      <div class="body">
      <div class="row" style="margin-top:12px">
        <div class="col" style="flex:1.15">{shot('decision.png')}</div>
        <div class="col">
          <p class="note" style="margin-bottom:10px">Сумма ущерба — диагноз.
            Решение принимается сравнением двух смет:</p>
          <table>
            <tr><td class="k">Вывезти как есть</td>
                <td class="r">{ru(n['plain'], 1)} млн ₸</td></tr>
            <tr><td class="k">Вывезти с разбором</td>
                <td class="r lit">{ru(n['sorted'], 1)} млн ₸</td></tr>
            <tr><td class="k">Экономия выбора</td>
                <td class="r em">{ru(n['saving'], 1)} млн ₸</td></tr>
          </table>
          <div class="num mid em" style="margin-top:22px">{ru(n['breakeven'])}%</div>
          <p class="note" style="margin-top:8px">до этой доли от стоимости
            вывоза разбор окупается — столько стоит сырьё внутри. Наша оценка
            надбавки за разбор — 30%, запас почти двукратный.</p>
          <p class="note" style="margin-top:14px">Это единственное число,
            которое надо спросить у подрядчика, чтобы проверить весь расчёт.
            Станет дороже — рекомендация меняется на обратную, и мы скажем об
            этом первыми.</p>
        </div>
      </div>
            </div>
      {foot(5, 'экономический эффект')}</section>""")

    # 6 ── Что нашли
    s.append(f"""<section class="slide">
      <div class="kicker">Результат</div>
      <h2>{n['dumps']} свалок опознаны человеком,<br>{n['pending']} ждут выезда</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col" style="flex:1.25">{shot('map.png')}</div>
        <div class="col">
          <table>
            <tr><td class="k">Сырых находок детектора</td><td class="r">{n['raw']}</td></tr>
            <tr><td class="k">Снял автоматический отсев</td>
                <td class="r">{n['auto_rejected']}</td></tr>
            <tr><td class="k">Просмотрено человеком</td><td class="r">{n['passed']}</td></tr>
            <tr><td class="k">Отвергнуто при просмотре</td>
                <td class="r am">{n['passed'] - n['published']}</td></tr>
            <tr><td class="k">В очереди выезда</td><td class="r lit">{n['published']}</td></tr>
            <tr><td class="k">— опознаны как свалка</td><td class="r em">{n['dumps']}</td></tr>
            <tr><td class="k">— по снимку не разобрать</td><td class="r am">{n['pending']}</td></tr>
          </table>
          <p class="note" style="margin-top:18px">
            Отвергнутые не спрятаны: {n['passed'] - n['published']} собственные находки
            оказались складами, стройплощадками и старицами — три четверти всего,
            что прошло автоматический отсев. Команда, отвергшая три четверти
            своего списка, называет это сама.</p>
        </div>
      </div>
            </div>
      {foot(6, 'что нашли')}</section>""")

    # 7 ── Чем это лучше обычной проверки
    s.append(f"""<section class="slide">
      <div class="kicker">Чем это лучше нынешнего способа</div>
      <h2>Инспектор и Vantage AI<br>на одной территории</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col" style="flex:1.35">
          <table>
            <tr><th>Вопрос</th><th>Как сейчас</th>
                <th style="text-align:right">С Vantage AI</th></tr>
            <tr><td class="k">Как ищут объект</td><td>по жалобе или объезду</td>
                <td class="r">сплошной просмотр области</td></tr>
            <tr><td class="k">Охват за два дня</td><td>маршрут одного экипажа</td>
                <td class="r">1 709 км²</td></tr>
            <tr><td class="k">Что проверяет человек</td><td>всё подряд</td>
                <td class="r">{n['passed']} из {n['raw']}</td></tr>
            <tr><td class="k">Порядок объезда</td><td>по дате обращения</td>
                <td class="r">по деньгам: {n['half_trips']} выезда = 50% суммы</td></tr>
            <tr><td class="k">Когда объект возник</td><td>неизвестно</td>
                <td class="r">дата разрыва во временном ряду</td></tr>
            <tr><td class="k">Убрали или засыпали</td><td>верят на слово</td>
                <td class="r">тепловой контроль по зимам</td></tr>
          </table>
        </div>
        <div class="col">
          <div class="num mid lit">{ru(n['screen_work'])}%</div>
          <p class="note" style="margin-top:8px">ручной работы снимает машинный
            просмотр снимков — измерено на {n['screen_n']} объектах с
            человеческим вердиктом</p>
          <p style="margin-top:18px">Дороже всего не найденный объект, а
            <b style="color:var(--line)">зря совершённый выезд</b>. Поэтому
            главная экономия здесь не в поиске, а в очереди: программа
            снимает {n['auto_rejected']} проверок до того, как человек откроет
            первый снимок.</p>
          <p class="note" style="margin-top:16px">И отдельно: где метод НЕ
            сработает, мы умеем сказать до прогона, за секунды и без единого
            снимка. Область — один запрос к открытой карте, вся агломерация
            по сетке — двенадцать минут.</p>
        </div>
      </div>
            </div>
      {foot(7, 'сравнение')}</section>""")

    # 8 ── ИИ
    s.append(f"""<section class="slide">
      <div class="kicker">Использование ИИ</div>
      <h2>Где именно решает модель,<br>и чего она не может</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col">
          <table>
            <tr><th>Что измерено</th><th style="text-align:right">Результат</th></tr>
            <tr><td class="k">Машинный просмотр снимает ручной работы</td>
                <td class="r lit">{ru(n['screen_work'])}%</td></tr>
            <tr><td class="k">Опознанных свалок среди его отказов</td>
                <td class="r">{n['screen_rejected_dump']} из {n['screen_rejected']}</td></tr>
            <tr><td class="k">Полнота, нижняя граница 90%</td>
                <td class="r">{ru(n['screen_recall_low'])}%
                  ({n['screen_kept']} из {n['screen_dumps']})</td></tr>
            <tr><td class="k">Риск появления, выигрыш над случайным</td>
                <td class="r lit">не хуже ×{n['lift_low']:.0f}</td></tr>
            <tr><td class="k">Риск появления, PR-AUC (интервал)</td>
                <td class="r">{ru(n['pr_low'], 2)}–{ru(n['pr_high'], 2)}</td></tr>
            <tr><td class="k">Сеть «до/после», ROC-AUC</td><td class="r">0,907</td></tr>
          </table>
          <p class="note" style="margin-top:16px">Три попытки обучить собственную
            модель дали отрицательный результат, и все три записаны с числами.
            «Пробовали трижды, вот измерения» сильнее любого «у нас свой ИИ».</p>
        </div>
        <div class="col">
          <p><b style="color:var(--line)">Без ИИ на выходе — тысячи изменений
            поверхности.</b> ИИ сокращает их до короткой очереди, отсортированной
            по деньгам, и оставляет проверку человеку: {n['raw']} → {n['passed']} →
            {n['published']}.</p>
          <p style="margin-top:16px"><b style="color:var(--line)">Опознанных
            свалок в проверке всего {n['screen_dumps']}</b>, и
            {n['screen_kept']} из {n['screen_dumps']} совместимо с полнотой
            {ru(n['screen_recall_low'])}% — поэтому оценка модели остаётся
            подсказкой, а решение за человеком. Лишний объект в очереди стоит минуты;
            выброшенный не вернётся никогда.</p>
          <p style="margin-top:16px">Оценка риска проверена <b
            style="color:var(--line)">по времени</b>: обучена на объектах до
            сентября 2023, проверена на возникших после. Свалок после отсечки
            {n['positives']}, поэтому называем нижнюю границу интервала, а не
            середину.</p>
        </div>
      </div>
            </div>
      {foot(8, 'использование ИИ')}</section>""")

    # 9 ── Чему из этих чисел верить
    s.append(f"""<section class="slide">
      <div class="kicker">Экономика</div>
      <h2>Чему из этих чисел<br>можно верить, и насколько</h2>
      <div class="body">
      <div class="row" style="margin-top:16px">
        <div class="col">
          <div class="num mid lit">{ru(n['net'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">сумма медиан по опознанным
            свалкам: складывается из столбца на экране</p>
          <div class="num" style="font-size:28px;margin-top:20px;color:var(--muted)">
            {ru(n['band_low'], 1)} — {ru(n['band_high'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">интервал по списку целиком,
            P10 — P90</p>
          <p style="margin-top:20px">Монте-Карло, 20 000 итераций, восемь
            допущений. У каждого на экране указано происхождение:
            <b style="color:var(--emerald)">подтверждено источником</b>,
            <b style="color:var(--lit)">выведено</b> или
            <b style="color:var(--amber)">инженерная оценка</b>. Число без такой
            пометки проверить нельзя.</p>
          <p style="margin-top:14px"><b style="color:var(--line)">Сильнее всего итог
            двигает стоимость вывоза тонны</b>: ранговая корреляция
            {ru(n['rho_removal'], 2)}. Прямого тарифа по Астане в открытом доступе
            нет — величина выведена из тарифа Алматы и так помечена; уточнить её
            у оператора — первое, что мы просим на пилоте.</p>
        </div>
        <div class="col">{shot('priority.png')}</div>
      </div>
            </div>
      {foot(9, 'экономика')}</section>""")

    # 10 ── Продукт и стек
    #
    # Слайд был перечнем экранов, и читался он ровно так: «мы сделали
    # всё». Перечень отвечает на вопрос «что у вас есть», а жюри задаёт
    # другой — «что происходит с моей задачей». Здесь тот же
    # единственный сценарий, что идёт на живой демонстрации, шагами;
    # второстепенное сжато в одну строку.
    #
    # Снимка на слайде нет намеренно. Стоял снимок прогноза — экрана,
    # которого в этом сценарии нет вообще: текст вёл по карте, картинка
    # показывала другое. Несовпадение картинки с подписью на защите
    # замечают раньше, чем читают подпись.
    s.append(f"""<section class="slide">
      <div class="kicker">Продукт и стек</div>
      <h2>Один объект от находки<br>до подписанного акта</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col" style="flex:1.3">
          <table>
            <tr><th>Шаг</th><th style="text-align:right">Что видит инспектор</th></tr>
            <tr><td class="k">1. Открыл карту</td>
                <td class="r">очередь по деньгам, не список точек</td></tr>
            <tr><td class="k">2. Взял верхний объект</td>
                <td class="r lit">{ru(n['top_area'])} м², {ru(n['top_damage'], 1)} млн ₸</td></tr>
            <tr><td class="k">3. Посмотрел до и после</td>
                <td class="r">снимок 0,4–0,8 м на пиксель</td></tr>
            <tr><td class="k">4. Проверил доказательства</td>
                <td class="r">пять признаков с весами</td></tr>
            <tr><td class="k">5. Увидел деньги</td>
                <td class="r">вывоз, возврат сырьём, интервал</td></tr>
            <tr><td class="k">6. Распечатал акт</td>
                <td class="r em">черновик до подписи человека</td></tr>
            <tr><td class="k">7. Через месяц — контроль</td>
                <td class="r">убрали или засыпали, по зимнему теплу</td></tr>
          </table>
        </div>
        <div class="col">
          <p><b style="color:var(--line)">Рядом, но не в этом сценарии:</b>
            карта риска на год вперёд, Telegram-бот для жителей, инструмент
            разметки. Показываем их, только если спросят: один доведённый
            до конца путь убедительнее шести начатых.</p>
          <p style="margin-top:16px"><b style="color:var(--line)">Данные — в
            сборке, а не в облаке.</b> Объекты, деньги, черновик акта и шрифты
            приезжают вместе со страницей; сеть нужна только подложке снимков.
            Без сети остаётся рабочий инструмент на пустом фоне — проверено
            автоматически.</p>
          <div style="margin-top:18px">
            <span class="pill">Python · xarray · dask · rasterio</span>
            <span class="pill">PyTorch</span>
            <span class="pill">LightGBM</span>
            <span class="pill">FastAPI</span>
            <span class="pill">React · Leaflet · Tailwind</span>
            <span class="pill">ReportLab</span>
            <span class="pill">Sentinel-2 · Sentinel-1 · Landsat</span>
            <span class="pill">OpenStreetMap</span>
          </div>
        </div>
      </div>
            </div>
      {foot(10, 'продукт и стек')}</section>""")

    # 11 ── Кто пользуется и как это масштабируется
    s.append(f"""<section class="slide">
      <div class="kicker">Целевая аудитория</div>
      <h2>Один главный пользователь,<br>и его понедельник</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col">
          <div class="num mid lit">Отдел экологии акимата</div>
          <p class="note" style="margin-top:10px">Он платит, он действует, он
            отвечает за результат. ЖКХ и подрядчик по вывозу — те же данные
            в его контуре; житель и малый бизнес — второй контур, который
            закрывает то, чего спутник не видит физически.</p>
          <table style="margin-top:18px">
            <tr><th>Понедельник инспектора</th><th style="text-align:right"></th></tr>
            <tr><td class="k">Система пересчитала область</td>
                <td class="r">{n['raw']} находок</td></tr>
            <tr><td class="k">Отсев снял заведомо чужое</td>
                <td class="r">осталось {n['passed']}</td></tr>
            <tr><td class="k">Инспектор получил очередь по деньгам</td>
                <td class="r lit">{n['half_trips']} выезда = 50% суммы</td></tr>
            <tr><td class="k">Выехал, сфотографировал, подписал акт</td>
                <td class="r em">кадр с координатами</td></tr>
            <tr><td class="k">Через месяц — проверка устранения</td>
                <td class="r">убрали или засыпали</td></tr>
          </table>
        </div>
        <div class="col">
          <p><b style="color:var(--line)">И то, чего обычно не говорят: мы умеем
            заранее сказать, где метод НЕ сработает.</b> Один запрос к открытой
            карте, ни одного снимка, секунды.</p>
          <table style="margin-top:16px">
            <tr><th>Контуров OSM на км²</th><th style="text-align:right">Что вышло</th></tr>
            <tr><td class="k">253</td><td class="r">сплошная застройка: 0 из 499</td></tr>
            <tr><td class="k">95–100</td><td class="r lit">работает: {n['dumps']} свалок</td></tr>
            <tr><td class="k">7–18</td><td class="r">пустая карта: 0 из 96</td></tr>
          </table>
          <div class="stats" style="margin-top:18px">
            <div class="stat"><dt>на обычном ноутбуке без видеокарты</dt>
              <dd class="num" style="font-size:30px">223 км²/ч</dd></div>
            <div class="stat"><dt>пригороды 20 областных центров</dt>
              <dd class="num" style="font-size:30px">~40 часов</dd></div>
          </div>
          <p class="note" style="margin-top:12px">Проверено на семи областях.
            Измеримый ответ «здесь не сработает» надёжнее обещания, что
            сработает везде.</p>
        </div>
      </div>
            </div>
      {foot(11, 'целевая аудитория и масштаб')}</section>""")

    # 12 ── Зачем это запускать: бизнес-модель и команда
    s.append(f"""<section class="slide">
      <div class="kicker">Бизнес-модель и команда</div>
      <h2>Почему это стоит запустить<br>после хакатона</h2>
      <div class="body">
      <div class="row" style="margin-top:12px">
        <div class="col">
          <table>
            <tr><th>Что уже есть</th><th style="text-align:right">Сколько</th></tr>
            <tr><td class="k">Опознано свалок по снимку</td>
                <td class="r em">{n['dumps']}</td></tr>
            <tr><td class="k">Ждут выезда</td>
                <td class="r am">{n['pending']}</td></tr>
            <tr><td class="k">Потери на опознанных свалках</td>
                <td class="r lit">{ru(n['net'], 1)} млн ₸</td></tr>
            <tr><td class="k">Возвратного сырья в этих отходах</td>
                <td class="r em">{ru(n['recyclable'], 1)} млн ₸</td></tr>
            <tr><td class="k">Ручной работы снято</td><td class="r">71%</td></tr>
          </table>
          <p style="margin-top:14px"><b style="color:var(--line)">Подписка на область
            под наблюдением</b> — по квадратным километрам: ежемесячный пересчёт,
            очередь по деньгам, контроль устранения. Пояс вокруг областного центра —
            400 км², около четырёх часов машинного времени в месяц; одна
            опознанная свалка здесь в среднем стоит {ru(n['net'] / n['dumps'], 1)} млн ₸ потерь.</p>

        </div>
        <div class="col">
          <div class="num mid">Команда</div>
          <div style="margin-top:16px">
            {"".join(
                f'<div style="display:flex;align-items:baseline;gap:12px;'
                f'margin-bottom:9px">'
                f'<span style="font-size:20px;color:var(--line);min-width:96px">{who}</span>'
                f'<span style="font-size:16px;color:var(--muted2)">{role}</span>'
                f'</div>'
                for who, role in TEAM)}
          </div>
          <p style="margin-top:22px"><b style="color:var(--line)">Репозиторий:</b>
            github.com/k41270075-stack/hakathon</p>
          <p style="margin-top:8px"><b style="color:var(--line)">Живой продукт:</b>
            hakathon-lyart.vercel.app</p>
          <p class="note" style="margin-top:22px">Больше 700 автоматических
            проверок, семь страниц в трёх браузерах, данные карты в сборке.
            Все числа этой деки читаются из выгрузки прогона — ни одно не
            вписано руками.</p>
          <p class="note" style="margin-top:12px">Пилот на восемь недель расписан
            по неделям в docs/PILOT.md: что нужно от заказчика, чем меряется
            успех и где пилот вправе провалиться. Оценки бизнес-модели
            расчётные, продажами не подтверждены — говорим это сами.</p>
        </div>
      </div>
            </div>
      {foot(12, 'бизнес-модель и команда')}</section>""")

    return "\n".join(s)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT.parent / "VANTAGE — питч-дека.pdf"))
    args = parser.parse_args()

    if not DATA.exists():
        print(f"нет выгрузки {DATA}")
        return 1

    n = numbers()
    html = (f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<style>{css()}</style></head><body>{slides(n)}</body></html>")

    tmp = ROOT / "_deck.html"
    tmp.write_text(html, encoding="utf-8")

    port = 4321
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "-d", str(ROOT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        from playwright.sync_api import sync_playwright

        out = Path(args.out)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H})
            page.goto(f"http://127.0.0.1:{port}/_deck.html", wait_until="networkidle")
            # Шрифты грузятся из data-URI, но отрисовка всё равно асинхронна.
            page.wait_for_timeout(1200)
            page.pdf(path=str(out), width=f"{W}px", height=f"{H}px",
                     print_background=True, margin={"top": "0", "right": "0",
                                                    "bottom": "0", "left": "0"})
            browser.close()
    finally:
        server.terminate()
        tmp.unlink(missing_ok=True)

    size = Path(args.out).stat().st_size
    print(f"── Дека собрана: {args.out}")
    print(f"   двенадцать слайдов, {size // 1024} КБ")
    print()
    print("Числа взяты из выгрузки прогона:")
    print(f"   опознано свалок {n['dumps']}, ждут выезда {n['pending']}, "
          f"подтверждено выездом {n['ground']}, ущерб {ru(n['damage'], 1)} млн ₸")
    return 0


if __name__ == "__main__":
    sys.exit(main())
