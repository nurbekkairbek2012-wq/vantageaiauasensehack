/* Витрина отбраковок: что детектор нашёл, а проверка отвергла.
 *
 * ── Зачем показывать свои ошибки ─────────────────────────────────────
 *
 * Это самый сильный материал, который у проекта есть, и одновременно
 * самый опасный, если о нём промолчать.
 *
 * Детектор ищет места, где растительность исчезла необратимо. Новый склад
 * выглядит ровно так же: трава была, потом бетон, и обратно она не
 * вырастет. Отличить свалку от застройки по этому признаку нельзя —
 * это свойство метода, а не ошибка кода.
 *
 * Поэтому вопрос не в том, будут ли в списке склады, а в том, кто скажет
 * об этом первым. Найденный проверяющим самостоятельно, такой объект
 * обрушивает доверие ко всему остальному. Названный нами — доказывает,
 * что проверка была: команда, которая показывает свои отбраковки, явно
 * смотрела, а команда, показывающая одни успехи, явно нет.
 *
 * ── Почему рядом стоят числа признаков ───────────────────────────────
 *
 * Без них раздел выглядит как покаяние. С ними он объясняет метод: физика
 * честно измерила, что растительность ушла и появился открытый грунт —
 * оба измерения верны. Неверен вывод, и его исправил человек за секунду
 * там, где машине нужен признак, которого у неё нет.
 *
 * Это и есть рабочий процесс: детектор находит изменения, человек
 * проверяет за час. Не признание слабости, а описание того, как это
 * устроено.
 */

import { SiteView } from './SiteView';

type Feature = GeoJSON.Feature & { properties: Record<string, unknown> };

/** Сколько показывать. Три — потому что четвёртый ничего не добавляет,
 *  а каждая врезка это живая карта с сетевыми запросами за тайлами. */
const SHOWN = 3;

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function signalLine(p: Record<string, unknown>): string {
  const parts: string[] = [];
  const drop = num(p.ndvi_drop);
  const bsi = num(p.bsi_rise);
  if (drop !== null) parts.push(`растительность −${drop.toFixed(2)}`);
  if (bsi !== null) parts.push(`открытый грунт +${bsi.toFixed(2)}`);
  return parts.join(', ');
}

export function Rejected({ features }: { features: Feature[] }) {
  // Самые крупные из отвергнутых: мелкий объект на снимке неубедителен, а
  // весь смысл раздела в том, чтобы читатель увидел кровлю сам.
  const shown = features
    .filter((f) => f.properties?.visual_check === 'not_landfill')
    .sort((a, b) => (num(b.properties.area_m2) ?? 0) - (num(a.properties.area_m2) ?? 0))
    .slice(0, SHOWN);

  if (shown.length < SHOWN) return null;

  return (
    <section className="border-t border-grid pt-14 pb-16">
      <h2 className="max-w-[26ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
        Мы отвергли восемнадцать собственных находок
      </h2>
      <p className="mt-4 max-w-[62ch] text-muted">
        Детектор ищет места, где растительность исчезла и не вернулась. Новый
        склад выглядит так же: трава была, потом бетон, обратно она не
        вырастет. Разделить их по этому признаку нельзя — и здесь работу
        заканчивает человек.
      </p>

      <div className="mt-8 grid gap-6 sm:grid-cols-3">
        {shown.map((f) => (
          <figure key={String(f.properties.candidate_id)} className="min-w-0">
            <SiteView feature={f} zoom={17} className="aspect-square w-full" />
            <figcaption className="mt-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-display text-sm uppercase tracking-[0.1em] text-muted-2">
                  {String(f.properties.candidate_id)}
                </span>
                <span className="tabular text-sm text-line">
                  {Math.round(num(f.properties.area_m2) ?? 0).toLocaleString('ru-RU')} м²
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-snug text-muted-2">
                Автоматика: {signalLine(f.properties)}. Глазами — не свалка.
              </p>
            </figcaption>
          </figure>
        ))}
      </div>

      <p className="mt-8 max-w-[62ch] text-sm leading-relaxed text-muted-2">
        Физика измерила верно: растительность действительно ушла, открытый
        грунт действительно появился. Неверен вывод — и его исправляет человек
        за секунду там, где машине не хватает признака. Детектор находит
        изменения, человек проверяет список за час.
      </p>
    </section>
  );
}
