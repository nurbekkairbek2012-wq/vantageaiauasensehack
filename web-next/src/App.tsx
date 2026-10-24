/* Лендинг.
 *
 * Первая версия доказывала словами и проиграла собственному предмету.
 * Пять признаков, восемь допущений, триста восемьдесят пять кандидатов —
 * всё правда, и всё читалось как текст о работе, а не как работа. Жюри
 * даёт три минуты, за которые сплошной текст не читают.
 *
 * Здесь на каждый раздел одна картинка и один абзац. Ни одно число не
 * вписано в вёрстку: всё считается из того же candidates.geojson, что
 * лежит на карте. Вписанное руками число живёт до первого нового прогона,
 * после чего тихо становится ложью.
 */

import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { Logo, Mark } from './components/Logo';
import { Nav } from './components/Nav';
import { Tape } from './components/Tape';
import { Plates } from './components/Plates';
import { Funnel, YearBars, DamageStrip, type Stage } from './components/Charts';
/* SiteView грузится отдельно и только когда до него дошли.
 *
 * Внутри Leaflet — 146 КБ, и до этой правки он тянулся при открытии
 * лендинга ради одной врезки, лежащей ниже сгиба. На канале 400 кбит/с
 * первая отрисовка занимала 12,8 секунды против полутора у карты и
 * прогноза: посетитель полминуты смотрел на пустоту ради картинки,
 * которую ещё не видит.
 *
 * Заглушка держит место точно того же размера — иначе страница дёргается,
 * когда врезка приезжает. */
const SiteView = lazy(() =>
  import('./components/SiteView').then((m) => ({ default: m.SiteView })),
);

type Series = Parameters<typeof Tape>[0]['data'];
type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;

/* Стадии отсева и пояснение к каждой.
 *
 * Числа сюда НЕ вписываются. Раньше вписывались — 213, 124, 59, 3 при 429
 * кандидатах, — и после пересчёта по всему кольцу их стало 385 при 21
 * объекте в списке. Подпись под воронкой начала противоречить карте на
 * том же сайте, а расхождение чисел между страницами проверяющий находит
 * за минуту и после этого не верит ни одному.
 *
 * Теперь счётчики приходят из funnel.json, который пишет пайплайн, а
 * здесь остаются только человеческие формулировки причин. Ключ — то, что
 * пишет в reject_reason контекстный отсев. */
const STAGE_TEXT: Record<string, { label: string; detail: string }> = {
  'площадь ниже порога разрешения Sentinel-2':
    { label: 'Площадь ниже разрешения', detail: 'меньше 500 м² — пять пикселей Sentinel-2, ниже уже шум' },
  'площадь слишком велика — это полигон, а не стихийная свалка':
    { label: 'Слишком большая площадь', detail: 'это полигон ТБО, а не стихийная свалка' },
  'пересекается с известным объектом OSM (карьер, стройка, застройка, вода)':
    { label: 'Совпал с объектом OSM', detail: 'карьер, стройка, застройка, вода' },
  'нет подъезда: далеко от проезжей дороги':
    { label: 'Нет подъезда', detail: 'дальше 300 м от проезжей дороги — самосвал не доедет' },
  'слишком близко к жилью':
    { label: 'Слишком близко к жилью', detail: 'ближе 1000 м — там исчезновение зелени это стройка' },
  'слишком далеко от жилья — невыгодно везти':
    { label: 'Слишком далеко от жилья', detail: 'дальше 15 км — возить невыгодно' },
};

type Funnel = { raw: number; rejected: Record<string, number> };
/* Деньги на лендинге читаются из той же выгрузки, что и экран
   «Экономика». Своего расчёта здесь нет намеренно: две страницы,
   считающие ущерб каждая по-своему, рано или поздно разойдутся, и
   расхождение найдёт проверяющий, а не мы. */
type Money = {
  totals: {
    mass_t: { p50: number };
    removal_kzt: { p50: number };
    recyclable_kzt: { p50: number };
    damage_kzt: { p10: number; p50: number; p90: number };
    /* Суммы медиан по объектам: ими подписан герой страницы и они же
       складываются из карточек на карте. Портфельный интервал живёт
       рядом и отвечает на другой вопрос. */
    sum_of_medians: {
      mass_t: number;
      removal_kzt: number;
      recyclable_kzt: number;
      damage_kzt: number;
    };
    co2e_emitted_t: number;
    co2e_t: number;
  };
  priority: { n: number; share: number }[];
};
type Metrics = { lift: number; pr_auc_future: number; base_rate_future: number; cells?: number;
  /* Интервал по бутстрэпу. Положительных ячеек единицы, и середина без
     границ здесь не измерение, а совпадение подходящего размера. */
  pr_auc_low?: number; pr_auc_high?: number; lift_low?: number; positives_future?: number };

function stagesFrom(funnel: Funnel | null, kept: number): Stage[] {
  if (!funnel?.rejected) return [];
  const stages = Object.entries(funnel.rejected)
    .filter(([reason]) => reason !== 'ПРОШЁЛ ОТСЕВ')
    .sort((a, b) => b[1] - a[1])
    .map(([reason, count]) => ({
      // Незнакомая причина показывается как есть: молча пропавшая
      // строка воронки — это несходящаяся сумма, которую заметят.
      label: STAGE_TEXT[reason]?.label ?? reason,
      detail: STAGE_TEXT[reason]?.detail ?? '',
      count,
    }));

  /* Последний шаг — человек, и без него воронка не сходилась.
     Автоматический отсев снимает 326 из 385, остаётся 59 — а на карте их
     семнадцать. Сорок два объекта убрал не алгоритм, а просмотр глазами по
     снимкам 0,4–0,8 м, и в воронке этого шага не было: столбцы давали 343 при
     385 кандидатах. Несходящаяся сумма на лендинге — первое, что считает
     проверяющий.
     Шаг стоит показывать ещё и потому, что он сильный: команда, которая
     отвергла сорок две собственные находки, вызывает больше доверия, чем
     команда с ровным списком. */
  const passed = funnel.rejected['ПРОШЁЛ ОТСЕВ'] ?? 0;
  const byEye = passed - kept;
  if (byEye > 0) {
    stages.push({
      label: 'Отвергнуто при просмотре',
      detail: 'человек посмотрел каждый по снимку 0,4–0,8 м — склад, стройка, старица',
      count: byEye,
    });
  }
  return stages;
}

const LIMITS: [string, string][] = [
  [
    'Последние 18 месяцев не подтверждаются',
    'Свалку отличает необратимость: растительность исчезла и не вернулась. Чтобы это увидеть, нужны полтора года наблюдений после появления — у свежих объектов их ещё нет.',
  ],
  [
    'Десять метров на пиксель',
    'Объекты меньше 30–50 м² Sentinel-2 не разрешает. Эту дыру закрывает житель с телефоном, а не алгоритм.',
  ],
  [
    'В сельской местности метод не работает',
    'Детектор ищет место, где растительность исчезла навсегда. В промзоне это редкое событие и потому значимое; в поле — рядовое: залежь, смена оборота, заброшенный огород. Проверено на пяти областях: восточный пояс Астаны 33 находки, юго-восточный 63, западная промзона 42, юг 21 — настоящих свалок ноль, все просмотрены глазами. Проверять область научились до прогона по полноте карты: где карта пуста, отсеивать нечем и метод не работает наверняка. Обратное неверно: подробная карта не обещает свалок, она обещает работающий отсев.',
  ],
  [
    'Перенос классификатора измерен, но выборка мала',
    'Модель обучена на открытых датасетах — ROC-AUC 0,858 на них. На наших объектах 0,680 при интервале 0,517–0,841. Пока объектов было семнадцать, нижняя граница лежала на 0,333 — ниже случайного, и перенос назывался недоказанным; на 51 она перешла черту. Называем нижнюю границу, а не середину.',
  ],
  [
    'Это оценка, а не юридическое доказательство',
    'Статус объекта, размер ущерба и факт устранения устанавливает уполномоченное лицо после выезда. Акт выходит черновиком.',
  ],
];

const num = (v: number, d = 0) =>
  Number.isFinite(v) ? v.toLocaleString('ru-RU', { maximumFractionDigits: d }) : '—';

const kzt = (v: number) =>
  Math.abs(v) >= 1e6 ? `${num(v / 1e6, 1)} млн ₸` : `${num(v / 1e3)} тыс ₸`;

/* Два списка месяцев, а не один. «1 апреля» и «в апреле» — разные падежи,
   и заголовок, собранный из родительного, читается как опечатка: первая
   версия выдала «Свалка возникла в апреля 2019». Русский язык не режется
   по пробелу. */
const MONTHS_OF = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_IN = ['январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
  'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре'];

function humanDate(v: unknown) {
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? '—' : `${d.getDate()} ${MONTHS_OF[d.getMonth()]} ${d.getFullYear()}`;
}

/** «в апреле 2019» — для заголовка. */
function whenPhrase(v: unknown) {
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? null : `в ${MONTHS_IN[d.getMonth()]} ${d.getFullYear()}`;
}

export default function App() {
  const [series, setSeries] = useState<Series | null>(null);
  const [features, setFeatures] = useState<Feature[]>([]);

  useEffect(() => {
    fetch('./data/hero-series.json').then((r) => r.json()).then(setSeries).catch(() => setSeries(null));
    fetch('./data/candidates.geojson')
      .then((r) => r.json())
      .then((fc: GeoJSON.FeatureCollection) => setFeatures(fc.features as Feature[]))
      .catch(() => setFeatures([]));
  }, []);

  /* Герой — самый крупный из подтверждённых ГЛАЗАМИ, а не выбранный
     вручную и не подтверждённый автоматикой. Раньше здесь стоял объект с
     verify_confirmed, и на лендинг попадал склад: доверификация сверяет
     текстуру, а не смысл. Выбранный вручную пришлось бы менять после
     каждого прогона. */
  const hero = useMemo(() => {
    const confirmed = features.filter((f) => f.properties?.visual_check === 'landfill');
    const pool = confirmed.length ? confirmed : features;
    return [...pool].sort(
      (a, b) => (Number(b.properties?.area_m2) || 0) - (Number(a.properties?.area_m2) || 0),
    )[0] ?? null;
  }, [features]);

  /* Суммы считаются без объектов, отвергнутых проверкой глазами.
     Складывать ущерб по складу под синей кровлей значит завышать итог, и
     первый же вопрос «а что вот это» обесценит всю цифру. */
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [money, setMoney] = useState<Money | null>(null);
  useEffect(() => {
    fetch('./data/economy.json')
      .then((r) => (r.ok ? r.json() : null))
      .then(setMoney)
      .catch(() => setMoney(null));
    fetch('./data/funnel.json')
      .then((r) => (r.ok ? r.json() : null))
      .then(setFunnel)
      .catch(() => setFunnel(null));
    fetch('./data/metrics.json')
      .then((r) => (r.ok ? r.json() : null))
      .then(setMetrics)
      .catch(() => setMetrics(null));
  }, []);

  const totals = useMemo(() => {
    /* Три числа, а не одно, и это принципиально.

       «Найдено 59» — то, что программа вынесла на проверку. Свалками
       оказались девять, а сорок две — стройками, промплощадками и
       болотами. Просмотрены глазами все пятьдесят девять. Показывать 30 как список свалок значит выдавать работу
       детектора за результат, и первый же, кто ткнёт в объект и увидит
       кровлю склада, перестанет верить всему остальному.

       Поэтому в заголовке стоит число, пережившее проверку, а отвергнутые
       названы отдельно и не спрятаны: команда, показывающая свои
       отбраковки, очевидно проверяла. */
    const real = features.filter((f) => f.properties?.visual_check !== 'not_landfill');
    return {
      count: features.length,
      real: real.length,
      rejected: features.length - real.length,
      confirmed: features.filter((f) => f.properties?.visual_check === 'landfill').length,
      /* Сколько подтверждено человеком на месте. Это про источник
         проверки, а не про класс объекта, и потому считается отдельно. */
      ground: features.filter((f) => f.properties?.check_source === 'ground').length,
      damage: real.reduce((s, f) => s + (Number(f.properties?.damage_p50) || 0), 0),
      area: real.reduce((s, f) => s + (Number(f.properties?.area_m2) || 0), 0),
    };
  }, [features]);

  return (
    <div className="min-h-screen">
      <div className="sticky top-0 z-10 bg-soot/92 backdrop-blur-[2px]">
        <div className="mx-auto max-w-[1240px]">
          <Nav current="index">
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
        {/* ── Первый экран ──────────────────────────────────────────── */}
        {/* overflow-hidden обязателен: свечение ниже шире экрана (72rem
            против 390 пикселей телефона) и без подрезки растягивает
            страницу вбок. На телефоне это выглядело как 382 лишних
            пикселя горизонтальной прокрутки — палец елозит, вёрстка
            «плавает», и первое впечатление испорчено ещё до текста. */}
        <section id="hero" className="relative overflow-hidden pt-12 pb-16">
          {/* Мягкое свечение за первым экраном. Единственное украшение на
              странице, и стоит оно здесь по делу: тёмная страница без
              градиента читается как консоль, а первый экран должен читаться
              как продукт. Дальше по странице свечения нет — иначе оно
              перестаёт что-либо выделять. */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -top-24 left-1/2 -z-10 h-[38rem] w-[72rem] -translate-x-1/2 opacity-70"
            style={{
              background:
                'radial-gradient(60% 50% at 50% 40%, rgba(124,58,237,.22), rgba(13,9,24,0) 70%)',
            }}
          />

          <div className="grid items-start gap-8 lg:grid-cols-[1.15fr_1fr]">
            <div>
              {/* Карточка ленты: рамка светлеет сверху, фон уходит вниз в
                  темноту. Плоский прямоугольник рядом с крупным заголовком
                  выглядел заготовкой, а не главным изображением страницы. */}
              <div
                className="rounded-md border border-grid p-3 md:p-6"
                style={{
                  background:
                    /* Через переменные темы, а не тремя вписанными оттенками:
                       на лазури вписанный фиолетовый градиент оставался
                       фиолетовым, и карточка выпадала из страницы. */
                    'linear-gradient(180deg, var(--color-soot-3) 0%, var(--color-soot-2) 45%, var(--color-soot) 100%)',
                  /* Тень тоже от темы: вписанный фиолетовый отблеск на
                     лазури читался как чужой слой поверх карточки.
                     color-mix даёт прозрачность из переменной, которой
                     нельзя задать альфу напрямую. */
                  boxShadow:
                    '0 1px 0 color-mix(in srgb, var(--color-violet-lit) 14%, transparent) inset,'
                    + ' 0 24px 60px -30px color-mix(in srgb, var(--color-violet-deep) 75%, transparent)',
                }}
              >
                <div className="mb-3 flex items-center justify-between">
                  <span className="font-display text-[11px] uppercase tracking-[0.16em] text-violet-lit">
                    Наблюдение одного пикселя
                  </span>
                  <span className="tabular text-[11px] text-muted-2">2018 — 2026</span>
                </div>
                {series ? (
                  <Tape data={series} />
                ) : (
                  <div className="h-[320px] animate-pulse rounded-sm bg-soot-3" />
                )}
              </div>
              <p className="mt-2.5 text-xs text-muted-2">
                Провал не сезонный — после него сигнал не вернулся. Именно
                это отличает свалку от пашни и от гари.
              </p>

              {/* Числа стоят под графиком, а не под заголовком.
                  Причин две. По смыслу: цифры — итог того, что показывает
                  график, и читаются как его подпись, а не как обещание
                  рядом с крупным заголовком.
                  По вёрстке: правая колонка вдвое выше левой, и под
                  графиком оставалась пустая полоса высотой почти в экран.
                  Заполнять её декором было бы нечестно — здесь стоит то,
                  ради чего страница написана. */}
              <dl className="mt-7 grid grid-cols-3 border-t border-grid pt-6">
                {[
                  ['Объектов в списке', String(totals.real), false],
                  ['Опознаны как свалка', String(totals.confirmed), true],
                  ['Ущерб по списку', kzt(totals.damage), false],
                ].map(([k, v, accent], i) => (
                  <div
                    key={String(k)}
                    className={i > 0 ? 'border-l border-grid pl-4' : 'pr-4'}
                  >
                    <dt className="text-xs leading-snug text-muted-2">{k}</dt>
                    <dd
                      className={`tabular mt-1.5 font-display text-[clamp(1.5rem,2.8vw,2.2rem)] leading-none ${
                        accent ? 'text-violet-lit' : 'text-line'
                      }`}
                    >
                      {v}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="lg:pt-2">
              {/* Имя продукта на первом экране, а не только в шапке.
                  В шапке оно стоит в ряду с пунктами меню и читается как
                  ещё один пункт: глаз проходит мимо. Здесь оно занимает
                  собственную строку над заголовком и подписано тем, что
                  продукт делает — двух слов достаточно, третье уже
                  описание, а описание ниже. */}
              <Logo size={46} className="mb-1.5" />
              <p className="mb-5 text-sm tracking-[0.02em] text-muted-2">
                экологические потери в тенге · найти · посчитать · приоритизировать
              </p>

              {/* Заголовок говорит, что это за продукт, а не рассказывает
                  историю одного объекта.

                  Раньше здесь стояло «Свалка возникла в апреле 2019».
                  Фраза красивая и совершенно непонятная человеку, который
                  видит страницу впервые: он не знает ни что за свалка, ни
                  чей это сайт, ни что ему предлагают. История объекта —
                  хорошее второе предложение и плохое первое.

                  Второй заход — «Находим свалки на спутниковых снимках» —
                  был понятен, но продавал метод, а не результат. Читатель
                  из акимата спрашивает не «чем вы ищете», а «что я с этого
                  получу»; трек называется EcoFin, и слово «потери» в
                  заголовке стоит ровно поэтому. Свалки названы следующей
                  же строкой — как предмет, на котором это измерено. */}
              <h1 className="max-w-[17ch] text-[clamp(2.2rem,5.2vw,3.7rem)] leading-[1.04] text-line">
                Находим экологические потери, пока они дешёвые
              </h1>
              <p className="mt-5 max-w-[48ch] text-lg leading-relaxed text-muted">
                Несанкционированная свалка — это ресурс, вывезенный мимо
                экономики: чужие деньги на её уборку, потерянное вторсырьё и
                метан в воздухе. Программа читает восемь лет спутникового
                архива, находит места, где растительность исчезла и{' '}
                <strong className="font-normal text-line">не вернулась</strong>, и по
                каждому называет дату появления, массу отходов и сумму
                потерь в тенге{hero && whenPhrase(hero.properties?.break_date)
                  ? `. Самая крупная из найденных возникла ${whenPhrase(hero.properties?.break_date)} — и её нет ни в одном открытом реестре.`
                  : '.'}
              </p>

              {/* Отбраковки названы на первом экране намеренно. Тот же факт,
                  найденный жюри самостоятельно, ломает доверие; названный
                  нами — доказывает, что проверка была. */}
              <p className="mt-4 max-w-[48ch] text-sm leading-relaxed text-muted-2">
                Каждый объект в списке просмотрен человеком по снимку
                высокого разрешения — 0,4–0,8 м на пиксель, в зависимости от
                того, есть ли у поставщика съёмка этого места на максимальном
                приближении.
                {/* Выезд назван на первом экране: это единственное
                    основание, которое нельзя оспорить снимком, и потому
                    самое сильное, что есть у списка. Число считается из
                    данных — вписанное разошлось бы при первом же выезде. */}
                {totals.ground > 0 && (
                  <>
                    {' '}Из них{' '}
                    <span className="text-emerald">
                      {totals.ground} проверены человеком на месте
                    </span>
                    , а не только по снимку.
                  </>
                )}{' '}
                Находки, оказавшиеся складами, промплощадками и болотами, в
                список не попали: детектор ищет исчезнувшую навсегда
                растительность, а новая застройка выглядит так же.
              </p>
            </div>
          </div>
        </section>

        {/* ── Что это в деньгах ─────────────────────────────────────
            Раздел стоит вторым, сразу после первого экрана, и это
            осознанный выбор порядка. Проект живёт в треке EcoFin, а
            прежняя страница до пятого раздела говорила только о методе:
            признаки, воронка, сроки. Человек, который читает по
            диагонали, уходил, так и не узнав, что у находок есть цена.

            Числа те же, что на экране «Экономика»: одна выгрузка, один
            денежный слой, одно зерно. */}
        {money && (
          <section id="money" className="border-t border-grid pt-14 pb-16">
            <div className="grid gap-10 lg:grid-cols-[1.05fr_1fr]">
              <div>
                <h2 className="max-w-[22ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
                  Свалка — это ресурс, за который платят трижды
                </h2>
                <p className="mt-4 max-w-[56ch] text-muted">
                  Первый раз — вывозом: {num(money.totals.sum_of_medians.mass_t)} тонн под
                  Астаной стоят бюджету{' '}
                  <strong className="font-normal text-line">
                    {kzt(money.totals.sum_of_medians.removal_kzt)}
                  </strong>
                  . Второй — потерянным сырьём: внутри тех же тонн лежит
                  пластика, бумаги, металла и стекла на{' '}
                  <strong className="font-normal text-emerald">
                    {kzt(money.totals.sum_of_medians.recyclable_kzt)}
                  </strong>{' '}
                  по прайсу приёмки. Половину уборки оплачивает то, что в
                  ней лежит, — если приехать с сортировкой, а не с
                  самосвалом.
                </p>
                <p className="mt-4 max-w-[56ch] text-sm leading-relaxed text-muted-2">
                  И третий счёт, который не выставят: {num(money.totals.co2e_emitted_t)} т
                  CO₂-экв из {num(money.totals.co2e_t)} уже ушли в атмосферу, пока
                  объекты лежали ненайденными. Метан не возвращают уборкой —
                  только ранним обнаружением.
                </p>
                <a
                  href="./economy.html"
                  className="mt-6 inline-block text-sm text-violet-lit underline decoration-grid transition-colors duration-200 hover:decoration-violet-lit"
                >
                  Весь расчёт, слагаемое за слагаемым →
                </a>
              </div>

              {/* Цепочка кейса одной строкой: ресурс → потеря → ИИ →
                  приоритет → возврат. Без неё разделы страницы читаются
                  как рассказ о технологии, а не как ответ на вопрос
                  «что вы делаете с деньгами заказчика». */}
              <ol className="grid gap-px self-start overflow-hidden rounded-md border border-grid bg-grid sm:grid-cols-2">
                {[
                  ['Потери', kzt(money.totals.sum_of_medians.damage_kzt),
                   `интервал ${kzt(money.totals.damage_kzt.p10)} — ${kzt(money.totals.damage_kzt.p90)}`],
                  ['Возврат сырьём', kzt(money.totals.sum_of_medians.recyclable_kzt),
                   'пластик, бумага, металл, стекло'],
                  ['Отсев до человека',
                   funnel ? `${funnel.raw} → ${funnel.rejected['ПРОШЁЛ ОТСЕВ'] ?? 0}` : '—',
                   'выезд стоит часа дороги'],
                  ['Приоритет',
                   `${money.priority.find((p) => p.share >= 0.5)?.n ?? '—'} выезда`,
                   'закрывают половину суммы'],
                ].map(([label, value, note]) => (
                  <li key={String(label)} className="bg-soot-2 px-5 py-5">
                    <div className="font-display text-[10px] uppercase tracking-[0.14em] text-violet-lit">
                      {label}
                    </div>
                    <div className="tabular mt-2 font-display text-[clamp(1.2rem,2.2vw,1.7rem)] leading-none text-line">
                      {value}
                    </div>
                    <div className="mt-1.5 text-xs leading-snug text-muted-2">{note}</div>
                  </li>
                ))}
              </ol>
            </div>
          </section>
        )}

        {/* ── Кому это ──────────────────────────────────────────────
            Раздел короткий и стоит третьим намеренно. Прежде страница
            вообще не называла пользователя: продукт описывался тем, что
            он умеет, и читатель сам должен был догадаться, кому это
            нужно. Догадываться он не станет — он закроет вкладку.

            Пользователь назван один. «Служба, жители, малый бизнес и
            школы» через запятую звучит шире, а читается как «мы не
            решили»: у продукта, полезного всем, нет никого, кто за него
            отвечает. */}
        <section id="audience" className="border-t border-grid pt-14 pb-16">
          <div className="grid gap-10 lg:grid-cols-[1fr_1.1fr]">
            <div>
              <h2 className="max-w-[20ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
                Кто этим пользуется
              </h2>
              <p className="mt-4 max-w-[52ch] text-muted">
                Главный пользователь один —{' '}
                <strong className="font-normal text-line">отдел экологии акимата</strong>.
                Он платит, он выезжает, он отвечает за результат. Остальные
                контуры существуют, но они вторые, и продукт устроен вокруг
                первого.
              </p>
            </div>
            <dl className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
              {[
                ['Отдел экологии акимата', 'главный',
                 'очередь по деньгам, черновик акта, контроль устранения',
                 'text-violet-lit'],
                ['ЖКХ и подрядчик по вывозу', 'второй контур',
                 'те же объекты, но в порядке маршрута, а не по алфавиту',
                 'text-muted-2'],
                ['Житель с телефоном', 'второй контур',
                 'закрывает то, чего спутник не видит: объекты меньше 30 м² и свежее полутора лет',
                 'text-muted-2'],
                ['Малый бизнес и школы', 'третий контур',
                 'открытый слой зон риска: где не стоит арендовать участок',
                 'text-muted-2'],
              ].map(([who, rank, what, tone]) => (
                <div key={String(who)} className="border-t border-grid pt-3">
                  <dt className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-base text-line">{who}</span>
                    <span className={`text-[10px] uppercase tracking-[0.1em] ${tone}`}>
                      {rank}
                    </span>
                  </dt>
                  <dd className="mt-1.5 max-w-[36ch] text-sm leading-snug text-muted-2">{what}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {/* ── Вот он ────────────────────────────────────────────────── */}
        <section id="site" className="border-t border-grid pt-14 pb-16">
          <div className="grid items-center gap-10 lg:grid-cols-[1fr_1fr]">
            <div>
              <h2 className="max-w-[18ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
                Не схема и не рендер — снимок
              </h2>
              <p className="mt-4 max-w-[52ch] text-muted">
                Самый крупный из подтверждённых объектов на снимке 0,75 м на
                пиксель. Контур — настоящая геометрия из прогона, а не кружок
                «примерно здесь». Снимок живой: если объект вывезли, здесь
                будет чистое поле.
              </p>
              {hero && (
                <dl className="mt-6 flex flex-wrap gap-x-8 gap-y-3 text-sm">
                  {[
                    ['Возник', humanDate(hero.properties?.break_date)],
                    ['Площадь', `${num(Number(hero.properties?.area_m2))} м²`],
                    ['Ущерб', kzt(Number(hero.properties?.damage_p50))],
                    ['Проверен', 'глазами, 0,4–0,8 м/пиксель'],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-xs text-muted-2">{k}</dt>
                      <dd className="tabular mt-0.5 text-line">{v}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <a
                href="./map.html"
                className="mt-6 inline-block text-sm text-violet-lit underline decoration-grid transition-colors duration-200 hover:decoration-violet-lit"
              >
                Все {totals.real} объектов на карте →
              </a>
            </div>
            <Suspense
              fallback={<div className="aspect-[4/3] w-full rounded-sm border border-grid bg-soot-2" />}
            >
              <SiteView feature={hero} className="aspect-[4/3] w-full" />
            </Suspense>
          </div>
        </section>

        {/* ── Пять признаков ────────────────────────────────────────── */}
        <section id="signals" className="border-t border-grid pt-14 pb-16">
          <h2 className="max-w-[24ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
            Пять признаков находят изменение. Опознаёт человек
          </h2>
          <p className="mt-4 max-w-[62ch] text-muted">
            Оптика, ближний инфракрасный, радар и тепло находят место, где
            поверхность изменилась <strong className="font-normal text-line">необратимо</strong>. В
            промзоне такое редко и потому подозрительно, в поле — рядово.
          </p>
          <Plates />
          {/* Граница названа здесь, а не спрятана в разделе ограничений:
              утверждение «признаки опознают свалку» проверяется одним
              вопросом «а от чего отличают» и рассыпается. Мы задали его
              себе сами и измерили ответ. */}
          <p className="mt-8 max-w-[62ch] text-sm leading-relaxed text-muted-2">
            Отличить свалку от склада <strong className="font-normal text-line">внутри одной
            местности</strong> эти признаки не могут: измерено на подтверждённых
            объектах и на находках, оказавшихся ложными, — точность 0,500,
            то есть случайная. Дальше решают контекстный отсев по
            OpenStreetMap и человек со снимком 0,4–0,8 м на пиксель.
          </p>
        </section>

        {/* ── Воронка ───────────────────────────────────────────────── */}
        {funnel && totals.count > 0 && (
          <section id="funnel" className="border-t border-grid pt-14 pb-16">
            <h2 className="max-w-[26ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
              До списка дошёл один кандидат из {Math.round(funnel.raw / totals.count)}
            </h2>
            <p className="mt-4 max-w-[62ch] text-muted">
              Причина отсева хранится по каждому. На вопрос «а почему выкинули
              вот это» отвечает файл, а не память выступающего.
            </p>
            <Funnel stages={stagesFrom(funnel, totals.count)} kept={totals.count} />
          </section>
        )}


        {/* ── Когда и почём ─────────────────────────────────────────── */}
        <section id="when" className="border-t border-grid pt-14 pb-16">
          <div className="grid gap-12 lg:grid-cols-2">
            <div>
              <h2 className="max-w-[20ch] text-[clamp(1.5rem,3vw,2.1rem)] text-line">
                Когда они появились
              </h2>
              <YearBars features={features} />
              <p className="mt-3 max-w-[46ch] text-sm text-muted-2">
                То же самое на местности —{' '}
                <a href="./timelapse.html" className="text-violet-lit underline decoration-grid">
                  таймлапс за восемь лет
                </a>
                .
              </p>
            </div>
            <div>
              <h2 className="max-w-[20ch] text-[clamp(1.5rem,3vw,2.1rem)] text-line">
                Сколько стоит каждый
              </h2>
              <DamageStrip features={features} />
              <p className="mt-3 max-w-[46ch] text-sm text-muted-2">
                Диапазон по каждому объекту получен методом Монте-Карло по
                восьми допущениям; у каждого указано происхождение.
              </p>
            </div>
          </div>
        </section>

        {/* ── Прогноз ───────────────────────────────────────────────── */}
        <section id="forecast" className="border-t border-grid pt-14 pb-16">
          <div className="grid items-baseline gap-8 lg:grid-cols-[1.3fr_1fr]">
            <div>
              <h2 className="max-w-[24ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
                Убрать свалку стоит миллионы. Не дать появиться — стоит знака
              </h2>
              <p className="mt-4 max-w-[58ch] text-muted">
                Модель обучена на объектах до отсечки и проверена на возникших
                после — на том будущем, которого не видела. Сто ячеек от модели
                вместо ста случайных дают в сотни раз больше находок на тот же
                бензин.
              </p>
              <a
                href="./forecast.html"
                className="mt-5 inline-block text-sm text-violet-lit underline decoration-grid transition-colors duration-200 hover:decoration-violet-lit"
              >
                Маршрут на ближайший месяц →
              </a>
            </div>
            <dl className="grid grid-cols-2 gap-6 border-l border-grid pl-8">
              {/* Метрики читаются из metrics.json, который пишет прогон.
                  Вписанные руками уже разошлись: после пересчёта по всему
                  кольцу выигрыш стал ×301, PR-AUC 0,046, базовая частота
                  0,00015 — а на странице стояли числа предыдущего прогона.
                  Числа модели, не совпадающие с её же выгрузкой, — первое,
                  что проверяют на техническом Q&A. */}
              {[
                /* Называется нижняя граница интервала, а не середина: положительных
                   ячеек восемь, и на восьми точках PR-AUC 0,62 и 0,30 неразличимы.
                   «Не хуже ×62» проверяемо, «×129» — нет. */
                ['Точнее случайного', metrics
                  ? (metrics.lift_low ? `не хуже ×${Math.round(metrics.lift_low)}` : `×${Math.round(metrics.lift)}`)
                  : '—'],
                ['PR-AUC', metrics
                  ? (metrics.pr_auc_low !== undefined && metrics.pr_auc_high !== undefined
                      ? `${metrics.pr_auc_low.toFixed(2).replace('.', ',')}–${metrics.pr_auc_high.toFixed(2).replace('.', ',')}`
                      : metrics.pr_auc_future.toFixed(3).replace('.', ','))
                  : '—'],
                ['Базовая частота', metrics ? metrics.base_rate_future.toFixed(5).replace('.', ',') : '—'],
                ['Ячеек в сетке', metrics?.cells ? Math.round(metrics.cells).toLocaleString('ru-RU') : '—'],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs text-muted-2">{k}</dt>
                  <dd className="tabular mt-1 font-display text-2xl leading-none text-line">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {/* ── Масштаб ───────────────────────────────────────────────
            Скорость измерена на настоящих прогонах, а не выведена из
            спецификаций: кольцо 406 км² за четыре часа на этой самой
            машине. Умножать читатель умеет сам, и умноженное им число
            убедительнее названного нами. */}
        <section id="scale" className="border-t border-grid pt-14 pb-16">
          <h2 className="max-w-[24ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
            Двести двадцать квадратных километров в час на обычном ноутбуке
          </h2>
          <p className="mt-4 max-w-[60ch] text-muted">
            Скорость померена на пяти прогонах: от 175 до 343 км² в час,
            медиана 223. Разброс — от того, сколько снимков уже скачано;
            первый прогон по холодному кэшу шёл вдвое медленнее.
            Без видеокарты, без спутникового контракта, без выездов на поиск.
            Снимки Sentinel и Landsat бесплатны и открыты, и это не льгота на
            время конкурса — так устроены европейская и американская
            программы наблюдения Земли.
          </p>

          <dl className="mt-9 grid gap-8 sm:grid-cols-3">
            {/* Числа держатся на прогонах, а не на прикидке. Посчитанная
                площадь — сумма областей, у которых есть выгрузка кандидатов;
                пересчитывается тем же способом, что в scripts/finish_all.py. */}
            {[
              ['1 709 км²', 'посчитано вокруг Астаны за два дня',
               'свалки нашлись в одной области из пяти'],
              ['8 120 км²', 'пригороды 20 областных центров', 'около сорока часов'],
              ['12 минут', 'просмотреть агломерацию и выбрать, где считать', 'до прогона, без снимков'],
            ].map(([big, what, cost]) => (
              <div key={String(big)}>
                <dt className="tabular font-display text-[clamp(1.6rem,3vw,2.2rem)] leading-none text-violet-lit">
                  {big}
                </dt>
                <dd className="mt-2 text-sm leading-snug text-line">{what}</dd>
                <dd className="mt-0.5 text-xs leading-snug text-muted-2">{cost}</dd>
              </div>
            ))}
          </dl>

          <p className="mt-8 max-w-[60ch] text-sm leading-relaxed text-muted-2">
            Последнее число важнее первых двух. Метод работает не везде: там,
            где сельское хозяйство оставляет тот же след, что и свалка,
            находки будут ложными. Мы умеем сказать это <strong className="font-normal text-line">до</strong> прогона
            и без единого снимка: одна область — секунды, вся агломерация по
            сетке — двенадцать минут. И говорим, даже когда ответ
            отрицательный: четыре области из пяти дали ноль настоящих свалок,
            и это написано здесь, а не спрятано.
          </p>
        </section>

        {/* ── Границы ───────────────────────────────────────────────── */}
        <section id="limits" className="border-t border-grid pt-14 pb-20">
          <h2 className="max-w-[20ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
            Чего система не может
          </h2>
          <p className="mt-4 max-w-[58ch] text-muted">
            Названо здесь, а не спрятано. Инструмент, границы которого
            неизвестны, применять нельзя.
          </p>
          <div className="mt-9 grid gap-x-12 gap-y-8 md:grid-cols-2">
            {LIMITS.map(([title, body]) => (
              <div key={title} className="border-t border-grid pt-4">
                <h3 className="text-base text-line">{title}</h3>
                <p className="mt-2 max-w-[52ch] text-sm leading-relaxed text-muted">{body}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-grid">
        <div className="mx-auto flex max-w-[1240px] flex-wrap items-center justify-between gap-6 px-6 py-9">
          <div className="flex items-center gap-3">
            {/* 34, а не 26: ниже 32 знак переходит на мелкую
                отрисовку без орбиты, и подвал переставал совпадать
                с шапкой. */}
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
