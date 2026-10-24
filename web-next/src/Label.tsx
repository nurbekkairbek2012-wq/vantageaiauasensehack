/* Ручная разметка: пара «до / после» и три кнопки.
 *
 * Это не витрина, а инструмент, и он закрывает самый дорогой пункт списка
 * недоделанного. Сеть не обучается, потому что положительных примеров из
 * OpenStreetMap не набирается в принципе: внутри существующего полигона ТБО
 * детектор изменений ничего не находит — там и в 2018 году была голая
 * поверхность. Остаётся посмотреть глазами, и до сих пор для этого не было
 * ничего, кроме QGIS и списка координат.
 *
 * Решения, которые здесь важны:
 *
 * Три кнопки, а не две. «Не понятно» — полноценный ответ: заставлять
 * человека выбирать между «свалка» и «не свалка» на мутном чипе значит
 * набирать шум и учить на нём сеть.
 *
 * Клавиши 1/2/3 и автопереход. Размечать надо сотни объектов; каждое
 * лишнее движение мыши умножается на эту сотню.
 *
 * Сохранение в localStorage на каждый ответ. Работа на сорок минут не
 * должна пропадать от закрытой вкладки.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Nav } from './components/Nav';

type Chip = {
  id: string;
  slug: string;
  area_m2?: number;
  break_date?: string;
  ndvi_drop?: number;
  bsi_rise?: number;
};

type Verdict = 'landfill' | 'not' | 'unclear';

const STORE = 'vantage.labels.v1';

const CHOICES: [Verdict, string, string][] = [
  ['landfill', 'Свалка', '1'],
  ['not', 'Не свалка', '2'],
  ['unclear', 'Не понятно', '3'],
];

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

function humanDate(v?: string) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export default function Label() {
  const [chips, setChips] = useState<Chip[]>([]);
  const [labels, setLabels] = useState<Record<string, Verdict>>({});
  const [at, setAt] = useState(0);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORE);
      if (saved) setLabels(JSON.parse(saved));
    } catch { /* повреждённое хранилище не должно ронять страницу */ }

    fetch('./data/chips.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setChips(d?.chips ?? []))
      .catch(() => setChips([]))
      .finally(() => setLoaded(true));
  }, []);

  // Начинать надо с первого неразмеченного, а не с первого в списке:
  // иначе после перезагрузки человек снова листает то, что уже прошёл.
  useEffect(() => {
    if (!chips.length) return;
    const next = chips.findIndex((c) => !labels[c.id]);
    setAt(next < 0 ? chips.length - 1 : next);
    // намеренно один раз, при появлении списка
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chips.length]);

  const current = chips[at];
  const done = useMemo(() => Object.keys(labels).length, [labels]);
  const counts = useMemo(() => {
    const out: Record<Verdict, number> = { landfill: 0, not: 0, unclear: 0 };
    Object.values(labels).forEach((v) => (out[v] += 1));
    return out;
  }, [labels]);

  const decide = useCallback(
    (verdict: Verdict) => {
      if (!current) return;
      setLabels((prev) => {
        const next = { ...prev, [current.id]: verdict };
        try { localStorage.setItem(STORE, JSON.stringify(next)); } catch { /* приватный режим */ }
        return next;
      });
      setAt((i) => Math.min(i + 1, chips.length - 1));
    },
    [current, chips.length],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      const choice = CHOICES.find(([, , key]) => key === e.key);
      if (choice) { e.preventDefault(); decide(choice[0]); return; }
      if (e.key === 'ArrowRight') setAt((i) => Math.min(i + 1, chips.length - 1));
      if (e.key === 'ArrowLeft') setAt((i) => Math.max(i - 1, 0));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [decide, chips.length]);

  const download = () => {
    const payload = JSON.stringify(
      { labelled_at: new Date().toISOString(), labels },
      null,
      1,
    );
    const url = URL.createObjectURL(new Blob([payload], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'labels.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="label">
        <div className="flex items-center gap-5 text-sm">
          <span className="tabular text-muted-2">
            размечено <span className="text-line">{done}</span> из {chips.length}
          </span>
          <button
            type="button"
            onClick={download}
            disabled={!done}
            className="cursor-pointer rounded-sm border border-grid px-3 py-1.5 text-muted transition-colors duration-150 hover:border-violet hover:text-line disabled:cursor-not-allowed disabled:opacity-40"
          >
            Скачать labels.json
          </button>
        </div>
      </Nav>

      <main className="mx-auto w-full max-w-[1100px] flex-1 px-6 py-8">
        {!loaded ? null : !chips.length ? (
          <div className="max-w-[60ch]">
            <h1 className="text-2xl text-line">Чипов нет</h1>
            <p className="mt-3 text-muted">
              Пары «до / после» выгружаются из результатов прогона:
            </p>
            <pre className="mt-4 overflow-x-auto rounded-sm border border-grid bg-soot-2 px-3 py-2.5 text-xs text-muted">
python scripts/export_chips.py 200
            </pre>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-4">
              <h1 className="text-xl text-line">
                Свалка на снимке «после»?
              </h1>
              <p className="tabular text-sm text-muted-2">
                {at + 1} / {chips.length} · {current?.id}
              </p>
            </div>

            <div className="mt-5 grid gap-5 md:grid-cols-2">
              {(['before', 'after'] as const).map((side) => (
                <figure key={side} className="m-0">
                  <figcaption className="mb-2 flex items-baseline justify-between text-sm">
                    <span className={side === 'after' ? 'text-line' : 'text-muted-2'}>
                      {side === 'before' ? 'До разрыва' : 'После разрыва'}
                    </span>
                    {side === 'after' && (
                      <span className="text-muted-2">{humanDate(current?.break_date)}</span>
                    )}
                  </figcaption>
                  <img
                    src={`./chips/${current?.slug}-${side}.png`}
                    alt={side === 'before' ? 'Снимок до разрыва' : 'Снимок после разрыва'}
                    /* Увеличение делает CSS: чип 64 px, и растягивать его
                       сглаживанием значит выдумывать детали, которых в
                       данных нет. pixelated показывает ровно пиксели. */
                    className="aspect-square w-full rounded-sm border border-grid bg-soot-2 [image-rendering:pixelated]"
                  />
                </figure>
              ))}
            </div>

            <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm text-muted-2">
              <div className="flex gap-2">
                <dt>Площадь</dt>
                <dd className="tabular text-line">{(current?.area_m2 ?? 0).toLocaleString('ru-RU')} м²</dd>
              </div>
              <div className="flex gap-2">
                <dt>Падение NDVI</dt>
                <dd className="tabular text-line">{current?.ndvi_drop ?? '—'}</dd>
              </div>
              <div className="flex gap-2">
                <dt>Рост BSI</dt>
                <dd className="tabular text-line">{current?.bsi_rise ?? '—'}</dd>
              </div>
              {labels[current?.id ?? ''] && (
                <div className="flex gap-2">
                  <dt>Уже размечен</dt>
                  <dd className="text-violet-lit">
                    {CHOICES.find(([v]) => v === labels[current.id])?.[1]}
                  </dd>
                </div>
              )}
            </dl>

            <div className="mt-7 flex flex-wrap gap-3">
              {CHOICES.map(([verdict, label, key]) => (
                <button
                  key={verdict}
                  type="button"
                  onClick={() => decide(verdict)}
                  className="flex cursor-pointer items-center gap-3 rounded-sm border border-grid px-5 py-3 text-line transition-colors duration-150 hover:border-violet hover:bg-violet-deep/40"
                >
                  <span className="tabular rounded-sm bg-soot-3 px-2 py-0.5 text-xs text-muted-2">
                    {key}
                  </span>
                  {label}
                </button>
              ))}
              <div className="flex items-center gap-2 text-sm text-muted-2">
                <button
                  type="button"
                  onClick={() => setAt((i) => Math.max(i - 1, 0))}
                  className="cursor-pointer rounded-sm px-2 py-1 hover:text-line"
                >
                  ← назад
                </button>
                <button
                  type="button"
                  onClick={() => setAt((i) => Math.min(i + 1, chips.length - 1))}
                  className="cursor-pointer rounded-sm px-2 py-1 hover:text-line"
                >
                  пропустить →
                </button>
              </div>
            </div>

            <div className="mt-8 h-[3px] w-full bg-grid">
              <div
                className="h-full bg-violet-lit transition-[width] duration-300"
                style={{ width: `${chips.length ? (done / chips.length) * 100 : 0}%` }}
              />
            </div>
            <p className="tabular mt-3 text-sm text-muted-2">
              свалка {counts.landfill} · не свалка {counts.not} · не понятно {counts.unclear}
            </p>

            <p className="mt-8 max-w-[70ch] text-sm leading-relaxed text-muted-2">
              Разметка хранится в браузере и не уходит никуда. Когда наберётся
              хотя бы по пять примеров каждого класса, скачайте файл и
              скормите его обучению:{' '}
              <code className="text-muted">python scripts/train_from_labels.py labels.json</code>.
            </p>
          </>
        )}
      </main>
    </div>
  );
}
