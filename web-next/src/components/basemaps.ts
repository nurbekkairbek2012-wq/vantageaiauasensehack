/* Подложка карты: снимок или схема.
 *
 * Раньше по умолчанию стояла координатная сетка без тайлов — расчёт был на
 * то, что карта обязана открыться без интернета. Расчёт не оправдался:
 * первое, что видит человек, открывший карту свалок, — пустой сиреневый
 * фон, на котором объекты висят ни к чему не привязанные. Свалка на пустом
 * фоне не доказывает ничего; свалка на снимке доказывает сама себя.
 *
 * Поэтому снимок стал состоянием по умолчанию, а сетка убрана совсем.
 * Сеть на площадке остаётся риском, но он закрывается иначе — записанным
 * дублем демонстрации, а не постоянно включённым запасным режимом.
 *
 * Слоя ровно два, и они отвечают на разные вопросы. Снимок: «что там на
 * земле». Схема: «где это относительно улиц и посёлков» — на снимке
 * подписей нет, и без схемы объект невозможно назвать словами.
 */

import L from 'leaflet';

export type Basemap = 'sat' | 'scheme';

export const BASEMAPS: Record<Basemap, { url: string; attribution: string; label: string }> = {
  sat: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri · Maxar',
    label: 'Снимок',
  },
  scheme: {
    url: 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap · © CARTO',
    label: 'Схема',
  },
};

export const BASEMAP_KEYS: Basemap[] = ['sat', 'scheme'];

/** Создать слой подложки. Общий для карты, таймлапса и прогноза. */
export function createBasemapLayer(kind: Basemap, maxZoom = 18): L.TileLayer {
  const cfg = BASEMAPS[kind];
  return L.tileLayer(cfg.url, {
    attribution: cfg.attribution,
    minZoom: 9,
    maxZoom,
    keepBuffer: 2,
    // Снимок Esri сам по себе ярче интерфейса; без притемнения фиолетовые
    // объекты на нём теряются, а глаз уходит на подложку вместо данных.
    className: kind === 'sat' ? 'basemap-sat' : 'basemap-scheme',
  });
}
