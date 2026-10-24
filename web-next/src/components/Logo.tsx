/* Знак VANTAGE.
 *
 * Логотипа у продукта не было — бренд был только словом. Знак выведен из
 * механизма, а не из темы: это запись прибора, которая идёт ровно, срывается
 * вниз и **не возвращается**. Необратимость — единственный признак, который
 * отличает свалку от засухи, пожара и уборки урожая, и именно она нарисована.
 *
 * Что сознательно не нарисовано: спутник, планета, глобус, лист, мусорный
 * бак и галочка. Всё это иллюстрирует тему, а не работу.
 *
 * Знак читается на 20 px: линия толстая, излом один, точка разрыва сплошная.
 */

type Props = { size?: number; className?: string };

export function Mark({ size = 32, className = '' }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <rect x="0.5" y="0.5" width="31" height="31" rx="7" fill="var(--color-violet)" />
      {/* Ступень, а не диагональ. Наклонная линия на 20 px превращается в
          кляксу — проверено скриншотом; прямой угол держит форму. */}
      <path
        d="M4 11h9v11h15"
        stroke="var(--color-paper)"
        strokeWidth="3.4"
        strokeLinecap="butt"
        strokeLinejoin="miter"
        fill="none"
      />
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
