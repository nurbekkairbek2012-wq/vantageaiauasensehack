/* Переключатель подложки. Отдельным файлом от констант: файл, который
 * экспортирует и компонент, и значения, ломает горячую перезагрузку —
 * Vite не может обновить его, не потеряв состояние.
 */

import { BASEMAPS, BASEMAP_KEYS, type Basemap } from './basemaps';

type SwitchProps = {
  value: Basemap;
  onChange: (next: Basemap) => void;
  className?: string;
};

export function BasemapSwitch({ value, onChange, className = '' }: SwitchProps) {
  return (
    <div
      className={`flex overflow-hidden rounded-sm border border-grid bg-soot/90 backdrop-blur-sm ${className}`}
      role="group"
      aria-label="Подложка карты"
    >
      {BASEMAP_KEYS.map((key) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          className={`cursor-pointer px-3 py-1.5 text-xs transition-colors duration-150 ${
            value === key ? 'bg-violet text-paper' : 'text-muted hover:bg-soot-2 hover:text-line'
          }`}
        >
          {BASEMAPS[key].label}
        </button>
      ))}
    </div>
  );
}
