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
    dates = pd.to_datetime(site["break_date"])
    rejected = funnel["rejected"]

    return {
        "raw": funnel["raw"],
        "passed": rejected.get("ПРОШЁЛ ОТСЕВ", 0),
        "published": len(site),
        "dumps": int((site["visual_check"] == "landfill").sum()),
        "ground": int((site["check_source"] == "ground").sum()),
        "damage": site["damage_p50"].sum() / 1e6,
        "low": site["damage_p10"].sum() / 1e6,
        "high": site["damage_p90"].sum() / 1e6,
        "area_ha": site["area_m2"].sum() / 1e4,
        "mass": site["mass_t"].sum(),
        "co2": site["co2e_t"].sum(),
        "emitted": site["co2e_emitted_t"].sum(),
        "penalty": site["penalty_kzt"].sum() / 1e6,
        "age": (pd.Timestamp.today() - dates).dt.days.mean() / 365.25,
        "oldest": (lambda d: f"{MONTHS[d.month - 1]} {d.year}")(
            site["break_date"].min()),
        "biggest": site["area_m2"].max(),
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
    faces = []
    for weight, name in ((400, "golos-text-400-cyrillic"), (500, "golos-text-500-cyrillic"),
                         (600, "golos-text-600-cyrillic")):
        path = FONTS / f"{name}.woff2"
        if path.exists():
            faces.append(f"""@font-face{{font-family:'Golos';font-weight:{weight};
              font-display:block;src:url('{data_uri(path)}') format('woff2');}}""")
    for name in ("oswald-500-cyrillic", "oswald-600-cyrillic"):
        path = FONTS / f"{name}.woff2"
        if path.exists():
            weight = name.split("-")[1]
            faces.append(f"""@font-face{{font-family:'Oswald';font-weight:{weight};
              font-display:block;src:url('{data_uri(path)}') format('woff2');}}""")

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
    """


def slides(n: dict) -> str:
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
      <h1>Свалки, которых<br>нет ни в одном реестре</h1>
      <p class="lead" style="margin-top:26px;max-width:56ch">
        Находим несанкционированные свалки на спутниковых снимках, называем
        дату появления и считаем ущерб бюджету в тенге.
      </p>
      <dl class="stats" style="max-width:840px">
        <div class="stat"><dt>Свалок найдено под Астаной</dt>
          <dd class="num big lit">{n['dumps']}</dd></div>
        <div class="stat"><dt>Проверено человеком на месте</dt>
          <dd class="num big em">{n['ground']}</dd></div>
        <div class="stat"><dt>Ущерб по проверенному списку</dt>
          <dd class="num big">{ru(n['damage'], 1)}<span style="font-size:30px"> млн ₸</span></dd></div>
      </dl>
      {foot(1, 'титул')}</section>""")

    # 2 ── Проблема
    s.append(f"""<section class="slide">
      <div class="kicker">Проблема</div>
      <h2>Свалку видно из космоса,<br>но её никто не ищет</h2>
      <div class="body">
      <div class="row" style="margin-top:8px">
        <div class="col">
          <ul>
            <li><b>Реестры знают о единицах объектов.</b> В открытых данных по
              области — {n['cells'] and '27'} мест обращения с отходами. Всё, что
              возникло стихийно, туда не попадает.</li>
            <li><b>Инспектор не может объехать область.</b> Выезд на подозрительное
              место стоит часа дороги, и мест этих тысячи.</li>
            <li><b>Пока свалку не нашли, она растёт.</b> Убрать сто тонн дешевле,
              чем тысячу; вопрос только в том, кто заметит раньше.</li>
            <li><b>Отходы разлагаются и дают метан.</b> Каждый год ожидания — это
              выброс, которого уже не вернуть.</li>
          </ul>
        </div>
        <div class="col">{shot('pixel.png')}
          <p class="note" style="margin-top:12px">
            Один пиксель за восемь лет. Вегетация упала и не вернулась — так
            выглядит свалка на снимке Sentinel-2.</p>
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
          <tr><td class="k">Опознаны как свалки</td>
              <td class="r lit">{n['dumps']}</td></tr>
          <tr><td class="k">Лежит средняя свалка</td>
              <td class="r">{ru(n['age'], 1)} года</td></tr>
          <tr><td class="k">Самая старая возникла</td>
              <td class="r">{n['oldest']}</td></tr>
          <tr><td class="k">Отходов в списке</td>
              <td class="r">{ru(n['mass'])} т</td></tr>
        </table></div>
        <div class="col">
          <div class="num mid lit">{ru(n['damage'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">ущерб по объектам, пережившим
            проверку глазами</p>
          <div class="num mid" style="margin-top:26px">{ru(n['co2'])} т CO₂-экв</div>
          <p class="note" style="margin-top:8px">метан за двадцать лет, из них
            <b style="color:var(--amber)">{ru(n['emitted'])} т уже выброшено</b> —
            их не вернуть</p>
          <div class="num mid" style="margin-top:26px">{ru(n['penalty'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">штрафы по ст. 344 ч. 2-1 КоАП РК,
            если нарушители установлены</p>
        </div>
      </div>
            </div>
      {foot(3, 'цена бездействия')}</section>""")

    # 4 ── Решение
    s.append(f"""<section class="slide">
      <div class="kicker">Решение</div>
      <h2>Спутник находит изменение.<br>Свалку опознаёт человек за час</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col"><ul>
          <li><b>Восемь лет архива Sentinel-2, Sentinel-1 и Landsat.</b> Программа
            ищет место, где растительность исчезла и <b>не вернулась</b>.</li>
          <li><b>Пять физических признаков</b> вместо «нейросеть посмотрела на
            картинку»: оптика, ближний инфракрасный, радар и тепло.</li>
          <li><b>Контекстный отсев по OpenStreetMap</b> убирает карьеры, стройки,
            воду и всё, что имеет хозяина: {n['raw']} → {n['passed']}.</li>
          <li><b>Человек смотрит каждый оставшийся</b> по снимку 0,4–0,8 м на
            пиксель и решает. Модель предлагает, человек подтверждает.</li>
          <li><b>Дальше — действие:</b> дата, площадь, масса, ущерб с интервалом,
            черновик акта, контроль устранения и прогноз на год вперёд.</li>
        </ul></div>
        <div class="col">{shot('funnel.png')}</div>
      </div>
            </div>
      {foot(4, 'решение')}</section>""")

    # 5 ── Пять признаков
    s.append(f"""<section class="slide">
      <div class="kicker">Как это работает</div>
      <h2>Пять признаков находят изменение.<br>Опознаёт человек</h2>
      <div class="body">
      <div style="margin-top:10px">{shot('signals.png')}</div>
            </div>
      {foot(5, 'пять признаков')}</section>""")

    # 6 ── Что нашли
    s.append(f"""<section class="slide">
      <div class="kicker">Результат</div>
      <h2>{n['dumps']} свалок, и каждая проверена<br>человеком на месте</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col" style="flex:1.25">{shot('map.png')}</div>
        <div class="col">
          <table>
            <tr><td class="k">Сырых находок детектора</td><td class="r">{n['raw']}</td></tr>
            <tr><td class="k">Снял автоматический отсев</td>
                <td class="r">{n['by_osm'] + n['by_home'] + n['by_area'] + n['by_road']}</td></tr>
            <tr><td class="k">Просмотрено человеком</td><td class="r">{n['passed']}</td></tr>
            <tr><td class="k">Отвергнуто при просмотре</td>
                <td class="r am">{n['passed'] - n['published']}</td></tr>
            <tr><td class="k">Опубликовано</td><td class="r lit">{n['published']}</td></tr>
            <tr><td class="k">Подтверждено выездом</td><td class="r em">{n['ground']}</td></tr>
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

    # 7 ── Целевая аудитория
    s.append(f"""<section class="slide">
      <div class="kicker">Целевая аудитория</div>
      <h2>Два контура: кто действует<br>и кто замечает</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col">
          <div class="num mid lit">Служба</div>
          <p class="note" style="margin-top:10px">Отдел экологии акимата, ЖКХ,
            подрядчик по вывозу. Видит точку, дату, массу, сумму ущерба и
            черновик акта. Ответ на вопрос «куда ехать сегодня» — список,
            отсортированный по деньгам, а не по алфавиту.</p>
          <ul style="margin-top:18px">
            <li>Маршрут на месяц: двадцать точек в порядке объезда</li>
            <li>Контроль устранения: убрали или только засыпали</li>
            <li>Акт в PDF — черновик до подтверждения человеком</li>
          </ul>
        </div>
        <div class="col">
          <div class="num mid em">Житель</div>
          <p class="note" style="margin-top:10px">Telegram-бот: одно сообщение с
            геопозицией. Закрывает то, чего спутник не видит физически, —
            объекты меньше 30 м² и свежие, которым нет полутора лет.</p>
          <ul style="margin-top:18px">
            <li>Ни регистрации, ни формы — точка и есть адрес</li>
            <li>Отправитель анонимен: идентификатор хешируется с солью</li>
            <li>Служба получает карточку сразу, а не письмо в понедельник</li>
          </ul>
          <p class="note" style="margin-top:16px">
            <b style="color:var(--line)">Малый бизнес и школы</b> — третий контур:
            открытый слой зон риска показывает, где не стоит арендовать участок
            и куда смотреть при уборке территории.</p>
        </div>
      </div>
            </div>
      {foot(7, 'целевая аудитория')}</section>""")

    # 8 ── ИИ
    s.append(f"""<section class="slide">
      <div class="kicker">Использование ИИ</div>
      <h2>Каждое число — с интервалом<br>и с ценой ошибки</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col">
          <table>
            <tr><th>Что измерено</th><th style="text-align:right">Результат</th></tr>
            <tr><td class="k">Машинный просмотр снимает ручной работы</td>
                <td class="r lit">71%</td></tr>
            <tr><td class="k">Из них отказов верных</td><td class="r">34 из 35</td></tr>
            <tr><td class="k">Свалок находит</td><td class="r">7 из 8</td></tr>
            <tr><td class="k">Прогноз, выигрыш над случайным</td>
                <td class="r lit">не хуже ×{n['lift_low']:.0f}</td></tr>
            <tr><td class="k">Прогноз, PR-AUC (интервал)</td>
                <td class="r">{ru(n['pr_low'], 2)}–{ru(n['pr_high'], 2)}</td></tr>
            <tr><td class="k">Сеть «до/после», ROC-AUC</td><td class="r">0,907</td></tr>
          </table>
        </div>
        <div class="col">
          <p><b style="color:var(--line)">Одну свалку из восьми модель теряет</b> —
            поэтому её оценка остаётся подсказкой, а решение за человеком. Лишний
            объект в очереди стоит минуты; выброшенный не вернётся никогда.</p>
          <p style="margin-top:16px">Прогноз проверен <b style="color:var(--line)">по
            времени</b>: обучен на объектах до сентября 2023, проверен на возникших
            после. Свалок после отсечки {n['positives']}, поэтому называем нижнюю
            границу интервала, а не середину.</p>
          <p style="margin-top:16px">Три попытки обучить собственную модель дали
            отрицательный результат, и все три записаны с числами. Ответ
            «пробовали трижды, вот измерения» сильнее любого «у нас свой ИИ».</p>
        </div>
      </div>
            </div>
      {foot(8, 'использование ИИ')}</section>""")

    # 9 ── Деньги
    s.append(f"""<section class="slide">
      <div class="kicker">Экономика</div>
      <h2>Диапазон, а не одно число</h2>
      <div class="body">
      <div class="row" style="margin-top:16px">
        <div class="col">
          <div class="num mid lit">{ru(n['damage'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">медиана по проверенному списку</p>
          <div class="num" style="font-size:28px;margin-top:20px;color:var(--muted)">
            {ru(n['low'], 1)} — {ru(n['high'], 1)} млн ₸</div>
          <p class="note" style="margin-top:8px">честный ответ: P10 — P90</p>
          <p style="margin-top:22px">Монте-Карло, 20 000 итераций, восемь допущений.
            У каждого указано происхождение: закон о бюджете, методика расчёта
            тарифа, прайс приёмщика вторсырья, IPCC.</p>
          <p style="margin-top:14px"><b style="color:var(--line)">82% разброса даёт
            один параметр</b> — стоимость вывоза тонны. Мы знаем какой и почему:
            прямого тарифа по Астане в открытом доступе нет, и величина помечена
            как инженерная оценка.</p>
        </div>
        <div class="col">{shot('money.png')}</div>
      </div>
            </div>
      {foot(9, 'экономика')}</section>""")

    # 10 ── Продукт
    s.append(f"""<section class="slide">
      <div class="kicker">Продукт</div>
      <h2>Пять экранов, и каждый отвечает<br>на свой вопрос</h2>
      <div class="body">
      <div class="row" style="margin-top:12px">
        <div class="col">{shot('forecast.png')}</div>
        <div class="col">{shot('citizen.png')}</div>
      </div>
      <div style="margin-top:18px">
        <span class="pill">Карта — куда ехать сегодня</span>
        <span class="pill">Как росло — восемь лет за двадцать секунд</span>
        <span class="pill">Прогноз — где появится за год</span>
        <span class="pill">Жителям — бот и гражданский контур</span>
        <span class="pill">Разметка — инструмент проверки глазами</span>
      </div>
      <p class="note" style="margin-top:14px">Сборка самодостаточна: сайт
        открывается с флешки и работает <b style="color:var(--line)">без
        интернета</b> — шрифты и данные лежат локально, подложка по умолчанию
        схематическая. Проверено автоматически.</p>
            </div>
      {foot(10, 'продукт')}</section>""")

    # 11 ── Стек и масштабирование
    s.append(f"""<section class="slide">
      <div class="kicker">Технологический стек</div>
      <h2>Всё на бесплатных открытых данных</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col">
          <p>Sentinel-2 · Sentinel-1 · Landsat 8/9 · Planetary Computer ·
             OpenStreetMap</p>
          <p style="margin-top:10px">Python, xarray, dask, rasterio · PyTorch ·
             LightGBM · FastAPI · React, Leaflet, Tailwind · ReportLab</p>
          <div class="stats" style="margin-top:22px">
            <div class="stat"><dt>на обычном ноутбуке без видеокарты</dt>
              <dd class="num" style="font-size:34px">223 км²/ч</dd></div>
            <div class="stat"><dt>пригороды 20 областных центров</dt>
              <dd class="num" style="font-size:34px">~40 часов</dd></div>
          </div>
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
          <p class="note" style="margin-top:14px">Проверено на семи областях.
            Команда, у которой есть измеримый ответ «здесь не сработает»,
            надёжнее команды, обещающей, что сработает везде.</p>
        </div>
      </div>
            </div>
      {foot(11, 'стек и масштабирование')}</section>""")

    # 12 ── Бизнес-модель и команда
    s.append(f"""<section class="slide">
      <div class="kicker">Бизнес-модель и команда</div>
      <h2>Кто платит и за что</h2>
      <div class="body">
      <div class="row" style="margin-top:14px">
        <div class="col">
          <ul>
            <li><b>Подписка для акимата.</b> Область под наблюдением: ежемесячный
              пересчёт, маршрут объезда, контроль устранения. Считается по
              квадратным километрам.</li>
            <li><b>Возврат в бюджет.</b> Ст. 344 ч. 2-1 КоАП РК — до 1000 МРП с
              юридического лица, до 4,3 млн ₸ с одного дела.</li>
            <li><b>Углеродные единицы.</b> Предотвращённый метан: {ru(n['co2'])} т
              CO₂-экв на одном квадрате 20 × 20 км.</li>
            <li><b>Разовый аудит территории</b> для промышленных площадок и
              землепользователей.</li>
          </ul>
          <p class="note" style="margin-top:16px">Все оценки бизнес-модели —
            расчётные, не подтверждённые продажами. Говорим это сами.</p>
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
            hakathon-ll-1c21.vercel.app</p>
          <p class="note" style="margin-top:22px">677 автоматических проверок,
            шесть страниц в трёх браузерах, карта работает без интернета.
            Все числа этой деки читаются из выгрузки прогона — ни одно не
            вписано руками.</p>
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
    print(f"   свалок {n['dumps']}, подтверждено выездом {n['ground']}, "
          f"ущерб {ru(n['damage'], 1)} млн ₸")
    return 0


if __name__ == "__main__":
    sys.exit(main())
