/* Сколько времени объект лежит незамеченным — и во что это обходится.
 *
 * ── Зачем этот раздел жителю ─────────────────────────────────────────
 *
 * Остальная страница объясняет, что житель даёт системе: точку, которую
 * спутник не увидит. Это правда и это важно, но человеку, открывшему
 * страницу впервые, предлагается работа без ответа на вопрос «а мне что».
 *
 * Ответ есть, и он в данных: медианный объект из списка существует шесть
 * лет. Не «где-то есть свалки» — вот эти, с датой появления, рядом с
 * городом, и шесть лет их никто не считал. Число проверяемое: дата
 * появления стоит в карточке каждого объекта.
 *
 * ── Почему время это деньги, а не метафора ───────────────────────────
 *
 * Разложение отходов даёт метан, и наша оценка ущерба считает его на
 * двадцать лет вперёд по методике IPCC. Чем дольше объект лежит, тем
 * больше накопленный выброс и тем больше масса, которую придётся вывезти.
 * Год промедления — это не «непорядок», это строка в счёте.
 *
 * ── Числа считаются здесь, а не вписаны руками ───────────────────────
 *
 * Прогон обновляется, и вписанные числа разошлись бы с картой на первом
 * же пересчёте. Расхождение чисел между страницами — то, что проверяющий
 * находит за минуту и после чего перестаёт верить остальным.
 */

type Feature = GeoJSON.Feature & { properties: Record<string, unknown> };

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function years(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const when = Date.parse(value.slice(0, 10));
  if (Number.isNaN(when)) return null;
  return (Date.now() - when) / (365.25 * 24 * 3600 * 1000);
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function HowLong({ features }: { features: Feature[] }) {
  // Отвергнутые проверкой не считаются: складу «возраст» не приписывают.
  const real = features.filter((f) => f.properties?.visual_check !== 'not_landfill');
  const ages = real.map((f) => years(f.properties.break_date)).filter((a): a is number => a !== null);
  if (ages.length < 3) return null;

  const mass = real.reduce((s, f) => s + (num(f.properties.mass_t) ?? 0), 0);
  const old = ages.filter((a) => a > 5).length;

  /* Метан, уже ушедший за годы лежания.
     Раньше абзац ниже утверждал «каждый год ожидания — это выброс, которого
     уже не вернуть», а посчитано это не было: модель разложения IPCC FOD
     считала выброс от момента захоронения одинаково для свалки 2019 года и
     2024-го. Теперь возраст входит в расчёт, и утверждение подкреплено
     числом. */
  const total = real.reduce((s, f) => s + (num(f.properties.co2e_t) ?? 0), 0);
  const gone = real.reduce((s, f) => s + (num(f.properties.co2e_emitted_t) ?? 0), 0);
  const goneShare = total > 0 ? Math.round((gone / total) * 100) : 0;

  const stats: [string, string][] = [
    ['лет лежит средний объект', median(ages).toFixed(1).replace('.', ',')],
    ['из ' + ages.length + ' — дольше пяти лет', String(old)],
    ['тонн отходов в списке', Math.round(mass).toLocaleString('ru-RU')],
  ];
  if (gone > 0) {
    stats.push([
      `% метана уже ушло безвозвратно`,
      String(goneShare),
    ]);
  }

  /* Заголовок берёт то же число, что и плитка под ним.
     Раньше в нём стояло «Шесть лет» словами, а плитка считалась из данных.
     После пересчёта они разошлись: заголовок обещал шесть, число под ним
     показывало 4,4. Читатель видит оба одновременно, и это разрушает
     доверие ко всей странице быстрее, чем любая неточность по отдельности. */
  /* Ровно то же число, что в плитке, вплоть до десятых: округлённое «4
     года» рядом с «4,4» читается как две разные величины, и читатель
     видит их одновременно. Склонение — по целой части. */
  const exact = median(ages);
  const shown = exact.toFixed(1).replace('.', ',');
  const whole = Math.floor(exact) % 10;
  const word = Math.floor(exact) % 100 >= 11 && Math.floor(exact) % 100 <= 14 ? 'лет'
    : whole === 1 ? 'год' : whole >= 2 && whole <= 4 ? 'года' : 'лет';
  const said = `${shown} ${word}`;

  return (
    <section className="mt-14 border-t border-grid pt-12">
      <h2 className="max-w-[24ch] text-[clamp(1.6rem,3.4vw,2.4rem)] text-line">
        {said} — столько объект лежит, пока его никто не считает
      </h2>

      <dl className="mt-8 grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map(([label, value], i) => (
          <div
            key={label}
            className={
              /* Разделитель — только между соседями в строке. При четырёх
                 колонках на двухколоночной раскладке он иначе повисает
                 слева у третьей плитки, в начале второй строки. */
              i > 0 ? 'sm:border-l sm:border-grid sm:pl-8 [&:nth-child(odd)]:sm:border-l-0'
                    + ' [&:nth-child(odd)]:sm:pl-0 lg:border-l lg:pl-8'
                    + ' [&:nth-child(odd)]:lg:border-l [&:nth-child(odd)]:lg:pl-8'
                : ''
            }
          >
            <dd className="tabular font-display text-[clamp(2rem,4vw,3rem)] leading-none text-violet-lit">
              {value}
            </dd>
            <dt className="mt-2 text-sm leading-snug text-muted-2">{label}</dt>
          </div>
        ))}
      </dl>

      <p className="mt-8 max-w-[62ch] leading-relaxed text-muted">
        Это не метафора про «непорядок». Отходы разлагаются и дают метан, и
        мы считаем его по методике IPCC от даты возникновения каждого
        объекта, а не от сегодняшнего дня. Разложение экспоненциальное:
        первые годы дают больше всего, и потому счёт идёт не с момента, когда
        свалку заметили.{' '}
        {gone > 0 && (
          <>
            По списку это{' '}
            <b className="font-normal text-line">
              {Math.round(gone).toLocaleString('ru-RU')} тонн CO₂-эквивалента
            </b>
            , которые уже ушли в атмосферу и которых не вернуть, — {goneShare}%
            всего, что эти объекты отдадут за двадцать лет. Убрать их сейчас
            значит предотвратить остальные{' '}
            {Math.round(total - gone).toLocaleString('ru-RU')} тонн.
          </>
        )}
      </p>
      <p className="mt-4 max-w-[62ch] leading-relaxed text-muted">
        Спутник заметил эти объекты через годы после появления — раньше было
        физически нечего увидеть. Человек, проходящий мимо, замечает в первую
        неделю. Одна точка из телефона сокращает {said} до семи дней.
      </p>
    </section>
  );
}
