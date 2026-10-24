import type { ReactElement } from 'react';

/* Пять признаков как пластины отчёта, а не как карточки с иконками.
 *
 * Дисциплина взята у отклонённого направления «оболочка из одной складки»:
 * доказательства нумеруются как пластины и подаются парой «что измерено /
 * что это отсекает», а не как список фич с галочками. Разница смысловая:
 * фича обещает, пластина показывает.
 *
 * Диаграммы нарисованы, а не взяты из набора иконок: каждая показывает
 * форму сигнала, по которой признак и работает. Иконка «лист» на месте
 * NDVI не сообщила бы ничего.
 */

type Plate = {
  n: string;
  title: string;
  measure: string;
  physics: string;
  rejects: string;
  diagram: ReactElement;
};

const S = { stroke: 'var(--color-line)', strokeWidth: 2, fill: 'none' } as const;
const G = { stroke: 'var(--color-violet-lit)', strokeWidth: 2, fill: 'none' } as const;

const plates: Plate[] = [
  {
    n: 'I',
    title: 'Падение вегетации без возврата',
    measure: 'NDVI',
    physics: 'Растительность гибнет под насыпью и не отрастает следующей весной.',
    rejects: 'Пашню: у поля индекс возвращается каждый сезон.',
    diagram: (
      <>
        <path d="M2 26 Q10 6 18 26 Q26 6 34 26" {...G} opacity="0.5" />
        <path d="M34 26 Q42 6 50 26 Q58 6 66 26" {...G} opacity="0.5" />
        <path d="M2 26 Q10 6 18 26 Q26 8 34 24 L34 30 L66 30" {...S} />
      </>
    ),
  },
  {
    n: 'II',
    title: 'Рост открытого грунта',
    measure: 'BSI',
    physics: 'Доля минеральной поверхности в пикселе растёт скачком.',
    rejects: 'Плотную застройку: там она высока с самого начала.',
    diagram: (
      <>
        <path d="M2 28 L32 28 L34 10 L66 10" {...S} />
        <path d="M2 24 L66 24" {...G} strokeDasharray="3 4" opacity="0.6" />
      </>
    ),
  },
  {
    n: 'III',
    title: 'Отклик полимеров в коротковолновом ИК',
    measure: 'PMLI',
    physics: 'Обертоны связей C–H в полиэтилене и ПЭТ поглощают около 1730 и 2100–2300 нм.',
    rejects: 'Чистый грунт: у него этих полос нет.',
    diagram: (
      <>
        <path d="M2 20 L14 20 L20 8 L26 20 L40 20 L46 12 L52 20 L66 20" {...S} />
        <path d="M2 30 L66 30" {...G} opacity="0.35" />
      </>
    ),
  },
  {
    n: 'IV',
    title: 'Потеря стабильности поверхности',
    measure: 'Sentinel-1',
    physics: 'Свалку привозят, сгребают и уплотняют — обратное рассеяние меняется от прохода к проходу.',
    rejects: 'Карьер: его стенки стоят неделями.',
    diagram: (
      <>
        <path d="M2 19 L10 20 L18 18 L26 20 L34 19" {...G} opacity="0.7" />
        <path d="M34 19 L38 8 L42 28 L46 10 L50 26 L54 9 L58 27 L62 12 L66 22" {...S} />
      </>
    ),
  },
  {
    n: 'V',
    title: 'Тепловая аномалия зимой',
    measure: 'Landsat',
    physics: 'Анаэробное разложение органики греет тело свалки — зимой оно видно как пятно на снегу.',
    rejects: 'Снегосвалку: она, наоборот, холоднее фона.',
    diagram: (
      <>
        <path d="M2 26 L24 26 Q34 26 34 16 Q34 6 44 6 L66 6" {...S} />
        <path d="M2 26 L24 26 Q34 26 34 33 L66 33" {...G} opacity="0.55" />
      </>
    ),
  },
];

export function Plates() {
  return (
    <ol className="mt-14 border-t border-grid">
      {plates.map((p) => (
        <li
          key={p.n}
          className="grid grid-cols-1 gap-x-8 gap-y-4 border-b border-grid py-8 md:grid-cols-[3.5rem_15rem_1fr_1fr] md:items-start"
        >
          <span className="font-display text-2xl leading-none text-violet-lit tabular">{p.n}</span>

          <div>
            <h3 className="text-lg text-line">{p.title}</h3>
            {/* Чем измерено — часть доказательства, а не украшение: без
                этой строки пластина обещает, но не отчитывается. */}
            <p className="mt-1 font-display text-xs uppercase tracking-[0.18em] text-muted-2">
              {p.measure}
            </p>
            <svg viewBox="0 0 68 36" className="mt-3 h-9 w-40" aria-hidden="true">
              {p.diagram}
            </svg>
          </div>

          <p className="max-w-[46ch] text-muted">{p.physics}</p>

          <p className="max-w-[46ch] text-muted">
            <span className="text-muted-2">Отсекает.</span> {p.rejects}
          </p>
        </li>
      ))}
    </ol>
  );
}
