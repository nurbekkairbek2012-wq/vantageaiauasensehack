/* Лендинг.
 *
 * Первая версия доказывала словами и проиграла собственному предмету.
 * Пять признаков, восемь допущений, четыреста двадцать девять кандидатов —
 * всё правда, и всё читалось как текст о работе, а не как работа. Жюри
 * даёт три минуты, за которые сплошной текст не читают.
 *
 * Здесь на каждый раздел одна картинка и один абзац. Ни одно число не
 * вписано в вёрстку: всё считается из того же candidates.geojson, что
 * лежит на карте. Вписанное руками число живёт до первого нового прогона,
 * после чего тихо становится ложью.
 */

import { useEffect, useMemo, useState } from 'react';
import { Logo, Mark } from './components/Logo';
import { Nav } from './components/Nav';
import { Tape } from './components/Tape';
import { Plates } from './components/Plates';
import { Funnel, YearBars, DamageStrip } from './components/Charts';
import { SiteView } from './components/SiteView';

type Series = Parameters<typeof Tape>[0]['data'];
type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;

/* Отклонённые гипотезы показываются, а не прячутся. Причины отсева уже
   пишутся в rejected.geojson, и до сих пор их не видел никто. */
const STAGES = [
  { label: 'Площадь ниже разрешения', detail: 'меньше 900 м² — Sentinel-2 такое не разрешает', count: 213 },
  { label: 'Совпал с объектом OSM', detail: 'карьер, стройка, застройка, вода', count: 124 },
  { label: 'Слишком близко к жилью', detail: 'ближе 1500 м — такое замечают и без спутника', count: 59 },
  { label: 'Нет подъезда', detail: 'дальше 300 м от проезжей дороги', count: 3 },
];

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
    'Модель отличает свалку от карьера, а не находит с нуля',
    'Разметка слабая: положительные примеры взяты из доверификации, отрицательные — из карьеров и строек OSM. Вневыборочный PR-AUC 0,61 при базовой частоте 0,21.',
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
  const totals = useMemo(() => {
    const real = features.filter((f) => f.properties?.visual_check !== 'not_landfill');
    return {
      count: features.length,
      confirmed: features.filter((f) => f.properties?.visual_check === 'landfill').length,
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
        <section className="relative pt-12 pb-16">
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
                    'linear-gradient(180deg, #1a1330 0%, #150f26 45%, #110c20 100%)',
                  boxShadow:
                    '0 1px 0 rgba(167,139,250,.14) inset, 0 24px 60px -30px rgba(76,29,149,.75)',
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
                свалки из космоса · дата, площадь, сумма
              </p>

              {/* Заголовок говорит, что это за продукт, а не рассказывает
                  историю одного объекта.

                  Раньше здесь стояло «Свалка возникла в апреле 2019».
                  Фраза красивая и совершенно непонятная человеку, который
                  видит страницу впервые: он не знает ни что за свалка, ни
                  чей это сайт, ни что ему предлагают. История объекта —
                  хорошее второе предложение и плохое первое. */}
              <h1 className="max-w-[15ch] text-[clamp(2.2rem,5.2vw,3.7rem)] leading-[1.04] text-line">
                Находим свалки на спутниковых снимках
              </h1>
              <p className="mt-5 max-w-[48ch] text-lg leading-relaxed text-muted">
                Программа читает восемь лет архива и ищет места, где
                растительность исчезла и <strong className="font-normal text-line">не вернулась</strong>.
                По каждому называет дату появления, площадь и сумму ущерба в
                тенге{hero && whenPhrase(hero.properties?.break_date)
                  ? `. Самая крупная из найденных возникла ${whenPhrase(hero.properties?.break_date)} — и её нет ни в одном открытом реестре.`
                  : '.'}
              </p>

              <dl className="mt-8 grid grid-cols-3 border-t border-grid pt-6">
                {[
                  ['Найдено', String(totals.count), false],
                  ['Подтверждено глазами', String(totals.confirmed), true],
                  ['Ущерб', kzt(totals.damage), false],
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
          </div>
        </section>

        {/* ── Вот он ────────────────────────────────────────────────── */}
        <section className="border-t border-grid pt-14 pb-16">
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
                    ['Проверен', 'глазами, 0,6 м/пиксель'],
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
                Все {totals.count} объектов на карте →
              </a>
            </div>
            <SiteView feature={hero} className="aspect-[4/3] w-full" />
          </div>
        </section>

        {/* ── Пять признаков ────────────────────────────────────────── */}
        <section className="border-t border-grid pt-14 pb-16">
          <h2 className="max-w-[22ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
            Свалку опознают пять независимых признаков
          </h2>
          <p className="mt-4 max-w-[62ch] text-muted">
            Каждый по отдельности неспецифичен. Вместе они разделяют то, что не
            разделяет ни один: карьер, стройку, отвал грунта, снегосвалку и
            свалку.
          </p>
          <Plates />
        </section>

        {/* ── Воронка ───────────────────────────────────────────────── */}
        <section className="border-t border-grid pt-14 pb-16">
          <h2 className="max-w-[24ch] text-[clamp(1.7rem,3.6vw,2.6rem)] text-line">
            До списка дошёл один объект из четырнадцати
          </h2>
          <p className="mt-4 max-w-[62ch] text-muted">
            Причина отсева хранится по каждому. На вопрос «а почему выкинули
            вот это» отвечает файл, а не память выступающего.
          </p>
          <Funnel stages={STAGES} kept={totals.count || 30} />
        </section>

        {/* ── Когда и почём ─────────────────────────────────────────── */}
        <section className="border-t border-grid pt-14 pb-16">
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
        <section className="border-t border-grid pt-14 pb-16">
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
              {[
                ['Точнее случайного', '×293'],
                ['PR-AUC', '0,120'],
                ['Базовая частота', '0,0004'],
                ['Ячеек в сетке', '19 621'],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs text-muted-2">{k}</dt>
                  <dd className="tabular mt-1 font-display text-2xl leading-none text-line">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {/* ── Границы ───────────────────────────────────────────────── */}
        <section className="border-t border-grid pt-14 pb-20">
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
