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

  return (
    <button
      type="button"
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
      // Подпись говорит, что произойдёт, а не что сейчас: кнопка, которая
      // называет текущее состояние, читается наоборот примерно половиной
      // людей.
      title={next === 'azure' ? 'Переключить на лазурь' : 'Вернуть фиалковую'}
      aria-label={next === 'azure' ? 'Переключить на лазурь' : 'Вернуть фиалковую'}
      className={`flex shrink-0 cursor-pointer items-center gap-2 rounded-sm border border-grid px-2.5 py-1.5 text-xs text-muted transition-colors duration-150 hover:border-violet hover:text-line ${className}`}
    >
      <span
        aria-hidden="true"
        className="inline-block h-3 w-3 rounded-full border border-grid"
        style={{ background: next === 'azure' ? '#0a7ea4' : '#7c3aed' }}
      />
      <span className="hidden sm:inline">{next === 'azure' ? 'лазурь' : 'фиалка'}</span>
    </button>
  );
}
