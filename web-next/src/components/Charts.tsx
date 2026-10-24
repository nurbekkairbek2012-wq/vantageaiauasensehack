/* Графика лендинга: три картинки вместо трёх абзацев.
 *
 * Все три считаются из того же candidates.geojson, который лежит на карте.
 * Ни одно число здесь не вписано руками — если прогон изменится, изменятся
 * и картинки. Это не педантизм: вписанное руками число на лендинге живёт
 * ровно до первого нового прогона, после чего тихо становится ложью.
 */

type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;

const num = (v: number, d = 0) =>
  Number.isFinite(v) ? v.toLocaleString('ru-RU', { maximumFractionDigits: d }) : '—';

/* ── Воронка отсева ──────────────────────────────────────────────────
 *
 * Была таблицей на четыре строки. Таблица отвечает на вопрос «сколько
 * чего», но не показывает главного — что до списка доходит один объект из
 * четырнадцати. Ширина полосы показывает это без единого слова.
 */

export type Stage = { label: string; detail: string; count: number };

export function Funnel({ stages, kept }: { stages: Stage[]; kept: number }) {
  const total = stages.reduce((s, x) => s + x.count, 0) + kept;

  return (
    <div className="mt-9">
      <div className="flex flex-col gap-2">
        {stages.map((stage) => (
          <div key={stage.label} className="group grid grid-cols-[1fr] gap-1 sm:grid-cols-[19rem_1fr]">
            <div className="flex items-baseline justify-between gap-3 sm:block">
              <p className="text-sm text-line">{stage.label}</p>
              <p className="hidden text-xs leading-snug text-muted-2 sm:block">{stage.detail}</p>
            </div>
            <div className="flex items-center gap-3">
              <div
                className="h-7 rounded-[2px] bg-violet-deep/70 transition-colors duration-200 group-hover:bg-violet-deep"
                style={{ width: `${Math.max(2, (stage.count / total) * 100)}%` }}
              />
              <span className="tabular font-display text-base text-muted">{stage.count}</span>
            </div>
          </div>
        ))}

        <div className="mt-1 grid grid-cols-[1fr] gap-1 border-t border-grid pt-3 sm:grid-cols-[19rem_1fr]">
          <div className="flex items-baseline justify-between gap-3 sm:block">
            <p className="text-sm text-line">Прошли отсев</p>
            <p className="hidden text-xs leading-snug text-muted-2 sm:block">
              поехать можно по каждому
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div
              className="h-7 rounded-[2px] bg-violet-lit"
              style={{ width: `${Math.max(2, (kept / total) * 100)}%` }}
            />
            <span className="tabular font-display text-base text-line">{kept}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Появления по годам ──────────────────────────────────────────────
 *
 * Отвечает на вопрос, которого текст на лендинге не касался вовсе: это
 * копилось равномерно или рвануло? Ответ виден за секунду и ведёт на
 * таймлапс, где то же самое показано на местности.
 */

export function YearBars({ features }: { features: Feature[] }) {
  const byYear = new Map<number, number>();
  features.forEach((f) => {
    const date = String(f.properties?.break_date ?? '');
    if (!/^\d{4}/.test(date)) return;
    const year = Number(date.slice(0, 4));
    byYear.set(year, (byYear.get(year) ?? 0) + 1);
  });

  const years = [...byYear.keys()].sort((a, b) => a - b);
  if (!years.length) return null;

  const span = Array.from(
    { length: years[years.length - 1] - years[0] + 1 },
    (_, i) => years[0] + i,
  );
  const peak = Math.max(...byYear.values());

  return (
    <div className="mt-8">
      <div className="flex h-40 items-end gap-2">
        {span.map((year) => {
          const count = byYear.get(year) ?? 0;
          return (
            /* h-full и justify-end обязательны. Без них колонка получает
               высоту по содержимому, процентная высота полосы решается
               относительно auto и схлопывается в ноль: на странице
               оставались одни подписи без единой полосы. */
            <div key={year} className="flex h-full flex-1 flex-col items-center justify-end gap-2">
              <span className="tabular text-xs text-muted-2">{count || ''}</span>
              <div
                className="w-full rounded-t-[2px] bg-violet transition-colors duration-200 hover:bg-violet-lit"
                style={{ height: `${Math.max(count ? 4 : 0, (count / peak) * 100)}%` }}
                title={`${year}: ${count}`}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex gap-2 border-t border-grid pt-2">
        {span.map((year) => (
          <span key={year} className="tabular flex-1 text-center text-xs text-muted-2">
            {String(year).slice(2)}
          </span>
        ))}
      </div>
    </div>
  );
}

/* ── Ущерб по объектам ───────────────────────────────────────────────
 *
 * Одна полоса «от P10 до P90» показывала неопределённость одного объекта.
 * Здесь видно другое и более важное: распределение по всем тридцати. Оно
 * не равномерное — несколько объектов дают почти всю сумму, и это меняет
 * порядок объезда сильнее любой методики.
 */

export function DamageStrip({ features }: { features: Feature[] }) {
  const values = features
    .map((f) => Number(f.properties?.damage_p50) || 0)
    .filter((v) => v > 0)
    .sort((a, b) => b - a);

  if (!values.length) return null;

  const total = values.reduce((s, v) => s + v, 0);
  const peak = values[0];
  const topThree = values.slice(0, 3).reduce((s, v) => s + v, 0);

  return (
    <div className="mt-8">
      <div className="flex h-28 items-end gap-[3px]">
        {values.map((value, i) => (
          <div
            key={i}
            className={`flex-1 rounded-t-[1px] ${i < 3 ? 'bg-violet-lit' : 'bg-violet-deep'}`}
            style={{ height: `${Math.max(2, (value / peak) * 100)}%` }}
            title={`${num(value / 1e6, 1)} млн ₸`}
          />
        ))}
      </div>
      <p className="mt-3 max-w-[62ch] text-sm text-muted-2">
        Тридцать объектов по убыванию ущерба. Три первых —{' '}
        <span className="tabular text-line">{Math.round((topThree / total) * 100)}%</span> всей
        суммы. Ехать по списку сверху вниз и ехать по случайному — не одно и
        то же.
      </p>
    </div>
  );
}
