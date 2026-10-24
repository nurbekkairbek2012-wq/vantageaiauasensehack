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
import { CitySwitch, type City } from './components/CitySwitch';
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

/* Ниже этого значения модель фактически возражает детектору. Показать
   голый ноль рядом с объектом, который система вывела на карту как свалку,
   значит поставить два противоречащих утверждения без объяснения. Само
   разногласие — полезная величина: детектор нашёл необратимое изменение,
   а модель говорит, что такое изменение бывает законным. Это повод
   посмотреть глазами, а не повод спрятать число. */
const DISAGREEMENT = 0.2;

/* Проверка глазами по снимку 0,6 м на пиксель.
 *
 * Это сильнее любой модели, и не из-за качества модели. Модель обучена на
 * слабой разметке и уверенно ошибается: складу под синей кровлей она
 * ставит 0,97. Человек, посмотревший на тот же склад, ошибиться не может.
 *
 * Тридцать объектов проверяются за час, и на защите это разница между
 * «модель считает» и «мы посмотрели каждый». Поэтому вердикт стоит в
 * строке списка первым, а вероятность модели — после него.
 */
const VISUAL: Record<string, { short: string; full: string; tone: string; dot: string }> = {
  landfill: {
    short: 'свалка',
    full: 'Проверено по снимку на снимке 0,6 м/пиксель: это свалка.',
    tone: 'text-line',
    dot: '#a78bfa',
  },
  not_landfill: {
    short: 'не свалка',
    full: 'Проверено по снимку: это не свалка — постройка, промплощадка, стройка или водоём. Детектор нашёл здесь необратимое исчезновение растительности, и это правда: именно так выглядит любая застройка. Отсечь такое должен был контекстный фильтр по OpenStreetMap, но новая застройка вокруг Астаны в него не нанесена.',
    tone: 'text-amber',
    dot: '#e3b341',
  },
  unclear: {
    short: 'не разобрать',
    full: 'Снимка недостаточно: похоже на нарушенный грунт, но отличить свалку от отвала или заброшенной площадки по одному снимку нельзя. Нужен выезд.',
    tone: 'text-muted',
    dot: '#8578ad',
  },
};

const visualOf = (p: Props) => {
  const code = typeof p.visual_check === 'string' ? p.visual_check : '';
  return VISUAL[code] ?? null;
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
  // Запятая, а не точка: по-русски дробная часть отделяется запятой, и
  // «37.9 млн» рядом с «1,5 га» на той же полосе читается как опечатка.
  const one = (x: number) => x.toFixed(1).replace('.', ',');
  if (Math.abs(n) >= 1e9) return `${one(n / 1e9)} млрд ₸`;
  if (Math.abs(n) >= 1e6) return `${one(n / 1e6)} млн ₸`;
  return `${Math.round(n / 1e3).toLocaleString('ru-RU')} тыс ₸`;
};

/** Согласовать существительное с числом. «21 объектов» замечают раньше,
 *  чем содержание фразы, а числа теперь приходят из данных и подогнать
 *  формулировку под одно из них нельзя. */
const plural = (count: number, one: string, few: string, many: string) => {
  const mod100 = Math.abs(count) % 100;
  if (mod100 >= 11 && mod100 <= 14) return many;
  const mod10 = mod100 % 10;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
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

type SortKey = 'visual' | 'probability' | 'evidence_score' | 'damage_p50' | 'area_m2' | 'break_date' | 'risk_of_cover';

const SORTS: [SortKey, string][] = [
  ['visual', 'сначала опознанные как свалка'],
  ['risk_of_cover', 'сначала подозрение на присыпку'],
  ['probability', 'по вероятности модели'],
  ['evidence_score', 'по согласию признаков'],
  ['damage_p50', 'по ущербу'],
  ['area_m2', 'по площади'],
  ['break_date', 'по дате возникновения'],
];

export default function MapApp() {
  const [candidates, setCandidates] = useState<GeoJSON.FeatureCollection | null>(null);
  const [registry, setRegistry] = useState<GeoJSON.FeatureCollection | null>(null);
  const [risk, setRisk] = useState<GeoJSON.FeatureCollection | null>(null);
  /* Сколько кандидатов рассмотрел детектор — из воронки, а не числом в
     коде. Вписанное «429» пережило пересчёт и стало неправдой: сырых
     кандидатов теперь 385. Соседняя цифра в той же фразе при этом
     считалась из данных, так что предложение противоречило само себе. */
  const [raw, setRaw] = useState<number | null>(null);
  useEffect(() => {
    fetch('./data/funnel.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setRaw(typeof d?.raw === 'number' ? d.raw : null))
      .catch(() => setRaw(null));
  }, []);

  const [selected, setSelected] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>('visual');
  const [basemap, setBasemap] = useState<Basemap>('sat');
  // Прогноз выключен по умолчанию. Он покрывает всю область сплошной
  // заливкой и на старте прячет то, ради чего карту открыли, — сами
  // найденные объекты. Задача первична, прогноз включается осознанно.
  const [showRisk, setShowRisk] = useState(false);
  const [showRegistry, setShowRegistry] = useState(true);
  const [failed, setFailed] = useState(false);
  const [cities, setCities] = useState<City[]>([]);
  const [city, setCity] = useState<string | null>(null);
  const [flyTo, setFlyTo] = useState<{ center: [number, number]; zoom: number; key: string } | null>(null);

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

    fetch('./data/cities.json')
      .then((r) => (r.ok ? r.json() : []))
      .then((list: City[]) => {
        setCities(list);
        // Открываемся на городе, где что-то найдено, а не на первом в
        // списке: пустая карта на старте выглядит как поломка.
        const ready = list.find((c) => c.count > 0);
        if (ready) setCity(ready.id);
      })
      .catch(() => setCities([]));
  }, []);

  const rows = useMemo(() => {
    const list = [...(candidates?.features ?? [])] as Feature[];
    list.sort((a, b) => {
      if (sort === 'visual') {
        // Подтверждённые вверх, отвергнутые вниз: список читается сверху,
        // и первым должно стоять то, по чему можно ехать.
        const weight = (f: Feature) =>
          f.properties.visual_check === 'landfill' ? 3
          : f.properties.visual_check === 'unclear' ? 2
          : f.properties.visual_check === 'not_landfill' ? 0 : 1;
        const diff = weight(b) - weight(a);
        return diff || (Number(b.properties.area_m2) || 0) - (Number(a.properties.area_m2) || 0);
      }
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

  /* Считаем отдельно найденное и подтверждённое. Показывать одно число
     «30 объектов» больше нельзя: проверка глазами отвергла восемнадцать
     из них, и цифра в шапке стала бы обещанием, которое карта тут же
     опровергает — достаточно открыть первый попавшийся объект. */
  const totals = useMemo(() => {
    const list = candidates?.features ?? [];
    const real = list.filter((f) => f.properties?.visual_check !== 'not_landfill');
    return {
      count: list.length,
      confirmed: list.filter((f) => f.properties?.visual_check === 'landfill').length,
      damage: real.reduce((s, f) => s + (Number(f.properties?.damage_p50) || 0), 0),
      area: real.reduce((s, f) => s + (Number(f.properties?.area_m2) || 0), 0),
    };
  }, [candidates]);

  return (
    /* h-screen только на широком экране. На телефоне три колонки
       складываются в одну, и жёсткая высота окна делила её между списком,
       картой и карточкой — списку доставалось полторы строки. Там страница
       должна прокручиваться, а не втискиваться. */
    <div className="flex min-h-screen flex-col lg:h-screen">
      <Nav current="map">
        <dl className="flex min-w-0 flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-2">Найдено</dt>
            <dd className="tabular font-display text-lg text-line">{totals.count}</dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-2">Опознано как свалка</dt>
            <dd className="tabular font-display text-lg text-violet-lit">{totals.confirmed}</dd>
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
        <aside className="flex max-h-[60vh] min-h-0 flex-col border-r border-grid lg:max-h-none">
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
            <span title="Проверка глазами по снимку 0,6 м на пиксель">
              Проверено по снимку
            </span>
          </div>

          <ol className="min-h-0 flex-1 overflow-y-auto">
            {rows.map((f) => {
              const p = f.properties;
              const id = String(p.candidate_id);
              const on = id === selected;
              const score = Number(p.evidence_score) || 0;
              /* В строке стоит вероятность модели, а не согласие признаков.
                 Раньше стояло согласие, и это была подмена: среднее по пяти
                 физическим величинам — не уверенность, а сводка измерений.
                 Читалось оно как уверенность и давало «34%» там, где модель
                 говорит 0,97. Согласие никуда не делось — оно рядом, в виде
                 «признаков n из 5», где его невозможно спутать с долей. */
              const model = Number(p.probability);
              const hasModel = Number.isFinite(model);
              const agreeing = Number(p.n_agreeing) || 0;
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
                      {visualOf(p) ? (
                        <span className={`flex items-center gap-1.5 text-sm ${visualOf(p)!.tone}`}>
                          <span
                            aria-hidden="true"
                            className="inline-block h-2 w-2 rounded-full"
                            style={{ background: visualOf(p)!.dot }}
                          />
                          {visualOf(p)!.short}
                        </span>
                      ) : (
                        <span
                          className={`tabular text-sm ${hasModel ? 'text-violet-lit' : 'text-muted-2'}`}
                          title="вероятность модели"
                        >
                          {hasModel ? `${Math.round(model * 100)}%` : '—'}
                        </span>
                      )}
                    </div>
                    {/* Два ряда с постоянным составом, а не один плывущий.
                        При переносе по месту «признаков 4 из 5» уезжало на
                        первую строку у одних объектов и на вторую у других,
                        и колонка переставала читаться сверху вниз. */}
                    <div className="mt-1 flex flex-wrap items-baseline gap-x-4 text-xs text-muted-2">
                      <span className="tabular">{num(p.area_m2)} м²</span>
                      <span className="tabular">{kzt(p.damage_p50)}</span>
                      <span>{humanDate(p.break_date)}</span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-baseline gap-x-4 text-xs text-muted-2">
                      <span className="tabular">признаков {agreeing} из 5</span>
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
                      {hasModel && model < DISAGREEMENT && (
                        <span className="text-amber" title="модель считает изменение законным — карьером или стройкой">
                          модель не согласна
                        </span>
                      )}
                    </div>
                    <div className="mt-2 h-[3px] w-full bg-grid">
                      <div
                        className={`h-full ${hasModel ? 'bg-violet-lit' : 'bg-violet-deep'}`}
                        style={{ width: `${Math.min(100, (hasModel ? model : score) * 100)}%` }}
                      />
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
        <div className="relative h-[65vh] min-h-[22rem] lg:h-auto">
          <MapView
            candidates={candidates}
            registry={registry}
            risk={risk}
            selected={selected}
            onSelect={setSelected}
            basemap={basemap}
            showRisk={showRisk}
            showRegistry={showRegistry}
            flyTo={flyTo}
          />

          <div className="pointer-events-none absolute left-3 top-3 z-[500] flex flex-col gap-2">
            <BasemapSwitch value={basemap} onChange={setBasemap} className="pointer-events-auto" />
            <CitySwitch
              cities={cities}
              current={city}
              onSelect={(next) => {
                setCity(next.id);
                setSelected(null);
                setFlyTo({ center: next.center, zoom: next.zoom, key: `${next.id}:${Date.now()}` });
              }}
              className="pointer-events-auto"
            />
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
              /* Подложка обязательна. Раньше подсказка лежала прямо на
                 сетке и читалась; на снимке серый текст поверх пёстрого
                 поля не читается вовсе. */
              <p className="pointer-events-none max-w-[15rem] rounded-sm border border-grid bg-soot/90 px-2.5 py-1.5 text-xs leading-snug text-muted backdrop-blur-sm">
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
                В реестре слева {totals.count}{' '}
                {plural(totals.count, 'объект', 'объекта', 'объектов')}
                {raw ? `, отобранных из ${raw}` : ''}.
                Каждый просмотрен глазами на снимке 0,6 м на пиксель:{' '}
                <span className="text-line">{totals.confirmed}</span> подтверждены как
                свалки, остальные оказались постройками, промплощадками и
                нарушенным грунтом либо требуют выезда.
              </p>
              <p className="mt-3 max-w-[34ch] text-sm leading-snug text-muted-2">
                Отвергнутые оставлены на карте намеренно. Ошибка, которую
                видно и объяснили, честнее списка, из которого её вычистили.
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

/** Координаты в том виде, в каком их вбивают в навигатор. */
function Coordinates({ center }: { center: [number, number] | null }) {
  const [copied, setCopied] = useState(false);
  if (!center) return null;

  const text = `${center[0].toFixed(5)}, ${center[1].toFixed(5)}`;

  return (
    <div className="mt-3 flex items-baseline justify-between gap-3 rounded-sm border border-grid bg-soot-2 px-3 py-2.5">
      <div className="min-w-0">
        <p className="text-xs text-muted-2">Координаты</p>
        {/* Пять знаков после запятой — это около метра. Больше писать
            бессмысленно: контур объекта построен по пикселям в десять
            метров, и точность подписи не может быть выше точности данных. */}
        <p className="tabular mt-0.5 select-all text-sm text-line">{text}</p>
      </div>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(text).then(
            () => { setCopied(true); setTimeout(() => setCopied(false), 1600); },
            () => setCopied(false),
          );
        }}
        className={`shrink-0 cursor-pointer rounded-sm border px-2.5 py-1 text-xs transition-colors duration-150 ${
          copied ? 'border-violet-lit text-violet-lit' : 'border-grid text-muted hover:border-violet hover:text-line'
        }`}
      >
        {copied ? 'Скопировано' : 'Копировать'}
      </button>
    </div>
  );
}

function ObjectCard({ f }: { f: Feature }) {
  const p = f.properties;
  const score = Number(p.evidence_score) || 0;
  const model = Number(p.probability);
  const hasModel = Number.isFinite(model);

  return (
    <article className="px-5 py-5">
      {/* В шапке одно число, и то же, что в списке. Раньше здесь стояло
          согласие признаков, а строка списка показывала вероятность модели:
          у одного объекта получалось «26%» вверху карточки и «100%» в
          списке. Два числа рядом, оба похожие на уверенность, — это не
          полнота, это противоречие. Согласие живёт ниже, среди признаков,
          откуда оно и берётся. */}
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="tabular font-display text-2xl text-line">{String(p.candidate_id)}</h2>
        <span className={`tabular text-sm ${hasModel ? 'text-violet-lit' : 'text-muted-2'}`}>
          {hasModel ? `${Math.round(model * 100)}%` : 'модель молчит'}
        </span>
      </div>

      {/* Две разные величины, и путать их нельзя. Согласие признаков —
          сколько физических измерений сошлись, оно есть у каждого объекта.
          Вероятность — ответ сети, и он есть не у всех: сеть училась на
          слабой разметке, и объекты вне этой разметки она не видела.

          Вневыборочная: каждый объект оценён моделью, которая его на
          обучении не встречала. Первая версия показывала медиану 0,999 —
          модель просто помнила свои же обучающие примеры. */}
      {visualOf(p) && (
        <div className="mt-3 rounded-sm border border-grid bg-soot-2 px-3 py-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-xs text-muted-2">Проверено по снимку</span>
            <span className={`flex items-center gap-1.5 text-sm ${visualOf(p)!.tone}`}>
              <span
                aria-hidden="true"
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: visualOf(p)!.dot }}
              />
              {visualOf(p)!.short}
            </span>
          </div>
          <p className="mt-1.5 text-xs leading-snug text-muted-2">{visualOf(p)!.full}</p>
        </div>
      )}

      {/* Оценка по снимку высокого разрешения.
       *
       * Модель обучена на AerialWaste (Politecnico di Milano): 5 220
       * снимков, ROC-AUC 0,858 при пятикратной кросс-проверке НА ИХ
       * ДАННЫХ. Это единственное число, которое здесь доказано.
       *
       * Перенос на Казахстан НЕ доказан, и написать иначе нельзя:
       * ROC-AUC 0,643 при интервале по бутстрэпу 0,333–0,923 — нижняя
       * граница ниже случайного. Виновата не модель, а размер проверки:
       * подтверждённых свалок три.
       *
       * Соблазн был написать «ниже 0,35 модель надёжно отбраковывает» —
       * пять таких объектов действительно оказались не свалками. Но при
       * четырнадцати не-свалках из семнадцати такая пятёрка выпадает
       * случайно примерно в трети случаев. Это не доказательство, и
       * выдавать его за доказательство нельзя.
       *
       * Поэтому оценка показана как справка, а не как вердикт. */}
      {typeof p.highres_score === 'number' && (
        <div className="mt-3 rounded-sm border border-grid bg-soot-2 px-3 py-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-xs text-muted-2">Модель по снимку 0,5 м</span>
            <span className="tabular text-sm text-muted">
              {p.highres_score.toFixed(2)}
            </span>
          </div>
          <p className="mt-1.5 text-xs leading-snug text-muted-2">
            Обучена на AerialWaste, 5 220 снимков, ROC-AUC 0,858 на их данных.
            Перенос на Казахстан пока не доказан: подтверждённых свалок у нас
            три, и интервал оценки переноса шире, чем сама оценка. Число —
            справка, а не вердикт.
          </p>
        </div>
      )}

      <div className="mt-3 rounded-sm border border-grid bg-soot-2 px-3 py-2.5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-xs text-muted-2">Вероятность модели</span>
          <span className="tabular text-sm text-line">
            {hasModel ? `${Math.round(model * 100)}%` : '—'}
          </span>
        </div>
        {/* Короткая строка про этот объект, а не один и тот же абзац у всех
            тридцати. Общее описание метода повторялось в каждой карточке и
            переставало читаться уже на второй. То, что верно для всех,
            вынесено на лендинг; здесь остаётся то, что верно для этого. */}
        <p className="mt-1.5 text-xs leading-snug text-muted-2">
          {!hasModel
            ? 'Объект не попал в обучающую выборку, и модель по нему не высказывалась. Прочерк честнее подставленного числа.'
            : model < DISAGREEMENT
            ? 'Модель возражает детектору: изменение похоже на законное — карьер, стройку или отвал грунта. Ехать сюда стоит первым делом.'
            : model >= 0.85
            ? `Признаков сработало ${Number(p.n_agreeing) || 0} из 5, согласие ${Math.round(score * 100)}%. Оценка вневыборочная: этот объект модель на обучении не видела.`
            : `Признаков сработало ${Number(p.n_agreeing) || 0} из 5, согласие ${Math.round(score * 100)}%. Середина шкалы — объект стоит проверить глазами.`}
        </p>
      </div>

      <Coordinates center={centerOf(f)} />

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
