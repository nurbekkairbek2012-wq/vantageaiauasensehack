/* Рабочая поверхность инспектора.
 *
 * Режим здесь другой, чем на лендинге. Лендинг убеждает, карта — работает:
 * задача, состояние, привычные элементы важнее выразительности, а бренд
 * живёт в точности деталей, а не в размахе.
 *
 * Задача пользователя названа прямо: выбрать, куда ехать. Поэтому реестр
 * объектов — не «список фич», а таблица решения: чем крупнее, свежее и
 * дороже объект, тем выше он стоит, и рядом видно, сколько признаков за
 * него高 проголосовало.
 */

import { useEffect, useMemo, useState } from 'react';
import { MapView, type Basemap } from './components/MapView';
import { Nav } from './components/Nav';

type Props = Record<string, unknown>;
type Feature = GeoJSON.Feature<GeoJSON.Geometry, Props>;

const SIGNALS: [string, string, number][] = [
  ['ndvi_drop', 'Падение вегетации', 0.35],
  ['bsi_rise', 'Открытый грунт', 0.25],
  ['pmli_response', 'Отклик полимеров', 0.15],
  ['sar_incoherence', 'Нестабильность (радар)', 3.0],
  ['thermal_anomaly', 'Тепловая аномалия', 3.0],
];

const kzt = (v: unknown) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)} млрд ₸`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)} млн ₸`;
  return `${Math.round(n / 1e3).toLocaleString('ru-RU')} тыс ₸`;
};

const num = (v: unknown, d = 0) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString('ru-RU', { maximumFractionDigits: d }) : '—';
};

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

function humanDate(v: unknown) {
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? '—' : `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

type SortKey = 'evidence_score' | 'damage_p50' | 'area_m2' | 'break_date';

const SORTS: [SortKey, string][] = [
  ['evidence_score', 'по согласию признаков'],
  ['damage_p50', 'по ущербу'],
  ['area_m2', 'по площади'],
  ['break_date', 'по дате возникновения'],
];

export default function MapApp() {
  const [candidates, setCandidates] = useState<GeoJSON.FeatureCollection | null>(null);
  const [registry, setRegistry] = useState<GeoJSON.FeatureCollection | null>(null);
  const [risk, setRisk] = useState<GeoJSON.FeatureCollection | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>('evidence_score');
  const [basemap, setBasemap] = useState<Basemap>('grid');
  // Прогноз выключен по умолчанию. Он покрывает всю область сплошной
  // заливкой и на старте прячет то, ради чего карту открыли, — сами
  // найденные объекты. Задача первична, прогноз включается осознанно.
  const [showRisk, setShowRisk] = useState(false);
  const [showRegistry, setShowRegistry] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const load = (name: string) =>
      fetch(`./data/${name}.geojson`).then((r) => (r.ok ? r.json() : null));
    Promise.all([load('candidates'), load('registry'), load('risk_public')])
      .then(([c, g, r]) => { setCandidates(c); setRegistry(g); setRisk(r); })
      .catch(() => setFailed(true));
  }, []);

  const rows = useMemo(() => {
    const list = [...(candidates?.features ?? [])] as Feature[];
    list.sort((a, b) => {
      if (sort === 'break_date') {
        return String(b.properties.break_date ?? '').localeCompare(String(a.properties.break_date ?? ''));
      }
      return (Number(b.properties[sort]) || 0) - (Number(a.properties[sort]) || 0);
    });
    return list;
  }, [candidates, sort]);

  const current = rows.find((f) => f.properties.candidate_id === selected) ?? null;

  const totals = useMemo(() => {
    const list = candidates?.features ?? [];
    return {
      count: list.length,
      damage: list.reduce((s, f) => s + (Number(f.properties?.damage_p50) || 0), 0),
      area: list.reduce((s, f) => s + (Number(f.properties?.area_m2) || 0), 0),
    };
  }, [candidates]);

  return (
    <div className="flex h-screen flex-col">
      <Nav current="map">
        <dl className="flex items-baseline gap-6 text-sm">
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-2">Объектов</dt>
            <dd className="tabular font-display text-lg text-line">{totals.count}</dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-2">Площадь</dt>
            <dd className="tabular font-display text-lg text-line">{num(totals.area / 10000, 1)} га</dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-2">Ущерб</dt>
            <dd className="tabular font-display text-lg text-violet-lit">{kzt(totals.damage)}</dd>
          </div>
        </dl>
      </Nav>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[22rem_1fr_23rem]">
        {/* ── Реестр: таблица решения, а не список ────────────────────── */}
        <aside className="flex min-h-0 flex-col border-r border-grid">
          <div className="flex shrink-0 items-center gap-2 border-b border-grid px-4 py-2.5">
            <label htmlFor="sort" className="text-xs text-muted-2">Сортировка</label>
            <select
              id="sort"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="flex-1 rounded-sm border border-grid bg-soot-2 px-2 py-1.5 text-sm text-line"
            >
              {SORTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </div>

          <ol className="min-h-0 flex-1 overflow-y-auto">
            {rows.map((f) => {
              const p = f.properties;
              const id = String(p.candidate_id);
              const on = id === selected;
              const score = Number(p.evidence_score) || 0;
              return (
                <li key={id}>
                  <button
                    type="button"
                    onClick={() => setSelected(on ? null : id)}
                    aria-current={on}
                    className={`w-full cursor-pointer border-b border-grid px-4 py-3 text-left transition-colors duration-150 ${
                      on ? 'bg-violet-deep/45' : 'hover:bg-soot-2'
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="tabular font-display text-base text-line">{id}</span>
                      <span className="tabular text-sm text-violet-lit">{Math.round(score * 100)}%</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-baseline gap-x-4 text-xs text-muted-2">
                      <span className="tabular">{num(p.area_m2)} м²</span>
                      <span className="tabular">{kzt(p.damage_p50)}</span>
                      <span>{humanDate(p.break_date)}</span>
                    </div>
                    <div className="mt-2 h-[3px] w-full bg-grid">
                      <div className="h-full bg-violet-lit" style={{ width: `${Math.min(100, score * 100)}%` }} />
                    </div>
                  </button>
                </li>
              );
            })}
            {!rows.length && (
              <li className="px-4 py-8 text-sm text-muted-2">
                {failed ? 'Данные не загрузились.' : 'Загрузка…'}
              </li>
            )}
          </ol>
        </aside>

        {/* ── Карта ───────────────────────────────────────────────────── */}
        <div className="relative min-h-[22rem]">
          <MapView
            candidates={candidates}
            registry={registry}
            risk={risk}
            selected={selected}
            onSelect={setSelected}
            basemap={basemap}
            showRisk={showRisk}
            showRegistry={showRegistry}
          />

          <div className="pointer-events-none absolute left-3 top-3 z-[500] flex flex-col gap-2">
            <div className="pointer-events-auto flex overflow-hidden rounded-sm border border-grid bg-soot/90">
              {([['grid', 'Сетка'], ['sat', 'Снимок'], ['scheme', 'Схема']] as [Basemap, string][]).map(
                ([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setBasemap(key)}
                    className={`cursor-pointer px-3 py-1.5 text-xs transition-colors duration-150 ${
                      basemap === key ? 'bg-violet text-paper' : 'text-muted hover:bg-soot-2'
                    }`}
                  >
                    {label}
                  </button>
                ),
              )}
            </div>
            <div className="pointer-events-auto flex flex-col gap-1.5 rounded-sm border border-grid bg-soot/90 px-3 py-2 text-xs">
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" checked={showRisk} onChange={(e) => setShowRisk(e.target.checked)} />
                <span className="text-muted">Зоны риска</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" checked={showRegistry} onChange={(e) => setShowRegistry(e.target.checked)} />
                <span className="text-muted">Известные объекты</span>
              </label>
            </div>
            {basemap === 'grid' && (
              <p className="pointer-events-none max-w-[15rem] text-xs leading-snug text-muted-2">
                Подложка выключена — карта работает без интернета. Снимок
                включается кнопкой, когда сеть есть.
              </p>
            )}
          </div>
        </div>

        {/* ── Карточка объекта ────────────────────────────────────────── */}
        <aside className="min-h-0 overflow-y-auto border-l border-grid">
          {!current ? (
            <div className="px-5 py-8">
              <h2 className="text-xl text-line">Выберите объект</h2>
              <p className="mt-3 max-w-[34ch] text-sm text-muted">
                В реестре слева {totals.count} объектов, отобранных из 429
                найденных. По каждому известны дата возникновения, площадь,
                оценка ущерба и то, какие признаки за него сработали.
              </p>
            </div>
          ) : (
            <ObjectCard f={current} />
          )}
        </aside>
      </div>
    </div>
  );
}

function ObjectCard({ f }: { f: Feature }) {
  const p = f.properties;
  const score = Number(p.evidence_score) || 0;

  return (
    <article className="px-5 py-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="tabular font-display text-2xl text-line">{String(p.candidate_id)}</h2>
        <span className="tabular text-sm text-violet-lit">
          согласие {Math.round(score * 100)}%
        </span>
      </div>

      {/* Уверенности модели нет, и на её месте не должно быть числа,
          похожего на вероятность. Названо тем, чем является. */}
      <p className="mt-2 text-xs leading-snug text-muted-2">
        {p.probability == null
          ? 'Оценка по согласию физических признаков, не моделью: сеть не обучена — положительных примеров из открытых данных не набирается.'
          : 'Вероятность обученной модели.'}
      </p>

      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 border-y border-grid py-4 text-sm">
        {[
          ['Возник', humanDate(p.break_date)],
          ['Площадь', `${num(p.area_m2)} м²`],
          ['Масса', `${num(p.mass_t)} т`],
          ['Метан за 20 лет', `${num(p.co2e_t)} т CO₂-экв.`],
        ].map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-muted-2">{k}</dt>
            <dd className="tabular mt-0.5 text-line">{v}</dd>
          </div>
        ))}
      </dl>

      <h3 className="mt-5 text-sm text-muted-2">Признаки</h3>
      <ul className="mt-2 space-y-2.5">
        {SIGNALS.map(([key, label, full]) => {
          const raw = Number(p[key]);
          const has = Number.isFinite(raw);
          const pct = has ? Math.max(0, Math.min(1, raw / full)) * 100 : 0;
          return (
            <li key={key}>
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span className={has ? 'text-line' : 'text-muted-2'}>{label}</span>
                <span className={`tabular ${has ? 'text-muted' : 'text-muted-2'}`}>
                  {has ? `${Math.round(pct)}%` : 'нет данных'}
                </span>
              </div>
              <div className="mt-1 h-[3px] w-full bg-grid">
                <div
                  className={has ? 'h-full bg-violet-lit' : 'h-full'}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>

      <h3 className="mt-6 text-sm text-muted-2">Ущерб</h3>
      <p className="tabular mt-1 font-display text-3xl text-line">{kzt(p.damage_p50)}</p>
      <p className="tabular mt-1 text-xs text-muted-2">
        P10 {kzt(p.damage_p10)} · P90 {kzt(p.damage_p90)}
      </p>

      <h3 className="mt-6 text-sm text-muted-2">Норма</h3>
      <p className="mt-1 text-sm text-line">{String(p.penalty_article ?? 'ст. 344, ч. 2-1 КоАП РК')}</p>
      <p className="tabular mt-0.5 text-sm text-muted">{kzt(p.penalty_kzt)}</p>

      <button
        type="button"
        onClick={() => window.print()}
        className="mt-7 w-full cursor-pointer rounded-sm bg-violet px-4 py-3 font-display text-sm font-semibold uppercase tracking-[0.12em] text-paper transition-colors duration-200 hover:bg-violet-lit hover:text-soot"
      >
        Черновик акта
      </button>
      <p className="mt-2 text-xs leading-snug text-muted-2">
        Документ выходит черновиком и становится актом только после
        подтверждения именем и должностью.
      </p>
    </article>
  );
}
