/* Переключатель темы: фиалковая лента ↔ лазурь.
 *
 * ── Зачем вторая тема ──────────────────────────────────────────────
 *
 * Фиолетовая палитра закреплена брифом и права для лендинга: «архив,
 * плёнка, ночь». Но карту смотрят при дневном свете с проектора, где
 * фиалковая копоть садится в серое и перестаёт держать контраст. Лазурь
 * на плохом проекторе читается лучше.
 *
 * ── Как устроено ───────────────────────────────────────────────────
 *
 * Тема — это только набор цветовых переменных на корне документа. Ни один
 * компонент про неё не знает и знать не должен: как только тема протекает
 * в разметку, вторая тема перестаёт быть переключателем и становится
 * вторым сайтом, который надо чинить дважды.
 *
 * ── Почему выбор запоминается ──────────────────────────────────────
 *
 * Страниц шесть, и переход по ссылке — это полная перезагрузка. Без
 * запоминания тема слетала бы на каждом клике по меню, то есть работала
 * бы ровно один экран.
 *
 * ── Почему тема ставится до отрисовки ──────────────────────────────
 *
 * Скрипт в <head> (см. index.html) читает выбор и ставит атрибут до того,
 * как браузер что-либо покажет. Иначе на каждой загрузке мелькает
 * фиолетовый кадр, прежде чем React успеет переключить, — и это заметно
 * именно на проекторе, ради которого всё и делалось.
 */

import { useEffect, useState } from 'react';

export type Theme = 'violet' | 'azure';

const KEY = 'vantage-theme';

function read(): Theme {
  if (typeof document === 'undefined') return 'violet';
  const attr = document.documentElement.dataset.theme;
  if (attr === 'azure' || attr === 'violet') return attr;
  try {
    return localStorage.getItem(KEY) === 'azure' ? 'azure' : 'violet';
  } catch {
    // Приватный режим и запрет на хранилище — не повод падать.
    return 'violet';
  }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* пусто: выбор проживёт до перезагрузки, и это лучше падения */
  }
}

export function ThemeSwitch({ className = '' }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>('violet');

  // Читаем уже после монтирования: до него документа может не быть.
  useEffect(() => setTheme(read()), []);

  const next: Theme = theme === 'violet' ? 'azure' : 'violet';

  const azure = theme === 'azure';

  /* Тумблер, а не кнопка с подписью.
   *
   * У кнопки «лазурь» есть неустранимая двусмысленность: она называет либо
   * текущее состояние, либо то, что произойдёт при нажатии, и читается
   * наоборот примерно половиной людей. Тумблер снимает вопрос формой: видно
   * положение, а не название.
   *
   * Обе подписи стоят по краям и видны одновременно — подсвечена та, что
   * сейчас. Ползунок ездит между ними; ему же отдана роль образца цвета,
   * поэтому переключение показывает не только «сдвинулось», но и «во что».
   */
  return (
    <button
      type="button"
      role="switch"
      aria-checked={azure}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
      title={azure ? 'Вернуть фиалковую тему' : 'Переключить на лазурь'}
      aria-label={azure ? 'Вернуть фиалковую тему' : 'Переключить на лазурь'}
      className={`group relative flex h-8 shrink-0 cursor-pointer items-center gap-1 rounded-full border border-grid bg-soot-2 p-1 transition-colors duration-300 hover:border-violet ${className}`}
    >
      {/* Ползунок. Абсолютный, чтобы ехать поверх подписей, а не толкать их:
          при перестроении потоком соседние элементы дёргаются. */}
      <span
        aria-hidden="true"
        className="absolute top-1 bottom-1 left-1 rounded-full transition-transform duration-300 ease-out"
        style={{
          width: 'calc(50% - 0.25rem)',
          background: azure
            ? 'linear-gradient(140deg, #0a7ea4, #5fd0f5)'
            : 'linear-gradient(140deg, #4c1d95, #a78bfa)',
          // Тень того же тона: без неё ползунок читается наклейкой.
          boxShadow: azure
            ? '0 2px 10px -2px rgba(10,126,164,.65)'
            : '0 2px 10px -2px rgba(124,58,237,.65)',
          transform: azure ? 'translateX(100%)' : 'translateX(0)',
        }}
      />
      {(['фиалка', 'лазурь'] as const).map((label, i) => {
        const on = (i === 1) === azure;
        return (
          <span
            key={label}
            className={`relative z-10 w-[4.2rem] text-center text-[11px] leading-none transition-colors duration-300 ${
              on ? 'text-paper' : 'text-muted-2 group-hover:text-muted'
            }`}
          >
            {label}
          </span>
        );
      })}
    </button>
  );
}
