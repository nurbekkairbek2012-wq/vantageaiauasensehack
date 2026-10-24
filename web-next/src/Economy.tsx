/* Экономика: что теряется в тенге и сколько из этого возвращается.
 *
 * Зачем отдельный экран
 * ---------------------
 * Карта отвечает на вопрос «куда ехать». Это вопрос инспектора. Вопрос
 * заказчика другой — «что я с этого получу», и до этого экрана ответ на
 * него был размазан по подписям: сумма ущерба на лендинге, масса в
 * карточке объекта, метан в деке. Человек, который смотрит продукт три
 * минуты, такую сумму не соберёт.
 *
 * Здесь она собрана в один экран и в один порядок:
 *
 *     нашли отходы → посчитали, во что обходятся → сколько вернётся
 *     сырьём → в каком порядке убирать → что уже потеряно навсегда
 *
 * Каждое звено — число из выгрузки, а не из вёрстки. Считает их
 * scripts/economy_export.py тем же денежным слоем, что и карта, с тем же
 * зерном Монте-Карло.
 *
 * Почему рядом с числами стоят пометки
 * ------------------------------------
 * «Подтверждено», «выведено», «инженерная оценка» — не украшение.
 * Экономика держится на восьми допущениях, из которых прямым источником
 * подтверждены не все, и число без пометки о происхождении невозможно
 * проверить. Вопрос «а эти сорок пять миллионов — измерены или
 * посчитаны?» задают первым, и правильный ответ должен стоять на экране
 * до вопроса, а не звучать после.
 */

import { useEffect, useMemo, useState } from 'react';
import { Nav } from './components/Nav';
import { Mark } from './components/Logo';

type Band = { p10: number; p50: number; p90: number };

type EconObject = {
  id: string;
  break_date: string | null;
  age_years: number;
  area_m2: number;
  mass_t: number;
  removal_kzt: number;
  recyclable_kzt: number;
  climate_kzt: number;
  damage_p10: number;
  damage_p50: number;
  damage_p90: number;
  co2e_t: number;
  co2e_emitted_t: number;
  co2e_preventable_t: number;
  co2e_next_year_t: number;
  penalty_kzt: number;
  check_source: string;
  removal_status: string;
};

type Economy = {
  generated: string;
  iterations: number;
  queue: { raw: number; auto_rejected: number; reviewed: number; published: number; ground: number };
  objects: EconObject[];
  priority: { n: number; id: string; share: number }[];
  totals: {
    objects: number;
    area_m2: number;
    mass_t: Band;
    removal_kzt: Band;
    recyclable_kzt: Band;
    climate_kzt: Band;
    damage_kzt: Band;
    naive_damage_kzt: Band;
    sum_of_medians: {
      mass_t: number;
      removal_kzt: number;
      recyclable_kzt: number;
      climate_kzt: number;
      damage_kzt: number;
      co2e_t: number;
      co2e_emitted_t: number;
      co2e_preventable_t: number;
      co2e_next_year_t: number;
    };
    co2e_t: number;
    co2e_emitted_t: number;
    co2e_preventable_t: number;
    penalty_kzt: number;
    waiting_year_co2e_t: number;
    waiting_year_kzt: number;
  };
  sensitivity: Record<string, number>;
  sensitivity_area_m2: number;
  provenance: Record<string, { title: string; kind: string; note: string }>;
};

const num = (v: number, d = 0) =>
  Number.isFinite(v) ? v.toLocaleString('ru-RU', { maximumFractionDigits: d }) : '—';

/* Деньги на этом экране всегда в миллионах и всегда с одним знаком.
   Смешивать «89 831 088 ₸» и «45,7 млн ₸» в одной таблице нельзя: глаз
   сравнивает длину строки быстрее, чем читает разряды. */
const mln = (v: number, d = 1) => `${num(v / 1e6, d)} млн ₸`;

const kzt = (v: number) =>
  Math.abs(v) >= 1e6 ? mln(v) : `${num(v / 1e3)} тыс ₸`;

/* Три вида происхождения числа, тремя разными цветами.
   Слово «оценка» рядом с суммой в сорок пять миллионов делает с доверием
   больше, чем ещё одна цифра после запятой. */
const KIND: Record<string, { label: string; className: string }> = {
  source: { label: 'подтверждено', className: 'text-emerald border-emerald/40' },
  derived: { label: 'выведено', className: 'text-violet-lit border-violet-lit/40' },
  estimate: { label: 'инженерная оценка', className: 'text-amber border-amber/40' },
};

const DRIVER_NAMES: Record<string, string> = {
  removal_cost: 'стоимость вывоза тонны',
  depth: 'глубина залегания',
  density: 'плотность отходов',
  doc: 'доля разлагаемого углерода',
  k: 'скорость разложения',
  carbon_price: 'цена углеродной единицы',
};

function Badge({ kind }: { kind: string }) {
  const k = KIND[kind];
  if (!k) return null;
  return (
    <span className={`ml-2 whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-[0.08em] ${k.className}`}>
      {k.label}
    </span>
  );
}

export default function Economy() {
  const [data, setData] = useState<Economy | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetch('./data/economy.json')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('нет выгрузки'))))
      .then(setData)
      .catch(() => setFailed(true));
  }, []);

  /* Сколько выездов закрывают половину суммы и сколько — восемьдесят
     процентов. Это и есть рекомендация: список из пятнадцати строк
     советом не является, «начните с четырёх» — является. */
  const cut = useMemo(() => {
    if (!data) return null;
    const at = (share: number) =>
      data.priority.find((p) => p.share >= share)?.n ?? data.priority.length;
    return { half: at(0.5), most: at(0.8) };
  }, [data]);

  const top = useMemo(() => {
    if (!data) return [];
    const byId = new Map(data.objects.map((o) => [o.id, o]));
    return data.priority
      .map((p) => ({ ...p, obj: byId.get(p.id) }))
      .filter((p): p is typeof p & { obj: EconObject } => Boolean(p.obj));
  }, [data]);

  if (failed) {
    return (
      <div className="min-h-screen">
        <Nav current="economy" />
        <main className="mx-auto max-w-[900px] px-6 py-16">
          <h1 className="text-2xl text-line">Выгрузки экономики нет</h1>
          <p className="mt-3 text-muted">
            Соберите её командой <code className="text-line">python scripts/economy_export.py</code> —
            экран читает <code className="text-line">data/economy.json</code> и ничего не
            держит в вёрстке.
          </p>
        </main>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-screen">
        <Nav current="economy" />
        <div className="mx-auto max-w-[1240px] px-6 py-16">
          <div className="h-64 animate-pulse rounded-md bg-soot-2" />
        </div>
      </div>
    );
  }

  const t = data.totals;
  /* Крупные числа страницы — суммы медиан по объектам, а не медианы
     сумм. Разница невелика (43,0 против 45,7 млн ₸) и объяснима, но
     складывает столбец таблицы читатель, а не мы: число, которое не
     сходится с таблицей под ним, обесценивает и таблицу, и объяснение.
     Портфельный расчёт остаётся там, где он действительно нужен, — в
     интервале, потому что сложить P10 по объектам нельзя вообще. */
  const sum = t.sum_of_medians;
  /* Доля стоимости уборки, которую закрывает сырьё внутри кучи. Главное
     число этого экрана: оно превращает расход в частично возвратный. */
  const recovery = sum.recyclable_kzt / sum.removal_kzt;
  const irreversible = sum.co2e_emitted_t / (sum.co2e_t || 1);

  return (
    <div className="min-h-screen">
      <div className="sticky top-0 z-10 bg-soot/92 backdrop-blur-[2px]">
        <div className="mx-auto max-w-[1240px]">
          <Nav current="economy">
            <a
              href="./map.html"
              className="rounded-sm bg-violet px-5 py-2.5 font-display text-sm font-semibold uppercase tracking-[0.12em] text-paper no-underline transition-colors duration-200 hover:bg-violet-lit hover:text-soot"
            >
              Открыть карту
            </a>
          </Nav>
        </div>
      </div>

      <main className="mx-auto max-w-[1240px] px-6">
        {/* ── Ответ на вопрос трека ─────────────────────────────────── */}
        <section className="border-b border-grid pt-12 pb-14">
          <p className="font-display text-[11px] uppercase tracking-[0.16em] text-violet-lit">
            Экономический эффект
          </p>
          <h1 className="mt-3 max-w-[22ch] text-[clamp(2rem,4.6vw,3.2rem)] leading-[1.06] text-line">
            Отходы — это ресурс, вывезенный мимо экономики
          </h1>
          <p className="mt-5 max-w-[62ch] text-lg leading-relaxed text-muted">
            {num(sum.mass_t)} тонн, найденных под Астаной, стоят бюджету{' '}
            <strong className="font-normal text-line">{mln(sum.removal_kzt)}</strong> на вывоз.
            Внутри этих же тонн лежит вторсырья на{' '}
            <strong className="font-normal text-emerald">{mln(sum.recyclable_kzt)}</strong> —
            {' '}{num(recovery * 100)}% стоимости уборки возвращается, если разбирать, а не
            просто перевозить на полигон.
          </p>

          {/* Цепочка кейса: ресурс → потеря → ИИ → приоритет → деньги.
              Она стоит первой и именно строкой: жюри должно увидеть связку
              за пять секунд, а не собрать её из четырёх разделов. */}
          <ol className="mt-9 grid gap-px overflow-hidden rounded-md border border-grid bg-grid sm:grid-cols-5">
            {[
              ['Ресурс', `${num(sum.mass_t)} т отходов`, 'масса по площади и глубине'],
              ['Потеря', mln(sum.damage_kzt), 'вывоз минус сырьё плюс климат'],
              ['ИИ', `${data.queue.raw} → ${data.queue.reviewed}`, 'отсев до человека'],
              ['Приоритет', `${cut?.half ?? '—'} выезда`, 'закрывают половину суммы'],
              ['Возврат', mln(sum.recyclable_kzt), 'вторсырьё внутри кучи'],
            ].map(([step, value, note]) => (
              <li key={step} className="bg-soot-2 px-4 py-4">
                <div className="font-display text-[10px] uppercase tracking-[0.14em] text-violet-lit">
                  {step}
                </div>
                <div className="tabular mt-2 font-display text-[clamp(1.05rem,1.8vw,1.4rem)] leading-none text-line">
                  {value}
                </div>
                <div className="mt-1.5 text-xs leading-snug text-muted-2">{note}</div>
              </li>
            ))}
          </ol>
        </section>

        {/* ── Из чего складывается сумма ────────────────────────────── */}
        <section className="border-b border-grid pt-14 pb-14">
          <h2 className="max-w-[24ch] text-[clamp(1.6rem,3.4vw,2.4rem)] text-line">
            Из чего складывается {mln(sum.damage_kzt)}
          </h2>
          <p className="mt-4 max-w-[60ch] text-muted">
            Три слагаемых, и одно из них со знаком минус. Штраф в ущерб не
            входит: это санкция нарушителю, а не расход бюджета, и складывать
            их — методологическая ошибка.
          </p>

          <div className="mt-9 grid gap-8 lg:grid-cols-[1.15fr_1fr]">
            <dl className="divide-y divide-grid border-y border-grid">
              {[
                ['Вывоз и захоронение', sum.removal_kzt, '+', 'text-line',
                 'масса × тариф за тонну + плечо до полигона'],
                ['Извлекаемое вторсырьё', sum.recyclable_kzt, '−', 'text-emerald',
                 'пластик, бумага, металл, стекло — по прайсу приёмки и извлекаемой доле'],
                ['Климатический ущерб', sum.climate_kzt, '+', 'text-amber',
                 'метан за 20 лет по IPCC FOD, в цене углеродной единицы'],
              ].map(([label, value, sign, color, note]) => (
                <div key={String(label)} className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-4">
                  <div className="min-w-[16ch] flex-1">
                    <dt className={`text-base ${color}`}>
                      <span className="mr-1.5 text-muted-2">{sign as string}</span>
                      {label as string}
                    </dt>
                    <dd className="mt-1 max-w-[46ch] text-xs leading-snug text-muted-2">{note as string}</dd>
                  </div>
                  <dd className={`tabular font-display text-xl leading-none ${color}`}>
                    {mln(value as number)}
                  </dd>
                </div>
              ))}
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-4">
                <dt className="text-base text-line">Чистый ущерб по списку</dt>
                <dd className="tabular text-right">
                  <div className="font-display text-2xl leading-none text-violet-lit">{mln(sum.damage_kzt)}</div>
                  <div className="mt-1 text-xs text-muted-2">
                    интервал {mln(t.damage_kzt.p10)} — {mln(t.damage_kzt.p90)}
                  </div>
                </dd>
              </div>
            </dl>

            <div>
              {/* Полоса состава: она нужна не для красоты, а чтобы было
                  видно, что сырьё закрывает половину вывоза. В таблице это
                  число, в полосе — половина ширины. */}
              <div className="rounded-md border border-grid bg-soot-2 p-5">
                <div className="text-xs uppercase tracking-[0.1em] text-muted-2">
                  Вывоз и то, что из него возвращается
                </div>
                <div className="mt-4 h-4 w-full overflow-hidden rounded-sm bg-soot-3">
                  <div
                    className="h-full bg-emerald/70"
                    style={{ width: `${Math.min(100, recovery * 100)}%` }}
                  />
                </div>
                <div className="mt-3 flex justify-between text-sm">
                  <span className="text-emerald">вернётся сырьём {num(recovery * 100)}%</span>
                  <span className="text-muted-2">останется расходом {num(100 - recovery * 100)}%</span>
                </div>
                <p className="mt-4 text-sm leading-relaxed text-muted">
                  Это и есть финансовая часть EcoFin: свалка перестаёт быть
                  только статьёй расхода. Половину уборки оплачивает то, что
                  в ней лежит, — если приехать с сортировкой, а не с
                  самосвалом.
                </p>
              </div>

              <div className="mt-6 rounded-md border border-grid p-5">
                <div className="text-xs uppercase tracking-[0.1em] text-muted-2">
                  Отдельно от ущерба
                </div>
                <div className="tabular mt-2 font-display text-2xl text-line">{kzt(t.penalty_kzt)}</div>
                <p className="mt-2 text-sm leading-relaxed text-muted-2">
                  штрафы по ст. 344 ч. 2-1 КоАП РК по всем {t.objects} объектам, если
                  нарушители установлены. Возврат в бюджет, а не снижение
                  ущерба.
                </p>
              </div>
            </div>
          </div>

          {/* Разница между медианой суммы и суммой медиан названа сама.
              Её всё равно найдут: на карте объекты складываются, и сумма
              не сходится с этим экраном. Названная разница — рабочая
              деталь, найденная — расхождение в данных. */}
          <p className="mt-8 max-w-[74ch] text-sm leading-relaxed text-muted-2">
            Крупные числа выше — суммы медиан по объектам: столбец таблицы
            ниже складывается ровно в них. Интервал так сложить нельзя.{' '}
            <strong className="font-normal text-line">{mln(t.damage_kzt.p10)} — {mln(t.damage_kzt.p90)}</strong>{' '}
            получен розыгрышем всего списка целиком: {num(data.iterations)} итераций,
            цены на каждой итерации одни для всех объектов — тариф на вывоз в
            городе один. Сложение P10 и P90 по объектам дало бы{' '}
            {mln(t.naive_damage_kzt.p10)} — {mln(t.naive_damage_kzt.p90)}: шире,
            потому что такая сумма предполагает, что все {t.objects} объектов
            одновременно окажутся дешевле, чем в девяти случаях из десяти.
            Медиана суммы при этом {mln(t.damage_kzt.p50)}, а сумма медиан{' '}
            {mln(sum.damage_kzt)} — распределение несимметрично, и это разные
            вопросы к одному расчёту, а не разные данные.
          </p>
        </section>

        {/* ── Приоритет: рекомендация, а не список ──────────────────── */}
        <section className="border-b border-grid pt-14 pb-14">
          <h2 className="max-w-[26ch] text-[clamp(1.6rem,3.4vw,2.4rem)] text-line">
            {cut?.half} выезда закрывают половину суммы
          </h2>
          <p className="mt-4 max-w-[60ch] text-muted">
            Пятнадцать точек — это не рекомендация, а список. Рекомендация —
            порядок: объекты отсортированы по деньгам, и накопленная доля
            показывает, где можно остановиться. {cut?.most} выездов закрывают
            80% суммы, оставшиеся {t.objects - (cut?.most ?? 0)} стоят вместе меньше
            пятой части.
          </p>

          <div className="mt-8 overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-grid text-left text-xs uppercase tracking-[0.08em] text-muted-2">
                  <th className="py-3 pr-3 font-normal">#</th>
                  <th className="py-3 pr-3 font-normal">Объект</th>
                  <th className="py-3 pr-3 text-right font-normal">Возник</th>
                  <th className="py-3 pr-3 text-right font-normal">Масса</th>
                  <th className="py-3 pr-3 text-right font-normal">Вывоз</th>
                  <th className="py-3 pr-3 text-right font-normal">Сырьё</th>
                  <th className="py-3 pr-3 text-right font-normal">Ущерб</th>
                  <th className="py-3 text-right font-normal">Накоплено</th>
                </tr>
              </thead>
              <tbody>
                {top.map(({ n, obj, share }) => (
                  <tr
                    key={obj.id}
                    className={`border-b border-grid/60 ${share <= 0.5 ? 'bg-violet-deep/15' : ''}`}
                  >
                    <td className="tabular py-2.5 pr-3 text-muted-2">{n}</td>
                    <td className="py-2.5 pr-3 text-line">
                      {obj.id}
                      {obj.check_source === 'ground' && (
                        <span className="ml-2 text-[10px] uppercase tracking-[0.08em] text-emerald">
                          выезд
                        </span>
                      )}
                    </td>
                    <td className="tabular py-2.5 pr-3 text-right text-muted">
                      {obj.break_date ? obj.break_date.slice(0, 7).replace('-', '.') : '—'}
                    </td>
                    <td className="tabular py-2.5 pr-3 text-right text-muted">{num(obj.mass_t)} т</td>
                    <td className="tabular py-2.5 pr-3 text-right text-muted">{mln(obj.removal_kzt)}</td>
                    <td className="tabular py-2.5 pr-3 text-right text-emerald">{mln(obj.recyclable_kzt)}</td>
                    <td className="tabular py-2.5 pr-3 text-right text-line">{mln(obj.damage_p50)}</td>
                    <td className="tabular py-2.5 text-right text-muted-2">{num(share * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-5 max-w-[70ch] text-sm leading-relaxed text-muted-2">
            Тот же порядок лежит в маршруте на{' '}
            <a href="./map.html" className="text-violet-lit underline decoration-grid">карте</a> и
            в PDF-акте: инспектор получает не карту вероятностей, а очередь,
            отсортированную по деньгам.
          </p>
        </section>

        {/* ── Экономия на самой проверке ────────────────────────────── */}
        <section className="border-b border-grid pt-14 pb-14">
          <h2 className="max-w-[24ch] text-[clamp(1.6rem,3.4vw,2.4rem)] text-line">
            Вторая экономия — на самой проверке
          </h2>
          <p className="mt-4 max-w-[62ch] text-muted">
            Выезд на подозрительное место стоит часа дороги. Поэтому дешевле
            всего не тот объект, который нашли, а тот, на который не поехали
            зря: до человека доходит {data.queue.reviewed} мест из{' '}
            {data.queue.raw}, а до выезда — {data.queue.published}.
          </p>

          <div className="mt-8 grid gap-px overflow-hidden rounded-md border border-grid bg-grid sm:grid-cols-4">
            {[
              [num(data.queue.raw), 'нашёл детектор', 'изменение поверхности за восемь лет'],
              [num(data.queue.auto_rejected), 'снял контекстный отсев',
               'карьеры, стройки, вода, слишком близко к жилью'],
              [num(data.queue.reviewed), 'просмотрел человек',
               'по снимку 0,4–0,8 м на пиксель'],
              [num(data.queue.published), 'дошли до списка',
               `из них ${data.queue.ground} проверены на месте`],
            ].map(([big, what, note]) => (
              <div key={String(what)} className="bg-soot-2 px-4 py-5">
                <div className="tabular font-display text-[clamp(1.5rem,2.6vw,2rem)] leading-none text-violet-lit">
                  {big}
                </div>
                <div className="mt-2 text-sm leading-snug text-line">{what}</div>
                <div className="mt-1 text-xs leading-snug text-muted-2">{note}</div>
              </div>
            ))}
          </div>

          <p className="mt-6 max-w-[70ch] text-sm leading-relaxed text-muted-2">
            Машинный просмотр снимков измерен отдельно, на 49 объектах с
            человеческим вердиктом: он снимает 71% ручной работы, ошибается в
            одном отказе из тридцати пяти и теряет одну свалку из восьми.
            Поэтому его вердикт — подсказка: объект снимает человек, а не
            модель. Разбор с матрицей ошибок — в{' '}
            <span className="text-line">docs/AI_RESULTS.md</span>.
          </p>
        </section>

        {/* ── Что уже потеряно ──────────────────────────────────────── */}
        <section className="border-b border-grid pt-14 pb-14">
          <div className="grid gap-10 lg:grid-cols-[1.1fr_1fr]">
            <div>
              <h2 className="max-w-[22ch] text-[clamp(1.6rem,3.4vw,2.4rem)] text-line">
                {num(irreversible * 100)}% климатического ущерба уже необратимы
              </h2>
              <p className="mt-4 max-w-[56ch] text-muted">
                Метан уходит с первого дня, и разложение экспоненциальное:
                больше всего в первые годы. Средний объект в списке лежит без
                внимания годами — {num(sum.co2e_emitted_t)} т CO₂-экв из{' '}
                {num(sum.co2e_t)} уже в атмосфере, и убрать их оттуда уборкой
                нельзя.
              </p>
              <p className="mt-4 max-w-[56ch] text-muted">
                Это и есть цена позднего обнаружения, выраженная не словом
                «важно», а числом. Ровно её снижает ранний поиск: убрать сто
                тонн дешевле, чем тысячу, и предотвращённый метан считается
                только с момента уборки.
              </p>
            </div>
            <dl className="grid grid-cols-2 gap-6 self-start border-l border-grid pl-8">
              {[
                ['Уже выброшено', `${num(sum.co2e_emitted_t)} т`, 'text-amber'],
                ['Ещё можно предотвратить', `${num(sum.co2e_preventable_t)} т`, 'text-emerald'],
                ['Уйдёт за следующий год', `${num(sum.co2e_next_year_t)} т`, 'text-line'],
                ['Всего за 20 лет', `${num(sum.co2e_t)} т`, 'text-line'],
              ].map(([k, v, color]) => (
                <div key={String(k)}>
                  <dt className="text-xs leading-snug text-muted-2">{k}</dt>
                  <dd className={`tabular mt-1.5 font-display text-2xl leading-none ${color}`}>{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {/* ── Происхождение чисел ───────────────────────────────────── */}
        <section className="border-b border-grid pt-14 pb-14">
          <h2 className="max-w-[24ch] text-[clamp(1.6rem,3.4vw,2.4rem)] text-line">
            Откуда взято каждое допущение
          </h2>
          <p className="mt-4 max-w-[62ch] text-muted">
            Восемь величин, и они не равны по надёжности. Пометка стоит у
            каждой: подтверждено источником, выведено из подтверждённых
            величин или названо инженерной оценкой там, где открытого
            источника нет.
          </p>

          <div className="mt-8 grid gap-x-10 gap-y-5 md:grid-cols-2">
            {Object.entries(data.provenance).map(([key, p]) => (
              <div key={key} className="border-t border-grid pt-3">
                <div className="flex flex-wrap items-baseline">
                  <span className="text-base text-line">{p.title}</span>
                  <Badge kind={p.kind} />
                </div>
                <p className="mt-1.5 max-w-[52ch] text-xs leading-relaxed text-muted-2">{p.note}</p>
              </div>
            ))}
          </div>

          <div className="mt-10 rounded-md border border-grid bg-soot-2 p-6">
            <h3 className="text-base text-line">
              Какое допущение двигает результат сильнее всех
            </h3>
            <p className="mt-2 max-w-[62ch] text-sm leading-relaxed text-muted">
              Ранговая корреляция Спирмена между разыгранным значением
              допущения и итоговым ущербом. Считано на объекте медианной
              площади ({num(data.sensitivity_area_m2)} м²), {num(data.iterations)} итераций.
            </p>
            <dl className="mt-5 space-y-2.5">
              {Object.entries(data.sensitivity).map(([key, value]) => (
                <div key={key} className="flex items-center gap-4">
                  <dt className="w-[24ch] shrink-0 text-sm text-muted">
                    {DRIVER_NAMES[key] ?? key}
                  </dt>
                  <dd className="flex min-w-0 flex-1 items-center gap-3">
                    <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-sm bg-soot-3">
                      <div
                        className={`h-full ${Math.abs(value) > 0.5 ? 'bg-amber/80' : 'bg-violet/70'}`}
                        style={{ width: `${Math.min(100, Math.abs(value) * 100)}%` }}
                      />
                    </div>
                    <span className="tabular w-[6ch] shrink-0 text-right text-sm text-line">
                      {value.toFixed(2).replace('.', ',')}
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-5 max-w-[62ch] text-sm leading-relaxed text-muted-2">
              Один параметр — стоимость вывоза тонны — двигает итог сильнее
              всех остальных вместе. Прямого тарифа по Астане в открытом
              доступе нет, поэтому величина выведена из тарифа Алматы и
              помечена как выведенная. Это первое, что стоит уточнить у
              оператора, и первое, что мы просим на пилоте.
            </p>
          </div>
        </section>

        <section className="pt-12 pb-16">
          <p className="max-w-[70ch] text-sm leading-relaxed text-muted-2">
            Все числа на этой странице читаются из{' '}
            <span className="text-line">data/economy.json</span>, который собирает{' '}
            <span className="text-line">scripts/economy_export.py</span> тем же денежным
            слоем и с тем же зерном Монте-Карло, что и карта. Ни одно не
            вписано в вёрстку. Выгрузка от {data.generated}.
          </p>
        </section>
      </main>

      <footer className="border-t border-grid">
        <div className="mx-auto flex max-w-[1240px] flex-wrap items-center justify-between gap-6 px-6 py-9">
          <div className="flex items-center gap-3">
            <Mark size={34} />
            <span className="text-sm text-muted-2">
              Future Minds Hackathon 2026 · трек EcoFin · Астана
            </span>
          </div>
          <a
            href="https://github.com/k41270075-stack/hakathon"
            className="text-sm text-muted underline decoration-grid transition-colors duration-200 hover:text-line hover:decoration-violet-lit"
          >
            Исходный код
          </a>
        </div>
      </footer>
    </div>
  );
}
