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

export type Surface = 'index' | 'map' | 'timelapse' | 'forecast' | 'citizen';

const LINKS: [Surface, string, string][] = [
  ['map', './map.html', 'Карта'],
  ['timelapse', './timelapse.html', 'Как росло'],
  ['forecast', './forecast.html', 'Прогноз'],
  ['citizen', './citizen.html', 'Жителям'],
];

export function Nav({ current, children }: { current: Surface; children?: React.ReactNode }) {
  return (
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-x-8 gap-y-3 border-b border-grid px-5 py-3">
      <div className="flex flex-wrap items-center gap-x-7 gap-y-2">
        <a href="./index.html" className="no-underline" aria-label="VANTAGE, на главную">
          <Logo size={26} />
        </a>
        <nav className="flex items-center gap-1">
          {LINKS.map(([key, href, label]) => (
            <a
              key={key}
              href={href}
              aria-current={current === key ? 'page' : undefined}
              className={`rounded-sm px-3 py-1.5 text-sm no-underline transition-colors duration-150 ${
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
