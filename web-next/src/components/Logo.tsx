/* Знак VANTAGE.
 *
 * Знак выведен из механизма, а не из темы: это запись прибора, которая идёт
 * ровно, срывается вниз и **не возвращается**. Необратимость — единственный
 * признак, отличающий свалку от засухи, пожара и уборки урожая, и именно
 * она нарисована.
 *
 * Что сознательно не нарисовано: спутник, планета, глобус, лист, мусорный
 * бак и галочка. Всё это иллюстрирует тему, а не работу.
 *
 * ── Почему знака два ────────────────────────────────────────────────
 *
 * Первая версия была одна на все размеры и состояла из одного излома.
 * В шапке она читалась, но не говорила ничего сверх «линия вниз».
 *
 * Здесь у знака две отрисовки, и переключаются они по размеру, а не по
 * вкусу. В крупном размере видно то, что и составляет метод: отсчёты
 * наблюдений точками, момент разрыва кольцом и **пунктир на прежнем
 * уровне** — линия, куда сигнал вернулся бы, будь это сезонное падение.
 * Разрыв между пунктиром и нижней полкой и есть предмет продукта.
 *
 * Ниже 26 px эта деталь превращается в грязь: пунктир с шагом в пиксель
 * сливается в серое, кольцо в 1,5 px — в точку. Поэтому на мелких размерах
 * остаётся один излом, и это не упрощение ради упрощения, а единственная
 * часть, которая на таком размере остаётся правдой.
 */

type Props = { size?: number; className?: string };

/** Ниже этого размера деталь перестаёт читаться и начинает мешать. */
const DETAIL_FROM = 26;

export function Mark({ size = 32, className = '' }: Props) {
  const detailed = size >= DETAIL_FROM;
  const uid = `vg${Math.round(size)}`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`${uid}-bg`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--color-violet-lit)" stopOpacity="0.28" />
          <stop offset="1" stopColor="var(--color-violet-deep)" stopOpacity="0.35" />
        </linearGradient>
      </defs>

      <rect x="0.5" y="0.5" width="31" height="31" rx="7" fill="var(--color-violet)" />
      <rect x="0.5" y="0.5" width="31" height="31" rx="7" fill={`url(#${uid}-bg)`} />

      {detailed ? (
        <>
          {/* Уровень, на который сигнал не вернулся. Пунктир, потому что
              это не наблюдение, а несбывшееся ожидание. */}
          <path
            d="M13 10.5H28"
            stroke="var(--color-paper)"
            strokeOpacity="0.42"
            strokeWidth="1.4"
            strokeDasharray="2.2 2.2"
            strokeLinecap="round"
          />

          {/* Сама запись: ровный ход, срыв, новая полка. */}
          <path
            d="M4 10.5H13V21.5H28"
            stroke="var(--color-paper)"
            strokeWidth="2.6"
            strokeLinecap="round"
            strokeLinejoin="miter"
            fill="none"
          />

          {/* Отсчёты наблюдений: сигнал не непрерывен, он снимается датами. */}
          {[6.4, 9.7].map((x) => (
            <circle key={x} cx={x} cy="10.5" r="1.5" fill="var(--color-violet)" />
          ))}
          {[19.4, 24.6].map((x) => (
            <circle key={x} cx={x} cy="21.5" r="1.5" fill="var(--color-violet)" />
          ))}

          {/* Момент разрыва — единственная точка, которую датирует система. */}
          <circle
            cx="13"
            cy="16"
            r="3.1"
            fill="var(--color-violet)"
            stroke="var(--color-paper)"
            strokeWidth="1.6"
          />
        </>
      ) : (
        /* Ступень, а не диагональ. Наклонная линия на 20 px превращается в
           кляксу — проверено скриншотом; прямой угол держит форму. */
        <path
          d="M4 11h9v11h15"
          stroke="var(--color-paper)"
          strokeWidth="3.4"
          strokeLinecap="butt"
          strokeLinejoin="miter"
          fill="none"
        />
      )}
    </svg>
  );
}

export function Logo({ size = 30, className = '' }: Props) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <Mark size={size} />
      <span
        className="font-display font-semibold uppercase leading-none text-line"
        style={{ fontSize: size * 0.72, letterSpacing: '0.16em' }}
      >
        Vantage
      </span>
    </span>
  );
}
