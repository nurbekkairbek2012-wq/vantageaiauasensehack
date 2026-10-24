/* Запись карты в видеофайл.
 *
 * ── Зачем ────────────────────────────────────────────────────────────
 *
 * Таймлапс — самое убедительное, что есть на сайте, и до сих пор его
 * можно было только показать вживую. На защите это риск: зал, проектор,
 * чужой Wi-Fi. Файл, лежащий на флешке, не зависит ни от чего.
 *
 * ── Почему нельзя записать «просто карту» ────────────────────────────
 *
 * Карта Leaflet — это не один холст, а стопка элементов: десятки тайлов
 * <img> с трансформациями, поверх них наш холст тепла, поверх — подписи.
 * Записать такое напрямую нечем: MediaRecorder умеет снимать поток с
 * ОДНОГО холста.
 *
 * Поэтому кадр собирается заново: берём каждый видимый элемент и рисуем
 * его в общий холст на том месте, где он оказался на экране. Положение
 * читается через getBoundingClientRect — оно уже учитывает все
 * трансформации Leaflet, и повторять его математику не нужно.
 *
 * ── Почему тайлы вообще рисуются ─────────────────────────────────────
 *
 * Браузер запрещает выгружать холст, на который попало изображение с
 * чужого домена без разрешения. Тайлы Esri такое разрешение отдают, но
 * только если элемент запрошен с crossOrigin — поэтому подложка создаётся
 * с этим признаком (см. basemaps.ts). Без него запись прошла бы, а
 * сохранение упало бы с ошибкой безопасности на последнем шаге.
 */

/** Что рисуется поверх кадра: год, подпись, счётчик. */
export type Overlay = {
  big: string;
  small: string;
};

type Options = {
  /** Сколько секунд писать. */
  seconds: number;
  /** Вызывается каждый кадр с долей пройденного, 0..1. Здесь двигают время. */
  onFrame: (progress: number) => void;
  /** Что подписать на кадре. Читается каждый кадр — подпись меняется. */
  overlay: () => Overlay;
};

/** Ширина записи. Больше 1280 не нужно: это не кино, а иллюстрация. */
const MAX_WIDTH = 1280;

function pickMime(): string {
  const wanted = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm',
  ];
  for (const type of wanted) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return '';
}

export function canRecord(): boolean {
  return (
    typeof MediaRecorder !== 'undefined' &&
    typeof document.createElement('canvas').captureStream === 'function'
  );
}

export async function recordMap(container: HTMLElement, options: Options): Promise<Blob> {
  const box = container.getBoundingClientRect();
  const scale = Math.min(1, MAX_WIDTH / box.width);
  const width = Math.round(box.width * scale);
  const height = Math.round(box.height * scale);

  const frame = document.createElement('canvas');
  frame.width = width;
  frame.height = height;
  const ctx = frame.getContext('2d');
  if (!ctx) throw new Error('нет двумерного контекста');

  const stream = frame.captureStream(30);
  const mime = pickMime();
  const recorder = new MediaRecorder(stream, mime ? { mimeType: mime, videoBitsPerSecond: 6_000_000 } : undefined);
  const chunks: BlobPart[] = [];
  recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };

  const done = new Promise<Blob>((resolve) => {
    recorder.onstop = () => resolve(new Blob(chunks, { type: mime || 'video/webm' }));
  });

  const draw = () => {
    const area = container.getBoundingClientRect();
    ctx.fillStyle = '#0d0918';
    ctx.fillRect(0, 0, width, height);

    /* Порядок обхода — порядок в DOM, а он совпадает с порядком слоёв
       Leaflet: подложка, потом наш холст. Сортировать по z-index не
       нужно, и попытка это делать сломала бы порядок тайлов внутри
       одного слоя. */
    const parts = container.querySelectorAll<HTMLElement>('img.leaflet-tile, canvas.vantage-heat');
    parts.forEach((part) => {
      const rect = part.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      // Невидимое не рисуем: у Leaflet тайлы уходящего зума ещё висят в
      // дереве с нулевой прозрачностью.
      const opacity = Number(getComputedStyle(part).opacity);
      if (!opacity) return;

      ctx.globalAlpha = opacity;
      try {
        ctx.drawImage(
          part as CanvasImageSource,
          (rect.left - area.left) * scale,
          (rect.top - area.top) * scale,
          rect.width * scale,
          rect.height * scale,
        );
      } catch {
        // Один неудачный тайл не должен ронять запись целиком.
      }
    });
    ctx.globalAlpha = 1;

    const { big, small } = options.overlay();
    const pad = Math.round(24 * scale);

    ctx.fillStyle = '#ede9fe';
    ctx.font = `600 ${Math.round(76 * scale)}px Oswald, Arial Narrow, sans-serif`;
    ctx.textBaseline = 'top';
    ctx.shadowColor = 'rgba(13,9,24,.9)';
    ctx.shadowBlur = Math.round(26 * scale);
    ctx.fillText(big, pad, pad);

    ctx.font = `400 ${Math.round(17 * scale)}px "Golos Text", system-ui, sans-serif`;
    ctx.fillStyle = '#b3a5d9';
    ctx.fillText(small, pad + 3, pad + Math.round(86 * scale));
    ctx.shadowBlur = 0;

    // Подпись источника обязана быть в кадре: файл уедет отдельно от
    // сайта, где эта строка стоит в углу карты.
    ctx.font = `400 ${Math.round(13 * scale)}px "Golos Text", system-ui, sans-serif`;
    ctx.fillStyle = 'rgba(179,165,217,.75)';
    const credit = 'Vantage AI · Sentinel-2, Landsat, Sentinel-1 · снимок Esri, Maxar';
    ctx.fillText(credit, pad, height - pad - Math.round(16 * scale));
  };

  recorder.start();
  const started = performance.now();

  await new Promise<void>((resolve) => {
    const tick = () => {
      const progress = (performance.now() - started) / (options.seconds * 1000);
      if (progress >= 1) {
        options.onFrame(1);
        draw();
        resolve();
        return;
      }
      options.onFrame(progress);
      draw();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  // Последний кадр должен успеть попасть в поток: остановка сразу после
  // отрисовки обрезает его, и видео заканчивается на предпоследнем.
  await new Promise((resolve) => setTimeout(resolve, 250));
  recorder.stop();
  return done;
}

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  // Отзывать сразу нельзя — браузер не успеет начать скачивание.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
