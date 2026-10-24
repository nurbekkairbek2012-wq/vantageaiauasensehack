/* Выбор города на карте.
 *
 * Стоит под переключателем подложки и работает так же: нажал — карта
 * перелетела. Не фильтр и не поиск, а именно перелёт: объекты всех городов
 * лежат на одной карте, и отсекать их при выборе было бы неправильно —
 * человек, который отдалит карту, должен увидеть всю страну.
 *
 * Область может быть в трёх состояниях, и это не одно и то же:
 *
 *   found    — есть объекты, кнопка обычная
 *   empty    — прогон прошёл, настоящих свалок не нашлось
 *   pending  — прогон не запускался
 *
 * Раньше «empty» и «pending» выглядели одинаково — «0» на кнопке и
 * подсказка «прогон ещё не проходил». Про восточный пояс это была
 * неправда: он считался четыре часа и просматривался час, 33 находки, ни
 * одной настоящей свалки.
 *
 * Разница важна для защиты. «Не проверяли» — дыра в охвате. «Проверили,
 * чисто» — работающая система: она умеет говорить не только «здесь
 * свалка», но и «здесь ничего нет», а без второго первое ничего не стоит.
 *
 * Кнопка проверенной пустой области не нажимается — вести на пустую карту
 * незачем, — но подписана она своим результатом, а не прочерком.
 */

export type City = {
  id: string;
  name: string;
  /** Короткая подпись для кнопки. Полное имя уходит в подсказку:
   *  «Астана · юго-восток» на телефоне рвётся на три строки и ломает
   *  ряд, а различать области достаточно одним словом. */
  short?: string;
  center: [number, number];
  zoom: number;
  /** Сколько объектов опубликовано. */
  count: number;
  /** Сколько находок дошло до просмотра глазами. */
  reviewed?: number;
  /** found — есть объекты; empty — проверено и чисто; pending — не считалось. */
  state?: 'found' | 'empty' | 'pending';
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
      className={`flex max-w-[calc(100vw-2rem)] overflow-x-auto rounded-sm border border-grid bg-soot/90 backdrop-blur-sm ${className}`}
      role="group"
      aria-label="Город"
    >
      {cities.map((city) => {
        const ready = city.count > 0;
        // Состояние может не прийти: старая выгрузка знает только count.
        const state = city.state ?? (ready ? 'found' : city.reviewed ? 'empty' : 'pending');
        const hint =
          state === 'found'
            ? `${city.name}: найдено ${city.count}`
            : state === 'empty'
              ? `${city.name}: проверено ${city.reviewed} находок, настоящих свалок нет`
              : `${city.name}: прогон ещё не проходил`;
        return (
          <button
            key={city.id}
            type="button"
            disabled={!ready}
            onClick={() => onSelect(city)}
            aria-pressed={current === city.id}
            title={hint}
            className={`shrink-0 cursor-pointer whitespace-nowrap px-3 py-1.5 text-xs transition-colors duration-150 ${
              current === city.id
                ? 'bg-violet text-paper'
                : ready
                  ? 'text-muted hover:bg-soot-2 hover:text-line'
                  : 'cursor-not-allowed text-muted-2/45'
            }`}
          >
            {city.short ?? city.name}
            <span className="tabular ml-1.5 opacity-70">
              {state === 'found' ? city.count : state === 'empty' ? 'чисто' : ''}
            </span>
          </button>
        );
      })}
    </div>
  );
}
