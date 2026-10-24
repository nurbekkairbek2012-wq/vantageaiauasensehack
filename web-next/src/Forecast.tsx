/* Прогноз: где свалка появится в следующие двенадцать месяцев.
 *
 * Ответ модели — это не карта вероятностей. Вероятность по девятнадцати
 * тысячам ячеек ничего не говорит человеку, который решает, куда послать
 * машину в понедельник. Ответ — короткий пронумерованный список мест, и
 * он здесь главный, а карта его иллюстрирует.
 *
 * Точная вероятность на эту поверхность не выгружается вообще: по
 * градиенту уверенности восстанавливается вся модель. Остаётся порядок
 * объезда — его для работы достаточно.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Nav } from './components/Nav';

type Metrics = {
  pr_auc_future: number;
  base_rate_future: number;
  lift: number;
  cutoff: string;
  importances: Record<string, number>;
};

const FEATURE_NAMES: Record<string, string> = {
  dist_settlement_m: 'Удалённость от жилья',
  dist_road_m: 'Расстояние до проезжей дороги',
  existing_density_3km: 'Плотность свалок в радиусе 3 км',
  screening_score: 'Укрытость: подъезд близко, жильё далеко',
  dist_nearest_existing_m: 'Расстояние до ближайшей свалки',
  existing_density_10km: 'Плотность свалок в радиусе 10 км',
  dist_legal_site_m: 'Расстояние до легального полигона',
};

const num = (v: number, d = 0) =>
  Number.isFinite(v) ? v.toLocaleString('ru-RU', { maximumFractionDigits: d }) : '—';

export default function Forecast() {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const cells = useRef<Map<number, L.Path>>(new Map());

  const [patrol, setPatrol] = useState<GeoJSON.FeatureCollection | null>(null);
  const [risk, setRisk] = useState<GeoJSON.FeatureCollection | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    const grab = <T,>(url: string, set: (v: T) => void) =>
      fetch(url).then((r) => (r.ok ? r.json() : null)).then(set).catch(() => set(null as T));
    grab('./data/patrol.geojson', setPatrol);
    grab('./data/risk_public.geojson', setRisk);
    grab('./data/metrics.json', setMetrics);
  }, []);

  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, {
      zoomControl: false, attributionControl: false, preferCanvas: true,
      minZoom: 9, maxZoom: 16, zoomSnap: 0.5,
    }).setView([51.21, 71.5], 11);
    L.control.zoom({ position: 'bottomright' }).addTo(m);
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);
    map.current = m;
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !risk) return;
    L.geoJSON(risk, {
      style: (f) => {
        const cls = Number(f?.properties?.risk_class) || 1;
        return { color: '#7c3aed', weight: 0, fillColor: '#7c3aed', fillOpacity: 0.05 + cls * 0.04 };
      },
    }).addTo(m);
  }, [risk]);

  useEffect(() => {
    const m = map.current;
    if (!m || !patrol) return;
    const layer = L.geoJSON(patrol, {
      style: () => ({ color: '#ede9fe', weight: 1.5, fillColor: '#a78bfa', fillOpacity: 0.25 }),
      onEachFeature: (f, l) => {
        const rank = Number(f.properties?.rank);
        cells.current.set(rank, l as L.Path);
        l.bindTooltip(`№${rank}`, { direction: 'top' });
        l.on('click', () => setSelected(rank));
      },
    }).addTo(m);
    m.fitBounds(layer.getBounds().pad(0.3), { animate: false });
  }, [patrol]);

  useEffect(() => {
    cells.current.forEach((cell, rank) => {
      cell.setStyle(
        rank === selected
          ? { color: '#ede9fe', weight: 3, fillColor: '#ede9fe', fillOpacity: 0.5 }
          : { color: '#ede9fe', weight: 1.5, fillColor: '#a78bfa', fillOpacity: 0.25 },
      );
    });
    const m = map.current;
    if (m && selected != null && cells.current.has(selected)) {
      m.flyTo((cells.current.get(selected) as L.Polygon).getBounds().getCenter(), 14, { duration: 0.6 });
    }
  }, [selected]);

  const rows = useMemo(() => {
    const list = [...(patrol?.features ?? [])];
    list.sort((a, b) => Number(a.properties?.rank) - Number(b.properties?.rank));
    return list;
  }, [patrol]);

  const importances = useMemo(() => {
    if (!metrics?.importances) return [];
    return Object.entries(metrics.importances)
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1]);
  }, [metrics]);

  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="forecast">
        <p className="max-w-[46ch] text-sm text-muted-2">
          Убрать свалку стоит миллионы. Не дать ей появиться — стоит знака и
          фотоловушки. Ниже координаты, куда их ставить.
        </p>
      </Nav>

      {/* Высота задана, а не растянута по содержимому: двадцать строк
          реестра растягивали карту на полторы тысячи пикселей, и она
          уезжала из поля зрения вместе с ответом, ради которого страница
          и существует. Реестр прокручивается внутри себя. */}
      <div className="grid h-[min(72vh,46rem)] grid-cols-1 lg:grid-cols-[26rem_1fr]">
        <aside className="flex min-h-0 flex-col border-r border-grid">
          <div className="border-b border-grid px-5 py-4">
            <h1 className="text-xl text-line">Маршрут на ближайший месяц</h1>
            <p className="mt-2 text-sm text-muted">
              Двадцать ячеек по 500 м, отобранных моделью из{' '}
              <span className="tabular text-line">19 621</span>. Порядок —
              порядок объезда.
            </p>
          </div>

          <ol className="min-h-0 flex-1 overflow-y-auto">
            {rows.map((f) => {
              const p = f.properties ?? {};
              const rank = Number(p.rank);
              const on = rank === selected;
              return (
                <li key={rank}>
                  <button
                    type="button"
                    onClick={() => setSelected(on ? null : rank)}
                    aria-current={on}
                    className={`flex w-full cursor-pointer items-baseline gap-4 border-b border-grid px-5 py-3 text-left transition-colors duration-150 ${
                      on ? 'bg-violet-deep/45' : 'hover:bg-soot-2'
                    }`}
                  >
                    <span className="tabular font-display text-lg text-violet-lit">
                      {String(rank).padStart(2, '0')}
                    </span>
                    <span className="flex-1 text-xs leading-relaxed text-muted-2">
                      до дороги <span className="tabular text-line">{num(Number(p.dist_road_m))} м</span>,
                      до жилья <span className="tabular text-line">{num(Number(p.dist_settlement_m) / 1000, 1)} км</span>
                      {Number(p.density_3km) > 0 && (
                        <>
                          , рядом уже <span className="tabular text-line">{num(Number(p.density_3km), 2)}</span> объектов на 3 км
                        </>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
            {!rows.length && (
              <li className="px-5 py-8 text-sm text-muted-2">
                Маршрут не загрузился. Он строится командой{' '}
                <code className="text-muted">python scripts/make_patrol.py</code>.
              </li>
            )}
          </ol>
        </aside>

        <div className="relative min-h-[20rem]">
          <div className="absolute inset-0 bg-soot map-grid" />
          <div ref={host} className="absolute inset-0" />
          <p className="pointer-events-none absolute bottom-4 left-4 z-[500] max-w-[20rem] text-xs leading-snug text-muted-2">
            Заливка — зоны повышенного риска. Светлые квадраты — отобранные
            места. Точная вероятность по ячейкам не публикуется: по ней
            восстанавливается модель.
          </p>
        </div>
      </div>

      {/* ── Чем это проверено ─────────────────────────────────────────── */}
      <section className="border-t border-grid px-5 py-8">
        <div className="mx-auto max-w-[1240px]">
          <h2 className="text-[clamp(1.5rem,3vw,2.2rem)] text-line">
            Проверено на будущем, которого модель не видела
          </h2>

          {metrics ? (
            <>
              <p className="mt-4 max-w-[70ch] text-muted">
                Обучение шло на объектах, возникших до{' '}
                <span className="tabular text-line">{metrics.cutoff}</span>, проверка — на
                возникших после. Ячейки, где свалка была уже до отсечки, из
                проверки исключены: предсказывать появление там, где уже есть,
                бессмысленно, и они завысили бы результат.
              </p>

              <dl className="mt-7 flex flex-wrap gap-x-12 gap-y-5">
                {[
                  ['PR-AUC на будущем', metrics.pr_auc_future.toFixed(3)],
                  ['Базовая частота', metrics.base_rate_future.toFixed(4)],
                  ['Выигрыш над случайным', `×${Math.round(metrics.lift)}`],
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-sm text-muted-2">{k}</dt>
                    <dd className="tabular mt-0.5 font-display text-3xl text-line">{v}</dd>
                  </div>
                ))}
              </dl>

              <p className="mt-6 max-w-[70ch] text-muted-2">
                Прямое чтение: если объехать сто ячеек, отобранных моделью,
                вместо ста случайных, свалок найдётся в сотни раз больше на тот
                же бензин.
              </p>

              <h3 className="mt-10 text-lg text-line">Что решает</h3>
              <ul className="mt-4 max-w-3xl">
                {importances.map(([key, value]) => (
                  <li key={key} className="flex items-baseline gap-4 border-b border-grid py-3">
                    <span className="flex-1 text-sm text-muted">{FEATURE_NAMES[key] ?? key}</span>
                    <span className="h-[3px] w-40 bg-grid">
                      <span className="block h-full bg-violet-lit" style={{ width: `${value * 100}%` }} />
                    </span>
                    <span className="tabular w-12 text-right text-sm text-muted-2">
                      {Math.round(value * 100)}%
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-4 max-w-[70ch] text-sm text-muted-2">
                Расстояние до легального полигона не решает ничего — его вес
                нулевой. Это ответ на распространённое возражение «свалки
                возникают там, где далеко везти»: по данным области это не так,
                решают удалённость от жилья и наличие подъезда.
              </p>
            </>
          ) : (
            <p className="mt-4 max-w-[70ch] text-muted">
              Метрики не загрузились. Модель обучается в прогоне; если файла
              нет, значит прогон до неё не дошёл — и показывать здесь нечего.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
