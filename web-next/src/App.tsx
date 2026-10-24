import { useEffect, useState } from 'react';
import { Mark } from './components/Logo';
import { Nav } from './components/Nav';
import { Tape } from './components/Tape';
import { Plates } from './components/Plates';

type Series = Parameters<typeof Tape>[0]['data'];

/* Отклонённые гипотезы. Дисциплина взята у отклонённого направления
   «событие в детекторе частиц»: конкурирующие версии показываются призрачными
   треками рядом с подтверждённой, а не прячутся. У нас это причины
   контекстного отсева — они уже пишутся в rejected.geojson и до сих пор не
   были видны никому. */
const rejected: [string, string, number][] = [
  ['Площадь ниже порога разрешения', 'меньше 900 м² — Sentinel-2 такое не разрешает', 213],
  ['Пересекается с известным объектом OSM', 'карьер, стройка, застройка, вода', 124],
  ['Слишком близко к жилью', 'ближе 1500 м — такое замечают и без спутника', 59],
  ['Нет подъезда', 'дальше 300 м от проезжей дороги', 3],
];

const RAW = 429;
const KEPT = 30;

const limits = [
  [
    'Последние полтора года система не подтверждает',
    'Свалку отличает необратимость: растительность исчезла и не вернулась. Чтобы это увидеть, нужно 18 месяцев наблюдений после появления объекта — у более свежих их ещё нет. Первый настоящий прогон это и показал: 25 кандидатов из 29 получили одну и ту же дату, последний месяц периода. Теперь такие разрывы отбрасываются на этапе поиска.',
  ],
  [
    'Десять метров на пиксель',
    'Объекты меньше 30–50 м² Sentinel-2 не разрешает. Это закрывает гражданский контур: житель видит то, чего не видит спутник.',
  ],
  [
    'Сеть не обучена на настоящих данных',
    'Положительные примеры берутся из полигонов ТБО в OpenStreetMap, но внутри существующего полигона детектор изменений ничего не находит: там и в 2018 году была голая поверхность. Поэтому вместо вероятности модели показывается согласие пяти физических признаков — и подписано именно так.',
  ],
  [
    'Это оценка вероятности, а не юридическое доказательство',
    'Решение о статусе объекта, размере ущерба и факте устранения принимает уполномоченное лицо после выезда. Акт выгружается черновиком и становится документом только после подтверждения именем и должностью.',
  ],
];

function Money() {
  return (
    <div className="mt-10 max-w-3xl">
      {/* Подписи привязаны к тем же процентам, что и концы полосы: раньше
          они стояли по краям контейнера и обещали не тот диапазон. */}
      <div className="relative h-6 text-sm text-muted-2 tabular">
        <span className="absolute left-[8%] -translate-x-1/2 whitespace-nowrap">P10 · 4,9 млн ₸</span>
        <span className="absolute left-[86%] -translate-x-1/2 whitespace-nowrap">P90 · 41 млн ₸</span>
      </div>
      <div className="relative mt-1 h-8">
        <div className="absolute inset-y-[14px] left-0 right-0 bg-grid" />
        <div className="absolute inset-y-[11px] left-[8%] right-[14%] bg-violet-deep" />
        <div className="absolute inset-y-0 left-[38%] w-[3px] bg-violet-lit" />
        <div className="absolute left-[38%] top-full mt-2 -translate-x-1/2 whitespace-nowrap font-display text-xl text-line tabular">
          19 млн ₸
        </div>
      </div>
      <p className="mt-12 max-w-[68ch] text-muted">
        Медианная оценка чистого ущерба по одному объекту площадью 1600 м².
        Диапазон получен методом Монте-Карло по восьми допущениям, у каждого
        указано происхождение: закон о бюджете, методика расчёта тарифа, прайс
        приёмщика вторсырья. Четыре величины — инженерная оценка, и они названы
        поимённо в отчёте, а не спрятаны в среднем.
      </p>
    </div>
  );
}

export default function App() {
  const [series, setSeries] = useState<Series | null>(null);

  useEffect(() => {
    fetch('./data/hero-series.json')
      .then((r) => r.json())
      .then(setSeries)
      .catch(() => setSeries(null));
  }, []);

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
        {/* Первый экран: сначала запись, потом слова. Изображение — подпись,
            текст лишь подтверждает то, что уже сказала лента. */}
        <section className="pt-14 pb-20">
          <div className="rounded-sm border border-grid bg-soot-2 p-3 md:p-9">
            {series ? (
              <Tape data={series} />
            ) : (
              <div className="h-[320px] animate-pulse rounded-sm bg-soot-3" />
            )}
          </div>

          <h1 className="mt-12 max-w-[16ch] text-[clamp(2.6rem,7vw,5.2rem)] text-line">
            Свалка возникла в октябре 2024
          </h1>
          <p className="mt-6 max-w-[62ch] text-xl leading-relaxed text-muted">
            Её нет ни в одном открытом реестре. Спутник видел, как это
            произошло, — восемь лет наблюдений лежали в архиве и ждали, пока
            кто-нибудь их прочитает. VANTAGE читает их по всей области и
            называет дату, площадь и сумму.
          </p>
        </section>

        <section className="border-t border-grid pt-16 pb-20">
          <h2 className="max-w-[22ch] text-[clamp(1.9rem,4vw,3rem)] text-line">
            Свалку опознают пять независимых признаков, а не нейросеть на
            картинке
          </h2>
          <p className="mt-5 max-w-[68ch] text-muted">
            Каждый по отдельности неспецифичен. Вместе они разделяют то, что не
            разделяет ни один: карьер, стройку, отвал грунта, снегосвалку и
            свалку. Модель не выдаёт вердикт — она выдаёт цепочку с весом
            каждого признака.
          </p>
          <Plates />
        </section>

        <section className="border-t border-grid pt-16 pb-20">
          <h2 className="max-w-[24ch] text-[clamp(1.9rem,4vw,3rem)] text-line">
            Что система отвергла и почему
          </h2>
          <p className="mt-5 max-w-[68ch] text-muted">
            Прогон по кольцу 20×20 км к северу от Астаны нашёл{' '}
            <span className="tabular text-line">{RAW}</span> объектов. До списка
            дошли <span className="tabular text-line">{KEPT}</span>. Причина
            отсева хранится по каждому: на вопрос «а почему вы выкинули вот это»
            отвечает файл, а не память выступающего.
          </p>
          <dl className="mt-10 max-w-4xl">
            {rejected.map(([reason, detail, count]) => (
              <div
                key={reason}
                className="grid grid-cols-[1fr_auto] items-baseline gap-x-6 border-b border-grid py-4 md:grid-cols-[20rem_1fr_auto]"
              >
                <dt className="text-line">{reason}</dt>
                <dd className="col-span-2 text-muted-2 md:col-span-1">{detail}</dd>
                <dd className="tabular text-right font-display text-xl text-violet-lit md:col-start-3 md:row-start-1">
                  {count}
                </dd>
              </div>
            ))}
            <div className="grid grid-cols-[1fr_auto] items-baseline gap-x-6 py-4 md:grid-cols-[20rem_1fr_auto]">
              <dt className="text-line">Прошли отсев</dt>
              <dd className="col-span-2 text-muted-2 md:col-span-1">
                поехать можно по каждому
              </dd>
              <dd className="tabular text-right font-display text-xl text-line md:col-start-3 md:row-start-1">
                {KEPT}
              </dd>
            </div>
          </dl>
        </section>

        <section className="border-t border-grid pt-16 pb-20">
          <h2 className="max-w-[22ch] text-[clamp(1.9rem,4vw,3rem)] text-line">
            Ущерб называется диапазоном, потому что честная цифра — диапазон
          </h2>
          <Money />
        </section>

        <section className="border-t border-grid pt-16 pb-20">
          <h2 className="max-w-[26ch] text-[clamp(1.9rem,4vw,3rem)] text-line">
            Убрать свалку стоит миллионы. Не дать ей появиться — стоит знака
          </h2>
          <p className="mt-5 max-w-[68ch] text-muted">
            Модель прогноза обучена на объектах, возникших до отсечки, и
            проверена на возникших после — то есть на том будущем, которого она
            не видела. На сетке 500 м по кольцу она попадает в{' '}
            <span className="tabular text-line">293</span> раза точнее
            случайного выбора: PR-AUC{' '}
            <span className="tabular text-line">0,120</span> при базовой частоте{' '}
            <span className="tabular text-line">0,0004</span>. Это измерено на
            настоящем прогоне, а не заявлено.
          </p>
          <p className="mt-4 max-w-[68ch] text-muted-2">
            Прямое чтение: если объехать сто ячеек, отобранных моделью, вместо
            ста случайных, свалок найдётся в сотни раз больше на тот же бензин.
          </p>
        </section>

        <section className="border-t border-grid pt-16 pb-24">
          <h2 className="max-w-[20ch] text-[clamp(1.9rem,4vw,3rem)] text-line">
            Чего система не может
          </h2>
          <p className="mt-5 max-w-[68ch] text-muted">
            Названо здесь, а не спрятано. Инструмент, границы которого
            неизвестны, применять нельзя.
          </p>
          <div className="mt-10 grid gap-x-14 gap-y-10 md:grid-cols-2">
            {limits.map(([title, body]) => (
              <div key={title}>
                <h3 className="text-lg text-line">{title}</h3>
                <p className="mt-3 max-w-[52ch] text-muted">{body}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-grid">
        <div className="mx-auto flex max-w-[1240px] flex-wrap items-center justify-between gap-6 px-6 py-10">
          <div className="flex items-center gap-3">
            <Mark size={26} />
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
