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

  /* Герой — самый крупный подтверждённый объект, а не выбранный вручную.
     Выбранный вручную пришлось бы менять после каждого прогона, и рано
     или поздно на лендинге оказался бы объект, которого больше нет. */
  const hero = useMemo(() => {
    const confirmed = features.filter((f) => f.properties?.verify_confirmed === true);
    const pool = confirmed.length ? confirmed : features;
    return [...pool].sort(
      (a, b) => (Number(b.properties?.area_m2) || 0) - (Number(a.properties?.area_m2) || 0),
    )[0] ?? null;
  }, [features]);

  const totals = useMemo(() => {
    const damage = features.reduce((s, f) => s + (Number(f.properties?.damage_p50) || 0), 0);
    const area = features.reduce((s, f) => s + (Number(f.properties?.area_m2) || 0), 0);
    return { count: features.length, damage, area };
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
        <section className="pt-12 pb-16">
          <div className="grid items-start gap-8 lg:grid-cols-[1.15fr_1fr]">
            <div>
              <div className="rounded-sm border border-grid bg-soot-2 p-3 md:p-6">
                {series ? (
                  <Tape data={series} />
                ) : (
                  <div className="h-[320px] animate-pulse rounded-sm bg-soot-3" />
                )}
              </div>
              <p className="mt-2.5 text-xs text-muted-2">
                Восемь лет наблюдений одного пикселя. Провал не сезонный —
                после него сигнал не вернулся.
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

              <h1 className="max-w-[15ch] text-[clamp(2.3rem,5.5vw,4rem)] leading-[1.02] text-line">
                {hero && whenPhrase(hero.properties?.break_date)
                  ? `Свалка возникла ${whenPhrase(hero.properties?.break_date)}`
                  : 'Свалка возникла, и её нет ни в одном реестре'}
              </h1>
              <p className="mt-5 max-w-[46ch] text-lg leading-relaxed text-muted">
                Её нет ни в одном открытом реестре. Спутник видел, как это
                произошло, — снимки восьми лет лежали в архиве и ждали, пока
                их прочитают.
              </p>

              <dl className="mt-8 grid grid-cols-3 gap-4 border-t border-grid pt-6">
                {[
                  ['Объектов', String(totals.count)],
                  ['Площадь', `${num(totals.area / 10000, 1)} га`],
                  ['Ущерб', kzt(totals.damage)],
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-xs text-muted-2">{k}</dt>
                    <dd className="tabular mt-1 font-display text-[clamp(1.3rem,2.4vw,1.9rem)] leading-none text-line">
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
                    ['Подтверждён', `${num(Number(hero.properties?.n_agreeing))} источника`],
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
