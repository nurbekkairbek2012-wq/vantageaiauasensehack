/* Как это росло: восемь лет за двадцать секунд.
 *
 * Отдельная поверхность, а не режим внутри рабочей карты. Внутри карты
 * таймлапс конкурировал бы со списком объектов и проигрывал ему: там
 * задача «куда ехать сегодня», здесь — «как дошло до сегодня». Разные
 * вопросы, разные экраны.
 *
 * Время здесь непрерывно, а не по кадрам. Покадровая версия переключала
 * год целиком, и каждый шаг читался как мигание: пятна исчезали и
 * появлялись в одном кадре. Причина была не в частоте — год как единица
 * показа просто не бывает плавным. Теперь время это дробная величина,
 * пятно набирает силу за неполный год, и ход получается непрерывным сам
 * собой, без анимации отдельных элементов.
 *
 * Последний участок шкалы не повторяет предпоследний: после конца
 * наблюдений поверх накопленного прошлого проступает прогноз на двенадцать
 * месяцев вперёд. Ради этого перехода таймлапс и нужен — иначе это просто
 * анимация.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Nav } from './components/Nav';
import { BasemapSwitch } from './components/BasemapSwitch';
import { createBasemapLayer, type Basemap } from './components/basemaps';
import { createHeatOverlay, riskToPoints, type HeatOverlay, type HeatPoint } from './components/HeatOverlay';
import { canRecord, recordMap, saveBlob } from './components/recordMap';

/** Восемь лет за столько секунд. Медленнее — скучно, быстрее — не читается. */
const PLAY_SECONDS = 18;

/** Хвост шкалы за последним наблюдением, на котором проступает прогноз. */
const FORECAST_SPAN = 1.2;

export default function Timelapse() {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const tiles = useRef<L.TileLayer | null>(null);
  const heat = useRef<HeatOverlay | null>(null);
  const riskHeat = useRef<HeatOverlay | null>(null);
  const raf = useRef(0);
  /* Время для подписи на записи. Ссылка, а не состояние: замыкание внутри
     запущенной записи держит то значение, которое было при её создании, и
     подпись застывала — на всех кадрах стояло «прогноз», потому что
     страница по умолчанию открыта в конце шкалы. */
  const timeRef = useRef<number | null>(null);
  const started = useRef(0);
  const fitted = useRef(false);

  const [points, setPoints] = useState<HeatPoint[]>([]);
  const [risk, setRisk] = useState<GeoJSON.FeatureCollection | null>(null);
  const [basemap, setBasemap] = useState<Basemap>('sat');
  const [time, setTime] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [recording, setRecording] = useState(false);
  /* Готовый файл, записанный при сборке. Пока он не проверен — null;
     проверка занимает один запрос HEAD и делается один раз. */
  const [ready, setReady] = useState<boolean | null>(null);

  /* Целые годы для подписей. Время внутри дробное — иначе не бывает
     плавности, — но кнопка «2019.5833333333333» это не год, а протечка
     внутреннего представления наружу. */
  const years = useMemo(() => {
    const set = new Set<number>();
    points.forEach((p) => p.year != null && set.add(Math.floor(p.year)));
    return [...set].sort((a, b) => a - b);
  }, [points]);

  const first = years.length ? years[0] : 2018;
  const last = years.length ? years[years.length - 1] + 1 : 2026;
  const end = last + FORECAST_SPAN;

  // Пока данные не пришли, шкала стоит в конце: открывший страницу видит
  // итог, а не пустую карту, с которой непонятно, что делать.
  const at = time ?? end;
  const forecastMix = Math.max(0, Math.min(1, (at - last - 0.15) / (FORECAST_SPAN - 0.15)));

  const counted = useMemo(
    () => points.filter((p) => p.year != null && p.year <= at).length,
    [points, at],
  );

  /* Есть ли заранее записанный ролик.
   *
   * Запись в браузере идёт в реальном времени: восемнадцать секунд ролика
   * пишутся восемнадцать секунд, и ускорить это нельзя — MediaRecorder
   * ставит метки по стенным часам. Поэтому ждёт сборка, а не посетитель:
   * scripts/make_timelapse.py кладёт готовый файл рядом, и кнопка
   * становится обычной ссылкой.
   *
   * Живая запись остаётся запасным путём: файла может не быть, если
   * прогон закончился, а ролик перезаписать забыли. */
  useEffect(() => {
    fetch('./timelapse.webm', { method: 'HEAD' })
      .then((r) => setReady(r.ok))
      .catch(() => setReady(false));
  }, []);

  // ── данные ──────────────────────────────────────────────────────────
  useEffect(() => {
    fetch('./data/candidates.geojson')
      .then((r) => r.json())
      .then((fc: GeoJSON.FeatureCollection) => {
        let maxWeight = 1;
        const raw = fc.features.map((f) => {
          const b = L.geoJSON(f).getBounds().getCenter();
          const area = Math.sqrt(Math.max(0, Number(f.properties?.area_m2) || 0));
          if (area > maxWeight) maxWeight = area;
          const date = String(f.properties?.break_date ?? '');
          // Месяц важен: без него все объекты года появляются разом, и
          // получается тот же покадровый скачок, только реже.
          const year = /^\d{4}-\d{2}/.test(date)
            ? Number(date.slice(0, 4)) + (Number(date.slice(5, 7)) - 1) / 12
            : /^\d{4}/.test(date)
              ? Number(date.slice(0, 4))
              : null;
          return { lat: b.lat, lon: b.lng, weight: area, year };
        });
        setPoints(raw.map((p) => ({ ...p, weight: p.weight / maxWeight })));
      })
      .catch(() => setPoints([]));

    fetch('./data/risk_public.geojson')
      .then((r) => r.json())
      .then(setRisk)
      .catch(() => setRisk(null));
  }, []);

  // ── карта ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, {
      zoomControl: false, attributionControl: true, preferCanvas: true,
      minZoom: 9, maxZoom: 15, zoomSnap: 0.5,
    }).setView([51.21, 71.5], 11);
    L.control.zoom({ position: 'bottomright' }).addTo(m);
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);
    map.current = m;
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (tiles.current) { m.removeLayer(tiles.current); tiles.current = null; }
    tiles.current = createBasemapLayer(basemap, 15).addTo(m);
    tiles.current.bringToBack();
  }, [basemap]);

  useEffect(() => {
    const m = map.current;
    if (!m || !points.length) return;
    if (!heat.current) {
      heat.current = createHeatOverlay(points, { kind: 'density' });
      heat.current.addTo(m);
    } else {
      heat.current.setPoints(points);
    }
    if (!fitted.current) {
      m.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon])).pad(0.35), { animate: false });
      fitted.current = true;
    }
  }, [points]);

  // Ссылка обновляется эффектом, а не при отрисовке: запись читает её из
  // своего кадра, а писать в ref во время рендера React не разрешает.
  useEffect(() => {
    timeRef.current = at;
  }, [at]);

  useEffect(() => {
    heat.current?.setTime(at);
    heat.current?.setHeatOpacity(1 - forecastMix * 0.6);
  }, [at, forecastMix]);

  // Прогноз проступает прозрачностью, а не появлением слоя целиком:
  // включённый разом, он читается как ещё одно мигание в конце.
  useEffect(() => {
    const m = map.current;
    if (!m || !risk) return;
    if (!riskHeat.current) {
      riskHeat.current = createHeatOverlay(riskToPoints(risk), { kind: 'risk', opacity: 0 });
      riskHeat.current.addTo(m);
    }
    riskHeat.current.setHeatOpacity(forecastMix * 0.8);
  }, [risk, forecastMix]);

  // ── воспроизведение ─────────────────────────────────────────────────
  const stop = useCallback(() => {
    if (raf.current) { cancelAnimationFrame(raf.current); raf.current = 0; }
    setPlaying(false);
  }, []);

  const play = useCallback(() => {
    if (playing) { stop(); return; }
    const span = end - first;
    if (span <= 0) return;
    setPlaying(true);
    started.current = performance.now();
    setTime(first);

    const tick = (now: number) => {
      const passed = (now - started.current) / 1000;
      const next = first + (passed / PLAY_SECONDS) * span;
      if (next >= end) {
        setTime(end);
        raf.current = 0;
        setPlaying(false);
        return;
      }
      setTime(next);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [playing, stop, first, end]);

  useEffect(() => () => { if (raf.current) cancelAnimationFrame(raf.current); }, []);

  /* Скачивание таймлапса файлом.
   *
   * Показ вживую зависит от зала, проектора и чужого Wi-Fi. Файл на
   * флешке не зависит ни от чего, и это единственная страница, где такой
   * запас имеет смысл: карту можно показать скриншотом, движение — нет. */
  const download = useCallback(async () => {
    const node = host.current;
    if (!node || recording) return;
    stop();
    setRecording(true);
    try {
      const span = end - first;
      const blob = await recordMap(node, {
        seconds: PLAY_SECONDS,
        onFrame: (progress) => {
          const now = first + progress * span;
          timeRef.current = now;
          setTime(now);
        },
        overlay: () => {
          const now = timeRef.current ?? end;
          const forecast = now > last + 0.15;
          return {
            big: forecast ? 'прогноз' : String(Math.floor(now)),
            small: forecast
              ? 'на 12 месяцев вперёд'
              : `объектов к этому году: ${points.filter((p) => p.year != null && p.year <= now).length}`,
          };
        },
      });
      saveBlob(blob, 'vantage-ai-timelapse.webm');
    } finally {
      setRecording(false);
    }
  }, [recording, stop, end, first, last, points]);

  const label = at > last + 0.15 ? 'прогноз' : String(Math.floor(at));

  return (
    <div className="flex h-screen flex-col">
      <Nav current="timelapse">
        <p className="max-w-[42ch] text-sm text-muted-2">
          Плотность найденных объектов, накопленная год за годом. В конце
          шкалы — прогноз на двенадцать месяцев вперёд.
        </p>
      </Nav>

      <div className="relative min-h-0 flex-1">
        <div className="absolute inset-0 bg-soot" />
        <div ref={host} className="absolute inset-0" />

        <BasemapSwitch
          value={basemap}
          onChange={setBasemap}
          className="absolute right-3 top-3 z-[500]"
        />

        {/* ── Год крупно: на проекторе подпись под ползунком не читается ── */}
        <div className="pointer-events-none absolute left-6 top-6 z-[500]">
          <p
            className="tabular font-display leading-none text-line"
            style={{
              fontSize: 'clamp(3rem,7vw,6rem)',
              textShadow: '0 2px 24px rgba(13,9,24,.85), 0 0 2px rgba(13,9,24,.9)',
            }}
          >
            {label}
          </p>
          <p
            className="mt-1 text-sm text-muted"
            style={{ textShadow: '0 1px 10px rgba(13,9,24,.9)' }}
          >
            {label === 'прогноз' ? (
              <span className="text-violet-lit">на 12 месяцев вперёд</span>
            ) : (
              <>объектов к этому году: <span className="tabular text-line">{counted}</span></>
            )}
          </p>
        </div>

        {forecastMix > 0.35 && (
          /* right-16 на телефоне: без него блок доходил до кнопок зума и
             последняя строка обрезалась ими же. */
          <div className="pointer-events-none absolute bottom-6 left-4 right-16 z-[500] max-w-[22rem] rounded-sm border border-grid bg-soot/90 px-4 py-3 backdrop-blur-sm sm:left-6 sm:right-auto">
            <p className="text-sm text-line">Тепло без объектов — где свалок ещё нет</p>
            <p className="mt-1 text-xs leading-snug text-muted">
              Модель обучена на объектах до сентября 2023 и проверена на
              возникших после. Попадает в 293 раза точнее случайного выбора.
            </p>
          </div>
        )}
      </div>

      {/* ── Управление ────────────────────────────────────────────────── */}
      <div className="flex shrink-0 flex-wrap items-center gap-4 border-t border-grid px-5 py-3">
        <button
          type="button"
          onClick={play}
          className="cursor-pointer rounded-sm bg-violet px-5 py-2.5 font-display text-sm font-semibold uppercase tracking-[0.12em] text-paper transition-colors duration-200 hover:bg-violet-lit hover:text-soot"
        >
          {playing ? 'Стоп' : 'Проиграть'}
        </button>

        {ready ? (
          <a
            href="./timelapse.webm"
            download="vantage-ai-timelapse.webm"
            className="rounded-sm border border-grid px-4 py-2.5 text-sm text-muted no-underline transition-colors duration-150 hover:border-violet hover:text-line"
          >
            Скачать видео
          </a>
        ) : ready === false && canRecord() ? (
          <button
            type="button"
            onClick={download}
            disabled={recording}
            title="Готовый файл не найден — ролик запишется прямо здесь, это займёт около двадцати секунд"
            className="cursor-pointer rounded-sm border border-grid px-4 py-2.5 text-sm text-muted transition-colors duration-150 hover:border-violet hover:text-line disabled:cursor-not-allowed disabled:opacity-60"
          >
            {recording ? 'Записываю…' : 'Записать видео'}
          </button>
        ) : null}

        <input
          type="range"
          min={first}
          max={end}
          step={0.02}
          value={at}
          onChange={(e) => { stop(); setTime(Number(e.target.value)); }}
          aria-label="Время"
          aria-valuetext={label}
          className="min-w-[12rem] flex-1 accent-violet"
        />

        <div className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-muted-2">
          {years.map((y) => (
            <button
              key={y}
              type="button"
              onClick={() => { stop(); setTime(y + 0.99); }}
              className={`tabular cursor-pointer rounded-sm px-2 py-1 transition-colors duration-150 ${
                Math.floor(at) === y && label !== 'прогноз'
                  ? 'bg-violet-deep/60 text-line'
                  : 'hover:text-line'
              }`}
            >
              {y}
            </button>
          ))}
          <button
            type="button"
            onClick={() => { stop(); setTime(end); }}
            className={`cursor-pointer rounded-sm px-2 py-1 transition-colors duration-150 ${
              label === 'прогноз' ? 'bg-violet-deep/60 text-line' : 'hover:text-line'
            }`}
          >
            прогноз
          </button>
        </div>
      </div>
    </div>
  );
}
