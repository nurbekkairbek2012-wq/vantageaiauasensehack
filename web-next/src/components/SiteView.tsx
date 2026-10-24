/* Найденный объект на снимке высокого разрешения.
 *
 * Лендинг до сих пор доказывал словами. Пять признаков, восемь допущений,
 * четыреста двадцать девять кандидатов — всё правда, и всё читается как
 * текст о работе, а не как работа. Одна картинка настоящего объекта в
 * 0,75 метра на пиксель делает то, чего три абзаца не делают: показывает,
 * что там действительно что-то есть.
 *
 * Снимок живой, а не сохранённая картинка, и это осознанный обмен. Живой
 * требует сети и показывает сегодняшнее состояние — если объект вывезли,
 * будет видно чистое поле, и это честно. Сохранённая картинка работала бы
 * без сети, но застыла бы на дате выгрузки, и проверить её было бы нечем.
 *
 * Контур объекта рисуется поверх настоящей геометрией из прогона, а не
 * кружком «примерно здесь»: показать границу — значит дать проверить.
 */

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { createBasemapLayer } from './basemaps';

type Props = {
  feature: GeoJSON.Feature | null;
  zoom?: number;
  className?: string;
};

export function SiteView({ feature, zoom = 16, className = '' }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const shape = useRef<L.GeoJSON | null>(null);

  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, {
      zoomControl: false,
      attributionControl: true,
      // Снимок, а не карта: перетаскивать и зумить здесь нечего, а уехавший
      // вид на лендинге никто не вернёт обратно.
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      minZoom: 12,
      maxZoom: 19,
    }).setView([51.21, 71.5], zoom);
    createBasemapLayer('sat', 19).addTo(m);
    map.current = m;

    // Leaflet запоминает размер контейнера при создании; на лендинге эта
    // ячейка получает высоту после раскладки соседей, и без наблюдателя
    // карта осталась бы чёрным прямоугольником.
    const watch = new ResizeObserver(() => m.invalidateSize());
    watch.observe(host.current);
    return () => { watch.disconnect(); m.remove(); map.current = null; };
  }, [zoom]);

  useEffect(() => {
    const m = map.current;
    if (!m || !feature) return;
    if (shape.current) m.removeLayer(shape.current);
    shape.current = L.geoJSON(feature, {
      style: () => ({ color: '#ede9fe', weight: 2, fillColor: '#a78bfa', fillOpacity: 0.18 }),
    }).addTo(m);
    m.fitBounds(shape.current.getBounds().pad(1.6), { animate: false, maxZoom: 17 });
  }, [feature]);

  return (
    <div className={`relative overflow-hidden rounded-sm border border-grid bg-soot-2 ${className}`}>
      <div ref={host} className="absolute inset-0" />
    </div>
  );
}
