/* Как это росло: восемь лет за двадцать секунд.
 *
 * Отдельная поверхность, а не режим внутри рабочей карты. Внутри карты
 * таймлапс конкурировал бы со списком объектов и проигрывал ему: там
 * задача «куда ехать сегодня», здесь — «как дошло до сегодня». Разные
 * вопросы, разные экраны.
 *
 * Последний кадр не повторяет предпоследний: после 2026 года поверх
 * накопленного прошлого ложится прогноз на двенадцать месяцев вперёд.
 * Ради этого перехода таймлапс и нужен — иначе это просто анимация.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Nav } from './components/Nav';
import { createHeatLayer, type HeatPoint } from './components/HeatLayer';

const PLAY_STEP_MS = 1100;

type Frame = number | 'forecast';

export default function Timelapse() {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const heat = useRef<ReturnType<typeof createHeatLayer> | null>(null);
  const riskLayer = useRef<L.GeoJSON | null>(null);
  const timer = useRef<number | null>(null);

  const [points, setPoints] = useState<HeatPoint[]>([]);
  const [risk, setRisk] = useState<GeoJSON.FeatureCollection | null>(null);
  const [frame, setFrame] = useState<Frame>('forecast');
  const [playing, setPlaying] = useState(false);

  const years = useMemo(() => {
    const set = new Set<number>();
    points.forEach((p) => p.year != null && set.add(p.year));
    return [...set].sort((a, b) => a - b);
  }, [points]);

  const frames: Frame[] = useMemo(() => [...years, 'forecast'], [years]);
  const index = frames.indexOf(frame);

  const counted = useMemo(() => {
    if (frame === 'forecast') return points.length;
    return points.filter((p) => p.year != null && p.year <= frame).length;
  }, [points, frame]);

  // данные
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
          const year = /^\d{4}/.test(date) ? Number(date.slice(0, 4)) : null;
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

  // карта
  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, {
      zoomControl: false, attributionControl: false, preferCanvas: true,
      minZoom: 9, maxZoom: 15, zoomSnap: 0.5,
    }).setView([51.21, 71.5], 11);
    L.control.zoom({ position: 'bottomright' }).addTo(m);
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);
    map.current = m;
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !points.length) return;
    if (!heat.current) {
      heat.current = createHeatLayer({ points, year: null });
      heat.current.addTo(m);
    } else {
      heat.current.setPoints(points);
    }
    m.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon])).pad(0.35), { animate: false });
  }, [points]);

  useEffect(() => {
    heat.current?.setYear(frame === 'forecast' ? null : frame);
  }, [frame]);

  // прогноз показывается только на последнем кадре
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (riskLayer.current) { m.removeLayer(riskLayer.current); riskLayer.current = null; }
    if (frame !== 'forecast' || !risk) return;
    riskLayer.current = L.geoJSON(risk, {
      style: (f) => {
        const cls = Number(f?.properties?.risk_class) || 1;
        return {
          color: '#a78bfa', weight: 1, dashArray: '6 5',
          fillColor: '#7c3aed', fillOpacity: 0.04 + cls * 0.035,
        };
      },
    }).addTo(m);
  }, [frame, risk]);

  const step = useCallback(() => {
    setFrame((current) => {
      const list = frames;
      const at = list.indexOf(current);
      return at < 0 || at >= list.length - 1 ? list[0] : list[at + 1];
    });
  }, [frames]);

  useEffect(() => {
    if (!playing || !frames.length) return;
    timer.current = window.setInterval(() => {
      setFrame((current) => {
        const at = frames.indexOf(current);
        if (at >= frames.length - 1) { setPlaying(false); return frames[frames.length - 1]; }
        return frames[at + 1];
      });
    }, PLAY_STEP_MS);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [playing, frames]);

  const play = () => {
    if (playing) { setPlaying(false); return; }
    setFrame(frames[0]);
    setPlaying(true);
  };

  return (
    <div className="flex h-screen flex-col">
      <Nav current="timelapse">
        <p className="max-w-[42ch] text-sm text-muted-2">
          Плотность найденных объектов, накопленная год за годом. Последний
          кадр — не 2026-й, а прогноз на двенадцать месяцев вперёд.
        </p>
      </Nav>

      <div className="relative min-h-0 flex-1">
        <div className="absolute inset-0 bg-soot map-grid" />
        <div ref={host} className="absolute inset-0" />

        {/* ── Год крупно: на проекторе подпись под ползунком не читается ── */}
        <div className="pointer-events-none absolute left-6 top-6 z-[500]">
          <p className="tabular font-display text-[clamp(3rem,7vw,6rem)] leading-none text-line">
            {frame === 'forecast' ? '2027' : frame}
          </p>
          <p className="mt-1 text-sm text-muted">
            {frame === 'forecast' ? (
              <span className="text-violet-lit">прогноз на 12 месяцев</span>
            ) : (
              <>
                объектов к этому году: <span className="tabular text-line">{counted}</span>
              </>
            )}
          </p>
        </div>

        {frame === 'forecast' && (
          <div className="pointer-events-none absolute right-6 top-6 z-[500] max-w-[22rem] rounded-sm border border-grid bg-soot/90 px-4 py-3">
            <p className="text-sm text-line">Пунктиром — где свалок ещё нет</p>
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

        <input
          type="range"
          min={0}
          max={Math.max(0, frames.length - 1)}
          value={index < 0 ? 0 : index}
          onChange={(e) => { setPlaying(false); setFrame(frames[Number(e.target.value)]); }}
          aria-label="Год"
          className="min-w-[12rem] flex-1 accent-violet"
        />

        <div className="flex items-center gap-1 text-xs text-muted-2">
          {frames.map((f) => (
            <button
              key={String(f)}
              type="button"
              onClick={() => { setPlaying(false); setFrame(f); }}
              className={`tabular cursor-pointer rounded-sm px-2 py-1 transition-colors duration-150 ${
                f === frame ? 'bg-violet-deep/60 text-line' : 'hover:text-line'
              }`}
            >
              {f === 'forecast' ? 'прогноз' : f}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={step}
          className="cursor-pointer rounded-sm border border-grid px-3 py-2 text-sm text-muted transition-colors duration-150 hover:border-violet hover:text-line"
        >
          Шаг →
        </button>
      </div>
    </div>
  );
}
