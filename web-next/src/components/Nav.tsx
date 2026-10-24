/* Общая навигация четырёх поверхностей.
 *
 * Их именно четыре, и каждая отвечает на свой вопрос:
 *   лендинг    — что это и почему этому можно верить;
 *   карта      — куда ехать сегодня;
 *   таймлапс   — как это росло восемь лет;
 *   прогноз    — где появится в следующие двенадцать месяцев;
 *   жителям    — как прислать то, чего спутник не видит.
 *
 * Разделены они не ради количества страниц. Таймлапс и прогноз внутри
 * рабочей карты были бы двумя режимами одного экрана, и оба проигрывали
 * бы главному — списку объектов. Показывать разное разными экранами
 * честнее, чем прятать в переключатель.
 */

import { Logo } from './Logo';

export type Surface = 'index' | 'map' | 'timelapse' | 'forecast' | 'citizen' | 'label';

const LINKS: [Surface, string, string][] = [
  ['map', './map.html', 'Карта'],
  ['timelapse', './timelapse.html', 'Как росло'],
  ['forecast', './forecast.html', 'Прогноз'],
  ['citizen', './citizen.html', 'Жителям'],
  // Разметка — инструмент, а не витрина, и стоит в конце: жюри она не
  // нужна, а команде без неё не обучить сеть.
  ['label', './label.html', 'Разметка'],
];

export function Nav({ current, children }: { current: Surface; children?: React.ReactNode }) {
  return (
    // min-w-0 на контейнерах обязателен: элемент flex по умолчанию не
    // сжимается меньше своего содержимого, и пять ссылок в ряд распирали
    // страницу на 34 пикселя при ширине 390 — на всех страницах сразу.
    <header className="flex w-full shrink-0 flex-wrap items-center justify-between gap-x-8 gap-y-3 border-b border-grid px-4 py-3 sm:px-5">
      <div className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-2">
        <a href="./index.html" className="no-underline" aria-label="Vantage, на главную">
          <Logo size={26} />
        </a>
        <nav className="flex min-w-0 flex-wrap items-center gap-x-1 gap-y-1">
          {LINKS.map(([key, href, label]) => (
            <a
              key={key}
              href={href}
              aria-current={current === key ? 'page' : undefined}
              className={`whitespace-nowrap rounded-sm px-2.5 py-1.5 text-sm no-underline transition-colors duration-150 sm:px-3 ${
                current === key
                  ? 'bg-violet-deep/60 text-line'
                  : 'text-muted hover:bg-soot-2 hover:text-line'
              }`}
            >
              {label}
            </a>
          ))}
        </nav>
      </div>
      {children}
    </header>
  );
}
