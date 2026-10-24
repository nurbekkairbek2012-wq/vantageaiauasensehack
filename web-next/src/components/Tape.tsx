/* Лента самописца: настоящая запись NDVI одного найденного объекта.
 *
 * Данные не нарисованы. Это 61 месячный медианный композит Sentinel-2 за
 * апрель–октябрь 2018–2026 по объекту площадью 1600 м², выгруженный
 * прогоном в public/data/hero-series.json. Растительность идёт своим
 * сезонным ходом восемь лет, в октябре 2024 срывается и не возвращается —
 * это и есть признак, по которому работает продукт.
 *
 * Почему SVG, а не canvas: линия должна быть выделяемой глазом на проекторе,
 * масштабироваться без пересчёта и работать при выключенном JS-рендере.
 */

import { useEffect, useRef, useState } from 'react';

type Series = {
  candidate_id: string;
  area_m2: number;
  break_date: string;
  break_index: number;
  ndvi_before: number;
  ndvi_after: number;
  dates: string[];
  ndvi: (number | null)[];
  source: string;
};

const W = 1200;

/* На узком экране лента сжимается по ширине вместе со всей вёрсткой, и при
   одной пропорции запись превращается в полоску в сорок пикселей высотой —
   подписи осей нечитаемы, разрыв не виден. Главный элемент страницы не может
   пропадать на телефоне, поэтому пропорция там другая: viewBox выше, шрифты
   крупнее, годы подписаны через один. */
const H_WIDE = 320;
const H_NARROW = 620;
const PAD = { top: 26, right: 24, bottom: 34, left: 46 };

const NDVI_MIN = -0.1;
const NDVI_MAX = 0.85;

export function Tape({ data }: { data: Series }) {
  const pathRef = useRef<SVGPathElement>(null);
  const [len, setLen] = useState(3000);
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    const sync = () => setNarrow(mq.matches);
    sync();
    mq.addEventListener('change', sync);
    return () => mq.removeEventListener('change', sync);
  }, []);

  useEffect(() => {
    if (pathRef.current) setLen(pathRef.current.getTotalLength());
  }, [data, narrow]);

  const H = narrow ? H_NARROW : H_WIDE;
  const label = narrow ? 30 : 13;
  const rule = narrow ? 2.2 : 1;

  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const x = (i: number) => PAD.left + (i / (data.ndvi.length - 1)) * plotW;
  const y = (v: number) =>
    PAD.top + plotH - ((v - NDVI_MIN) / (NDVI_MAX - NDVI_MIN)) * plotH;

  // Пропуски в ряду разрывают путь, а не соединяются прямой: месяц без
  // валидных пикселей — это отсутствие наблюдения, и рисовать через него
  // линию значит выдумывать данные.
  let d = '';
  let pen = false;
  data.ndvi.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
  });

  const bx = x(data.break_index);
  const by = y(data.ndvi[data.break_index] ?? data.ndvi_after);

  const years = Array.from(
    new Set(data.dates.map((s) => s.slice(0, 4))),
  );
  const yearAt = (yr: string) => x(data.dates.findIndex((s) => s.startsWith(yr)));

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto block"
        role="img"
        aria-label={`Запись вегетационного индекса объекта ${data.candidate_id} с 2018 по 2026 год. Растительность держится восемь лет, затем ${data.break_date} падает с ${data.ndvi_before} до ${data.ndvi_after} и не восстанавливается.`}
      >
        {/* сетка ленты: год по горизонтали, 0.2 NDVI по вертикали */}
        <g stroke="var(--color-grid)" strokeWidth={rule}>
          {years.map((yr) => (
            <line key={yr} x1={yearAt(yr)} y1={PAD.top} x2={yearAt(yr)} y2={PAD.top + plotH} opacity="0.55" />
          ))}
          {[0, 0.2, 0.4, 0.6, 0.8].map((v) => (
            <line key={v} x1={PAD.left} y1={y(v)} x2={W - PAD.right} y2={y(v)} opacity="0.35" />
          ))}
        </g>

        {/* подписи осей */}
        <g fill="var(--color-muted-2)" fontSize={label} fontFamily="var(--font-sans)" className="tabular">
          {years
            .filter((_, i) => !narrow || i % 2 === 0)
            .map((yr) => (
              <text key={yr} x={yearAt(yr) + 6} y={H - 8}>{yr}</text>
            ))}
          {[0, 0.4, 0.8].map((v) => (
            <text key={v} x={4} y={y(v) + label * 0.34}>{v.toFixed(1)}</text>
          ))}
        </g>

        {/* зона после разрыва — запись продолжается, но уже по другому уровню */}
        <rect
          x={bx}
          y={PAD.top}
          width={W - PAD.right - bx}
          height={plotH}
          fill="var(--color-violet)"
          opacity="0.09"
        />

        {/* сама запись */}
        <path
          ref={pathRef}
          d={d}
          fill="none"
          stroke="var(--color-line)"
          strokeWidth={narrow ? 5 : 2.4}
          strokeLinejoin="round"
          strokeLinecap="round"
          className="scribe"
          style={{ ['--len' as string]: len }}
        />

        {/* отметка разрыва: оператор обводит событие на ленте от руки */}
        <g className="mark-in" style={{ transformOrigin: `${bx}px ${by}px` }}>
          <line
            x1={bx} y1={PAD.top} x2={bx} y2={PAD.top + plotH}
            stroke="var(--color-violet-lit)" strokeWidth={narrow ? 3 : 1.5}
            strokeDasharray={narrow ? '9 9' : '4 4'}
          />
          <circle cx={bx} cy={by} r={narrow ? 22 : 11} fill="none" stroke="var(--color-violet-lit)" strokeWidth={narrow ? 5 : 2.5} />
          <circle cx={bx} cy={by} r={narrow ? 7 : 3.5} fill="var(--color-violet-lit)" />
        </g>
      </svg>

      <figcaption className="mt-4 flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm text-muted">
        <span>
          Объект <span className="text-line tabular">{data.candidate_id}</span>, площадь{' '}
          <span className="text-line tabular">{data.area_m2.toLocaleString('ru-RU')} м²</span>
        </span>
        <span>
          Вегетация упала с <span className="text-line tabular">{data.ndvi_before}</span> до{' '}
          <span className="text-line tabular">{data.ndvi_after}</span> и не вернулась
        </span>
        <span className="text-muted-2">{data.source}</span>
      </figcaption>
    </figure>
  );
}
