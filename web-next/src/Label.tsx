/* Ручная разметка: пара «до / после», живой снимок и три кнопки.
 *
 * Это не витрина, а инструмент, и он закрывает самый дорогой пункт списка
 * недоделанного. Сеть не обучается, потому что положительных примеров из
 * OpenStreetMap не набирается в принципе: внутри существующего полигона ТБО
 * детектор изменений ничего не находит — там и в 2018 году была голая
 * поверхность. Остаётся посмотреть глазами.
 *
 * ── Почему здесь три картинки, а не две ─────────────────────────────
 *
 * Пара чипов отвечает на вопрос «что изменилось» и делает это единственным
 * доступным способом — по Sentinel-2, где пиксель равен десяти метрам.
 * Объект в 40 метров занимает на таком снимке четыре пикселя, и вопрос
 * «свалка это или карьер» по ним не решается никак. Первая версия страницы
 * показывала только их, и честный ответ на любой чип был «не понятно».
 *
 * Третья картинка — то же место на живом снимке Esri, 0,75 метра на
 * пиксель. Она отвечает на другой вопрос: «что там на самом деле». Даты у
 * неё своей нет, она показывает сегодня, и поэтому не заменяет пару, а
 * дополняет её: пара датирует изменение, снимок называет предмет.
 *
 * ── Остальные решения ───────────────────────────────────────────────
 *
 * Три кнопки, а не две. «Не понятно» — полноценный ответ: заставлять
 * человека выбирать между «свалка» и «не свалка» на мутном чипе значит
 * набирать шум и учить на нём сеть.
 *
 * Клавиши 1/2/3 и автопереход. Размечать надо сотни объектов; каждое
 * лишнее движение мыши умножается на эту сотню.
 *
 * Сохранение в localStorage на каждый ответ. Работа на сорок минут не
 * должна пропадать от закрытой вкладки.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Nav } from './components/Nav';
import { createBasemapLayer } from './components/basemaps';

type Chip = {
  id: string;
  slug: string;
  area_m2?: number;
  break_date?: string;
  ndvi_drop?: number;
  bsi_rise?: number;
  lat?: number;
  lon?: number;
};

type Index = { chips: Chip[]; span_m?: number };

type Verdict = 'landfill' | 'not' | 'unclear';

const STORE = 'vantage.labels.v1';

const CHOICES: [Verdict, string, string][] = [
  ['landfill', 'Свалка', '1'],
  ['not', 'Не свалка', '2'],
  ['unclear', 'Не понятно', '3'],
];

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

function humanDate(v?: string) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/* Десятичный разделитель — запятая, как на остальных страницах.
   Значения признаков приходят числами и печатались как «0.347». */
const dec = (v: unknown, digits = 3) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits).replace('.', ',') : '—';
};

export default function Label() {
  const [chips, setChips] = useState<Chip[]>([]);
  const [span, setSpan] = useState(280);
  const [labels, setLabels] = useState<Record<string, Verdict>>({});
  const [at, setAt] = useState(0);
  const [loaded, setLoaded] = useState(false);

  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const pin = useRef<L.CircleMarker | null>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORE);
      if (saved) setLabels(JSON.parse(saved));
    } catch { /* повреждённое хранилище не должно ронять страницу */ }

    fetch('./data/chips.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Index | null) => {
        setChips(d?.chips ?? []);
        if (d?.span_m) setSpan(d.span_m);
      })
      .catch(() => setChips([]))
      .finally(() => setLoaded(true));
  }, []);

  // Начинать надо с первого неразмеченного, а не с первого в списке:
  // иначе после перезагрузки человек снова листает то, что уже прошёл.
  useEffect(() => {
    if (!chips.length) return;
    const next = chips.findIndex((c) => !labels[c.id]);
    setAt(next < 0 ? chips.length - 1 : next);
    // намеренно один раз, при появлении списка
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chips.length]);

  const current = chips[at];
  const done = useMemo(() => Object.keys(labels).length, [labels]);
  const counts = useMemo(() => {
    const out: Record<Verdict, number> = { landfill: 0, not: 0, unclear: 0 };
    Object.values(labels).forEach((v) => (out[v] += 1));
    return out;
  }, [labels]);

  // ── Живой снимок ────────────────────────────────────────────────────
  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, {
      zoomControl: false,
      attributionControl: true,
      // Ни перетаскивания, ни колеса: это опорная картинка, а не карта.
      // Уехавший вид пришлось бы возвращать руками на каждом объекте.
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      minZoom: 14,
      maxZoom: 19,
    }).setView([51.21, 71.5], 17);
    createBasemapLayer('sat', 19).addTo(m);
    map.current = m;

    /* Leaflet запоминает размер контейнера в момент создания. Здесь карта
       живёт в ячейке сетки, которая получает высоту только после того, как
       браузер разложит соседние картинки, — на момент создания она нулевая,
       и карта остаётся чёрным прямоугольником навсегда. Первый снимок
       страницы показал ровно это. Наблюдатель за размером надёжнее
       таймаута: он срабатывает и при повороте телефона. */
    const watch = new ResizeObserver(() => m.invalidateSize());
    watch.observe(host.current);
    return () => { watch.disconnect(); m.remove(); map.current = null; };
    /* Зависимости не пустые, и это не перестраховка. Контейнер карты
       рендерится условно — пока данные не пришли, на его месте null. Эффект
       с пустым списком отрабатывал один раз, до появления контейнера, молча
       выходил по host.current === null и второго шанса не получал: снимок
       страницы показывал чёрный прямоугольник вместо снимка. */
  }, [loaded, chips.length]);

  useEffect(() => {
    const m = map.current;
    if (!m || current?.lat == null || current?.lon == null) return;
    const point: [number, number] = [current.lat, current.lon];
    m.setView(point, 17, { animate: false });
    if (pin.current) m.removeLayer(pin.current);
    pin.current = L.circleMarker(point, {
      radius: 13, color: '#ede9fe', weight: 2, fill: false, opacity: 0.9,
    }).addTo(m);
  }, [current]);

  const decide = useCallback(
    (verdict: Verdict) => {
      if (!current) return;
      setLabels((prev) => {
        const next = { ...prev, [current.id]: verdict };
        try { localStorage.setItem(STORE, JSON.stringify(next)); } catch { /* приватный режим */ }
        return next;
      });
      setAt((i) => Math.min(i + 1, chips.length - 1));
    },
    [current, chips.length],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      const choice = CHOICES.find(([, , key]) => key === e.key);
      if (choice) { e.preventDefault(); decide(choice[0]); return; }
      if (e.key === 'ArrowRight') setAt((i) => Math.min(i + 1, chips.length - 1));
      if (e.key === 'ArrowLeft') setAt((i) => Math.max(i - 1, 0));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [decide, chips.length]);

  const download = () => {
    const payload = JSON.stringify(
      { labelled_at: new Date().toISOString(), labels },
      null,
      1,
    );
    const url = URL.createObjectURL(new Blob([payload], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'labels.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="label">
        <div className="flex items-center gap-5 text-sm">
          {/* Счётчик — про эту вкладку браузера, а не про проект.
              Разметка здесь копится в localStorage и выгружается кнопкой
              рядом; настоящая разметка проекта живёт в
              labels_manual.geojson и привязана к геометрии.
              Без пояснения посетитель видит «размечено 0» и делает вывод,
              что никто ничего не размечал — при 179 объектах, просмотренных
              глазами. */}
          <span className="tabular text-muted-2">
            в этой вкладке размечено <span className="text-line">{done}</span> из {chips.length}
          </span>
          <button
            type="button"
            onClick={download}
            disabled={!done}
            className="cursor-pointer rounded-sm border border-grid px-3 py-1.5 text-muted transition-colors duration-150 hover:border-violet hover:text-line disabled:cursor-not-allowed disabled:opacity-40"
          >
            Скачать labels.json
          </button>
        </div>
      </Nav>

      <main className="mx-auto w-full max-w-[1280px] flex-1 px-6 py-7">
        {!loaded ? null : !chips.length ? (
          <div className="max-w-[60ch]">
            <h1 className="text-2xl text-line">Чипов нет</h1>
            <p className="mt-3 text-muted">
              Пары «до / после» выгружаются из результатов прогона:
            </p>
            <pre className="mt-4 overflow-x-auto rounded-sm border border-grid bg-soot-2 px-3 py-2.5 text-xs text-muted">
python scripts/export_chips.py
            </pre>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-4">
              <h1 className="text-xl text-line">Свалка на снимке «после»?</h1>
              <p className="tabular text-sm text-muted-2">
                {at + 1} / {chips.length} · {current?.id}
              </p>
            </div>

            <div className="mt-4 grid gap-4 lg:grid-cols-3">
              {(['before', 'after'] as const).map((side) => (
                <figure key={side} className="m-0">
                  <figcaption className="mb-1.5 flex items-baseline justify-between text-sm">
                    <span className={side === 'after' ? 'text-line' : 'text-muted-2'}>
                      {side === 'before' ? 'До разрыва' : 'После разрыва'}
                    </span>
                    <span className="text-muted-2">
                      {side === 'after' ? humanDate(current?.break_date) : 'Sentinel-2'}
                    </span>
                  </figcaption>
                  <img
                    src={`./chips/${current?.slug}-${side}.png`}
                    alt={side === 'before' ? 'Снимок до разрыва' : 'Снимок после разрыва'}
                    /* Сглаживание, а не ступеньки. Ступенчатое увеличение
                       честнее — показывает ровно измеренные пиксели, — но
                       человек не умеет читать шахматную доску, и первая
                       версия страницы получала «не понятно» на любой чип.
                       Интерполяция не добавляет сведений; она делает
                       имеющиеся различимыми. Разрешение названо подписью. */
                    className="aspect-square w-full rounded-sm border border-grid bg-soot-2 object-cover"
                  />
                </figure>
              ))}

              <figure className="m-0">
                <figcaption className="mb-1.5 flex items-baseline justify-between text-sm">
                  <span className="text-line">Сегодня, высокое разрешение</span>
                  <span className="text-muted-2">0,75 м/пиксель</span>
                </figcaption>
                <div className="relative aspect-square w-full overflow-hidden rounded-sm border border-grid bg-soot-2">
                  <div ref={host} className="absolute inset-0" />
                  {current?.lat == null && (
                    <p className="absolute inset-0 grid place-items-center px-4 text-center text-xs text-muted-2">
                      Координат у этого куска нет — он выгружен до того, как
                      их начали писать в индекс.
                    </p>
                  )}
                </div>
              </figure>
            </div>

            <p className="mt-2.5 max-w-[80ch] text-xs leading-snug text-muted-2">
              Пара слева — Sentinel-2, десять метров на пиксель, кадр{' '}
              <span className="tabular">{span}</span> м в стороне. Она датирует
              изменение, но не позволяет назвать предмет. Снимок справа
              показывает сегодняшнее состояние и своей даты не имеет: если
              объект вывезли, там будет чисто.
            </p>

            <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm text-muted-2">
              <div className="flex gap-2">
                <dt>Площадь</dt>
                <dd className="tabular text-line">{(current?.area_m2 ?? 0).toLocaleString('ru-RU')} м²</dd>
              </div>
              <div className="flex gap-2">
                <dt>Падение NDVI</dt>
                <dd className="tabular text-line">{dec(current?.ndvi_drop)}</dd>
              </div>
              <div className="flex gap-2">
                <dt>Рост BSI</dt>
                <dd className="tabular text-line">{dec(current?.bsi_rise)}</dd>
              </div>
              {labels[current?.id ?? ''] && (
                <div className="flex gap-2">
                  <dt>Уже размечен</dt>
                  <dd className="text-violet-lit">
                    {CHOICES.find(([v]) => v === labels[current.id])?.[1]}
                  </dd>
                </div>
              )}
            </dl>

            <div className="mt-6 flex flex-wrap gap-3">
              {CHOICES.map(([verdict, label, key]) => (
                <button
                  key={verdict}
                  type="button"
                  onClick={() => decide(verdict)}
                  className="flex cursor-pointer items-center gap-3 rounded-sm border border-grid px-5 py-3 text-line transition-colors duration-150 hover:border-violet hover:bg-violet-deep/40"
                >
                  <span className="tabular rounded-sm bg-soot-3 px-2 py-0.5 text-xs text-muted-2">
                    {key}
                  </span>
                  {label}
                </button>
              ))}
              <div className="flex items-center gap-2 text-sm text-muted-2">
                <button
                  type="button"
                  onClick={() => setAt((i) => Math.max(i - 1, 0))}
                  className="cursor-pointer rounded-sm px-2 py-1 hover:text-line"
                >
                  ← назад
                </button>
                <button
                  type="button"
                  onClick={() => setAt((i) => Math.min(i + 1, chips.length - 1))}
                  className="cursor-pointer rounded-sm px-2 py-1 hover:text-line"
                >
                  пропустить →
                </button>
              </div>
            </div>

            <div className="mt-7 h-[3px] w-full bg-grid">
              <div
                className="h-full bg-violet-lit transition-[width] duration-300"
                style={{ width: `${chips.length ? (done / chips.length) * 100 : 0}%` }}
              />
            </div>
            <p className="tabular mt-3 text-sm text-muted-2">
              свалка {counts.landfill} · не свалка {counts.not} · не понятно {counts.unclear}
            </p>

            <p className="mt-6 max-w-[70ch] text-sm leading-relaxed text-muted-2">
              Разметка хранится в браузере и не уходит никуда. Когда наберётся
              хотя бы по пять примеров каждого класса, скачайте файл и
              скормите его обучению:{' '}
              <code className="text-muted">python scripts/train_from_labels.py labels.json</code>.
            </p>
          </>
        )}
      </main>
    </div>
  );
}
