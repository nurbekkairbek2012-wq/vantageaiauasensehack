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

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MapView } from './components/MapView';
import { BasemapSwitch } from './components/BasemapSwitch';
import type { Basemap } from './components/basemaps';
import { Nav } from './components/Nav';
import { Act } from './components/Act';

type Props = Record<string, unknown>;
type Feature = GeoJSON.Feature<GeoJSON.Geometry, Props>;

/* Контроль устранения. Три статуса, и третий — главный.
 *
 * «Возможно засыпан» означает: растительность вернулась, открытый грунт
 * нормализовался, объект формально выглядит убранным — а тепловая аномалия
 * держится, то есть органика под насыпью продолжает разлагаться. По такому
 * объекту может быть закрыт акт и оплачена работа, при том что отходы на
 * месте. Поэтому он и красный, и в списке выезда стоит первым.
 */
const REMOVAL: Record<string, { label: string; tone: string; dot: string }> = {
  active: { label: 'активна', tone: 'text-muted', dot: '#a78bfa' },
  possibly_removed: { label: 'вероятно устранена', tone: 'text-muted-2', dot: '#4c1d95' },
  possibly_covered: { label: 'возможно засыпана', tone: 'text-line', dot: '#ede9fe' },
  insufficient_data: { label: 'данных мало', tone: 'text-muted-2', dot: '#2f2450' },
};

const SIGNALS: [string, string, number][] = [
  ['ndvi_drop', 'Падение вегетации', 0.35],
  ['bsi_rise', 'Открытый грунт', 0.25],
  ['pmli_response', 'Отклик полимеров', 0.15],
  ['sar_incoherence', 'Нестабильность (радар)', 3.0],
  ['thermal_anomaly', 'Тепловая аномалия', 3.0],
];

/** Центр объекта по его геометрии: в акт идут координаты, а не «где-то там». */
function centerOf(f: Feature): [number, number] | null {
  const coords: number[][] =
    f.geometry.type === 'Polygon'
      ? (f.geometry.coordinates[0] as number[][])
      : f.geometry.type === 'MultiPolygon'
        ? (f.geometry.coordinates[0][0] as number[][])
        : [];
  if (!coords.length) return null;
  let minLon = 180, minLat = 90, maxLon = -180, maxLat = -90;
  for (const [lon, lat] of coords) {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  return [(minLat + maxLat) / 2, (minLon + maxLon) / 2];
}

const removalOf = (p: Props) => {
  const status = typeof p.removal_status === 'string' ? p.removal_status : '';
  return REMOVAL[status] ?? null;
};

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

type SortKey = 'evidence_score' | 'damage_p50' | 'area_m2' | 'break_date' | 'risk_of_cover';

const SORTS: [SortKey, string][] = [
  ['risk_of_cover', 'сначала подозрение на присыпку'],
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
  const [sort, setSort] = useState<SortKey>('risk_of_cover');
  const [basemap, setBasemap] = useState<Basemap>('sat');
  // Прогноз выключен по умолчанию. Он покрывает всю область сплошной
  // заливкой и на старте прячет то, ради чего карту открыли, — сами
  // найденные объекты. Задача первична, прогноз включается осознанно.
  const [showRisk, setShowRisk] = useState(false);
  const [showRegistry, setShowRegistry] = useState(true);
  const [failed, setFailed] = useState(false);

  /* Список и карта — две проекции одного набора, и выбор в одной обязан
     быть виден в другой. Клик по объекту на карте раньше подсвечивал
     строку, до которой надо было доскроллить руками: в списке из тридцати
     объектов подсветка вне экрана — это отсутствие ответа. */
  const listRefs = useRef<Map<string, HTMLLIElement>>(new Map());

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
      if (sort === 'risk_of_cover') {
        // Подозрение на присыпку выше всего: по такому объекту может быть
        // закрыт акт, а отходы на месте. Внутри группы — по ущербу.
        const weight = (f: Feature) =>
          f.properties.removal_status === 'possibly_covered' ? 2
          : f.properties.removal_status === 'active' ? 1 : 0;
        const diff = weight(b) - weight(a);
        return diff || (Number(b.properties.damage_p50) || 0) - (Number(a.properties.damage_p50) || 0);
      }
      if (sort === 'break_date') {
        return String(b.properties.break_date ?? '').localeCompare(String(a.properties.break_date ?? ''));
      }
      return (Number(b.properties[sort]) || 0) - (Number(a.properties[sort]) || 0);
    });
    return list;
  }, [candidates, sort]);

  const current = rows.find((f) => f.properties.candidate_id === selected) ?? null;

  useEffect(() => {
    if (!selected) return;
    const row = listRefs.current.get(selected);
    // block: 'nearest' — строка уже на экране не дёргается. Прокрутка
    // ради прокрутки читается как самопроизвольное движение страницы.
    row?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [selected]);

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
        <dl className="flex min-w-0 flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
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

          {/* Проценты в строке ничего не значили без подписи: их путали с
              вероятностью модели, которой здесь нет. Заголовок столбца
              стоит дешевле, чем объяснение на защите. */}
          <div className="flex shrink-0 items-baseline justify-between gap-3 border-b border-grid px-4 py-1.5 text-[11px] uppercase tracking-[0.1em] text-muted-2">
            <span>Объект</span>
            <span title="Сколько из пяти физических признаков сработало за объект">
              Согласие признаков
            </span>
          </div>

          <ol className="min-h-0 flex-1 overflow-y-auto">
            {rows.map((f) => {
              const p = f.properties;
              const id = String(p.candidate_id);
              const on = id === selected;
              const score = Number(p.evidence_score) || 0;
              return (
                <li
                  key={id}
                  ref={(node) => {
                    if (node) listRefs.current.set(id, node);
                    else listRefs.current.delete(id);
                  }}
                >
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
                      {removalOf(p) && (
                        <span className="flex items-center gap-1.5">
                          <span
                            aria-hidden="true"
                            className="inline-block h-2 w-2 rounded-full"
                            style={{ background: removalOf(p)!.dot }}
                          />
                          {removalOf(p)!.label}
                        </span>
                      )}
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
            <BasemapSwitch value={basemap} onChange={setBasemap} className="pointer-events-auto" />
            <div className="pointer-events-auto flex flex-col gap-1.5 rounded-sm border border-grid bg-soot/90 px-3 py-2 text-xs backdrop-blur-sm">
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" checked={showRisk} onChange={(e) => setShowRisk(e.target.checked)} />
                <span className="text-muted">Зоны риска</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" checked={showRegistry} onChange={(e) => setShowRegistry(e.target.checked)} />
                <span className="text-muted">Известные объекты</span>
              </label>
            </div>
            {showRisk && (
              <p className="pointer-events-none max-w-[15rem] text-xs leading-snug text-muted-2">
                Тепло — вероятность появления новой свалки, а не найденный
                объект. Границ у зоны нет: риск не обрывается на меже.
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

      {removalOf(p) && (
        <>
          <h3 className="mt-6 text-sm text-muted-2">Контроль устранения</h3>
          <p className="mt-1 flex items-center gap-2 text-line">
            <span
              aria-hidden="true"
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ background: removalOf(p)!.dot }}
            />
            {removalOf(p)!.label}
          </p>
          {typeof p.removal_note === 'string' && p.removal_note && (
            <p className="mt-1.5 text-xs leading-snug text-muted-2">{p.removal_note}</p>
          )}
        </>
      )}

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

      {/* Бланк выносится порталом прямо в body, а не остаётся в дереве.
          Правило печати прячет всех прямых детей body кроме него; вложенный
          в разметку карты, он прятался бы вместе с родителем — печать
          выходила пустой страницей, и это показал первый же тестовый PDF. */}
      {createPortal(
        <div id="act-root">
          <Act p={p} center={centerOf(f)} />
        </div>,
        document.body,
      )}
      <p className="mt-2 text-xs leading-snug text-muted-2">
        Документ выходит черновиком и становится актом только после
        подтверждения именем и должностью.
      </p>
    </article>
  );
}
