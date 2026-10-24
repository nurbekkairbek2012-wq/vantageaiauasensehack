/* Гражданский контур: то, чего спутник не видит.
 *
 * Это не «ещё одна фича», а прямое следствие названного ограничения.
 * Sentinel-2 даёт десять метров на пиксель и не разрешает объекты меньше
 * 30–50 м²; последние восемнадцать месяцев система вообще не подтверждает,
 * потому что необратимость ещё не наблюдаема. Обе дыры закрывает человек,
 * который стоит рядом с кучей.
 *
 * Контур двусторонний, и это важнее самой отправки: житель шлёт точку,
 * служба получает оповещение, а система отвечает жителю, совпало ли это с
 * уже известным объектом. Односторонняя форма «сообщить о проблеме» не
 * работает нигде и никогда — потому что ответа на неё не приходит.
 */

import { useEffect, useState } from 'react';
import { Nav } from './components/Nav';

type Site = { telegram_bot?: string; telegram_link?: string; qr?: string };

const STEPS: [string, string][] = [
  [
    'Житель отправляет точку',
    'Одно сообщение в Telegram: геопозиция и, если хочется, фотография. Ни регистрации, ни формы, ни адреса — точка и есть адрес.',
  ],
  [
    'Система отвечает сразу',
    'Сверяет координату с уже найденными объектами и известными полигонами. Если объект знаком — так и пишет, с датой обнаружения. Если нет — заводит новый и говорит об этом.',
  ],
  [
    'Служба получает оповещение',
    'Подписанным chat_id уходит карточка: координаты, статус, совпадение с реестром. Не письмо на почту, которое прочитают в понедельник.',
  ],
  [
    'Отправитель остаётся анонимным',
    'Идентификатор отправителя хешируется с солью и в отчёт не попадает. Жалоба на свалку рядом с чьим-то забором не должна становиться доносом с именем.',
  ],
];

export default function Citizen() {
  const [site, setSite] = useState<Site | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch('./data/site.json')
      .then((r) => (r.ok ? r.json() : null))
      .then(setSite)
      .catch(() => setSite(null))
      .finally(() => setLoaded(true));
  }, []);

  const link = site?.telegram_link;

  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="citizen">
        <p className="max-w-[44ch] text-sm text-muted-2">
          Спутник не видит объекты меньше 30 м² и не подтверждает свежие.
          Человек, стоящий рядом, видит и то и другое.
        </p>
      </Nav>

      <main className="mx-auto w-full max-w-[1240px] px-6 py-14">
        <div className="grid gap-x-16 gap-y-12 lg:grid-cols-[1fr_auto]">
          <div>
            <h1 className="max-w-[18ch] text-[clamp(2.2rem,5vw,4rem)] text-line">
              Житель видит то, чего не видит спутник
            </h1>
            <p className="mt-6 max-w-[60ch] text-lg leading-relaxed text-muted">
              Десять метров на пиксель — это физика прибора, а не недоработка.
              Куча в тридцать квадратов на неё не попадёт никогда. Поэтому у
              системы есть второй вход, и он в кармане у каждого.
            </p>
          </div>

          {/* ── Вход в контур ────────────────────────────────────────── */}
          <div className="lg:justify-self-end">
            {!loaded ? null : link ? (
              <div className="flex flex-col items-center gap-4 rounded-sm border border-grid bg-soot-2 p-6">
                {site?.qr && (
                  <img
                    src={site.qr}
                    alt={`QR-код на Telegram-бота ${site.telegram_bot}`}
                    width={188}
                    height={188}
                    className="h-[188px] w-[188px] rounded-sm bg-paper p-2 [image-rendering:pixelated]"
                  />
                )}
                <a
                  href={link}
                  target="_blank"
                  rel="noopener"
                  className="rounded-sm bg-violet px-6 py-3 font-display text-sm font-semibold uppercase tracking-[0.12em] text-paper no-underline transition-colors duration-200 hover:bg-violet-lit hover:text-soot"
                >
                  Открыть бота
                </a>
                <p className="tabular text-xs text-muted-2">@{site?.telegram_bot}</p>
              </div>
            ) : (
              /* Кнопки на несуществующего бота быть не должно: она хуже её
                 отсутствия. Пока имя не задано — здесь инструкция. */
              <div className="max-w-[26rem] rounded-sm border border-grid bg-soot-2 p-6">
                <h2 className="text-lg text-line">Бот ещё не подключён к сайту</h2>
                <p className="mt-3 text-sm leading-relaxed text-muted">
                  Код бота готов и лежит в{' '}
                  <code className="text-muted-2">src/vantage/bot/</code>. Чтобы
                  здесь появились кнопка и QR, нужно имя, которое выдаёт
                  @BotFather:
                </p>
                <pre className="mt-4 overflow-x-auto rounded-sm border border-grid bg-soot px-3 py-2.5 text-xs leading-relaxed text-muted">
{`python scripts/make_bot_qr.py @имя_бота
cd web-next && npm run build`}
                </pre>
                <p className="mt-3 text-xs leading-snug text-muted-2">
                  Имя не подставлено заранее намеренно: выдуманная ссылка на
                  защите открывается в пустоту.
                </p>
              </div>
            )}
          </div>
        </div>

        <ol className="mt-16 border-t border-grid">
          {STEPS.map(([title, body], i) => (
            <li
              key={title}
              className="grid gap-x-8 gap-y-3 border-b border-grid py-7 md:grid-cols-[3rem_18rem_1fr] md:items-baseline"
            >
              <span className="tabular font-display text-2xl leading-none text-violet-lit">
                {i + 1}
              </span>
              <h2 className="text-lg text-line">{title}</h2>
              <p className="max-w-[62ch] text-muted">{body}</p>
            </li>
          ))}
        </ol>

        <section className="mt-14 grid gap-x-14 gap-y-10 md:grid-cols-2">
          <div>
            <h2 className="text-xl text-line">Что бот умеет прямо сейчас</h2>
            <dl className="mt-4 space-y-3 text-sm">
              {[
                ['/start', 'коротко объясняет, что делать'],
                ['/help', 'как отправить точку и что будет дальше'],
                ['/stats', 'сколько сообщений пришло, сколько подтвердили известное, сколько нашли нового'],
                ['геопозиция', 'заводит обращение и отвечает, знаком ли объект'],
                ['фото без точки', 'просит прислать геопозицию: фотография без координат бесполезна'],
              ].map(([cmd, what]) => (
                <div key={cmd} className="flex flex-wrap items-baseline gap-x-3 border-b border-grid pb-3">
                  <dt className="tabular font-display text-sm text-line">{cmd}</dt>
                  <dd className="flex-1 text-muted-2">{what}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div>
            <h2 className="text-xl text-line">Чего он ещё не делает</h2>
            <p className="mt-4 max-w-[52ch] text-muted">
              Обращения жителей пока не попадают на карту отдельным слоем: для
              этого бот должен быть развёрнут и накопить хотя бы одно
              сообщение. Код приёма и хранения готов, развёртывание —{' '}
              <code className="text-muted-2">deploy/setup-bot.ps1</code>.
            </p>
            <p className="mt-4 max-w-[52ch] text-muted-2">
              До этого момента здесь нечего показывать, и рисовать
              несуществующие обращения на карте мы не будем.
            </p>
          </div>
        </section>
      </main>
    </div>
  );
}
