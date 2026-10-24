/* Выбор города на карте.
 *
 * Стоит под переключателем подложки и работает так же: нажал — карта
 * перелетела. Не фильтр и не поиск, а именно перелёт: объекты всех городов
 * лежат на одной карте, и отсекать их при выборе было бы неправильно —
 * человек, который отдалит карту, должен увидеть всю страну.
 *
 * Города, по которым прогон ещё не прошёл, показываются серыми и не
 * нажимаются. Это честнее, чем скрывать их: видно, что охват шире одного
 * города и куда он растёт. Кнопка, которая ведёт на пустую карту, хуже
 * неактивной.
 */

export type City = {
  id: string;
  name: string;
  center: [number, number];
  zoom: number;
  /** Сколько объектов найдено. Ноль означает «прогон не проходил». */
  count: number;
};

type Props = {
  cities: City[];
  current: string | null;
  onSelect: (city: City) => void;
  className?: string;
};

export function CitySwitch({ cities, current, onSelect, className = '' }: Props) {
  if (cities.length < 2) return null;

  return (
    <div
      className={`flex overflow-hidden rounded-sm border border-grid bg-soot/90 backdrop-blur-sm ${className}`}
      role="group"
      aria-label="Город"
    >
      {cities.map((city) => {
        const ready = city.count > 0;
        return (
          <button
            key={city.id}
            type="button"
            disabled={!ready}
            onClick={() => onSelect(city)}
            aria-pressed={current === city.id}
            title={ready ? `${city.name}: найдено ${city.count}` : `${city.name}: прогон ещё не проходил`}
            className={`cursor-pointer px-3 py-1.5 text-xs transition-colors duration-150 ${
              current === city.id
                ? 'bg-violet text-paper'
                : ready
                  ? 'text-muted hover:bg-soot-2 hover:text-line'
                  : 'cursor-not-allowed text-muted-2/45'
            }`}
          >
            {city.name}
            {ready && (
              <span className="tabular ml-1.5 opacity-70">{city.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
