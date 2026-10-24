/* Черновик акта о выявлении несанкционированного размещения отходов.
 *
 * Печать идёт через диалог браузера: ноль зависимостей, работает офлайн и
 * даёт PDF на любой машине. Никакой генерации на сервере — API не
 * развёрнут и до защиты разворачивать его незачем.
 *
 * Документ выходит ЧЕРНОВИКОМ, и это не оговорка в подвале, а первое, что
 * видно на листе. Модель предлагает, человек подтверждает: акт становится
 * документом только после подписи с именем и должностью. Ложный
 * официальный документ, сформированный автоматически, стоит доверия
 * акимата дороже, чем отсутствие функции целиком.
 */

type Props = Record<string, unknown>;

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const kzt = (v: unknown) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)} млн ₸`;
  return `${Math.round(n).toLocaleString('ru-RU')} ₸`;
};

const num = (v: unknown, d = 0) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString('ru-RU', { maximumFractionDigits: d }) : '—';
};

function humanDate(v: unknown) {
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? '—' : `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export function Act({ p, center }: { p: Props; center: [number, number] | null }) {
  const today = new Date().toLocaleDateString('ru-RU');
  // Number(null) === 0, и ноль проходит проверку на конечность. Пустая
  // вероятность превращалась в «Оценка модели 0%» — то есть в документе
  // появлялось утверждение «модель уверена, что это не свалка». Проверять
  // надо наличие значения, а не его конечность.
  const has = (v: unknown) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const conf = has(p.probability) ? Number(p.probability) : null;
  const evidence = has(p.evidence_score) ? Number(p.evidence_score) : null;

  const rows: [string, string][][] = [
    [
      ['Координаты (WGS84)', center ? `${center[0].toFixed(6)}, ${center[1].toFixed(6)}` : '—'],
      ['Площадь', `${num(p.area_m2)} м²`],
      ['Дата возникновения', humanDate(p.break_date)],
      ['Оценка массы отходов', `${num(p.mass_t)} т`],
    ],
    [
      ['Метод', 'дистанционное зондирование: Sentinel-2, Sentinel-1, Landsat 8/9'],
      /* В акт идут обе величины, и обе с оговоркой о происхождении. Это
         документ, который читает человек с полномочиями: «оценка модели
         100%» без указания, на чём модель училась и видела ли она этот
         объект, в акте недопустима. */
      conf !== null
        ? [
            'Оценка модели',
            `${Math.round(conf * 100)}% (вневыборочная: объект не входил в обучение). `
              + `Согласие физических признаков ${evidence !== null ? Math.round(evidence * 100) : '—'}%`
              + ` (${p.n_agreeing ?? '—'} из 5)`,
          ]
        : [
            'Согласие физических признаков',
            evidence !== null
              ? `${Math.round(evidence * 100)}% (${p.n_agreeing ?? '—'} из 5). `
                + 'Модель по этому объекту не высказывалась: он не входил в обучающую выборку'
              : 'не рассчитано',
          ],
      ['Подтверждено независимых источников съёмки', String(p.verify_providers ?? 0)],
      ['Контроль устранения', String(p.removal_note ?? 'не проводился')],
    ],
    [
      ['Диапазон ущерба (P10–P90)', `${kzt(p.damage_p10)} – ${kzt(p.damage_p90)}`],
      ['Медианная оценка', kzt(p.damage_p50)],
      ['Эмиссия за 20 лет', `${num(p.co2e_t)} т CO₂-экв.`],
    ],
    [
      ['Статья', String(p.penalty_article ?? 'ст. 344, ч. 2-1 КоАП РК')],
      ['Размер санкции', kzt(p.penalty_kzt)],
    ],
  ];

  const titles = [
    '1. Сведения об объекте',
    '2. Основания выявления',
    '3. Оценка ущерба',
    '4. Применимая норма',
  ];

  return (
    <article id="act-print" aria-hidden="true">
      <p className="act-draft">
        ЧЕРНОВИК. Документ сформирован автоматически системой Vantage AI на основе
        вероятностной модели и НЕ является официальным. Требуется проверка и
        подтверждение уполномоченным лицом.
      </p>

      <h1>АКТ о выявлении несанкционированного размещения отходов</h1>
      <p className="act-sub">
        № {String(p.candidate_id ?? '—')} от {today}
      </p>

      {rows.map((table, i) => (
        <section key={titles[i]}>
          <h2>{titles[i]}</h2>
          <table>
            <tbody>
              {table.map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      <div className="act-sign">
        <div>подпись проверяющего</div>
        <div>должность, ФИО</div>
      </div>

      <p className="act-foot">
        Результаты получены методом дистанционного зондирования и представляют
        собой оценку вероятности, а не юридическое доказательство. Решение о
        статусе объекта, размере ущерба и применении санкций принимается
        уполномоченным лицом по итогам выездной проверки.
        Vantage AI · Future Minds Hackathon 2026.
      </p>
    </article>
  );
}
