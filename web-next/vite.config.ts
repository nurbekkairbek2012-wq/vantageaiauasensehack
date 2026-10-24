import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// base: './' — сборка должна открываться из подкаталога, а не только с
// корня домена: так её публикует GitHub Pages, и так же она открывается
// локальным сервером на площадке. Абсолютные пути ломают оба случая.
//
// Чего относительные пути НЕ дают, вопреки тому, что здесь было написано
// раньше, — запуска файлом с флешки. Браузер запрещает странице,
// открытой по file://, подгружать модули, стили и данные из соседних
// файлов, и сборка выходит пустой. Запасной вариант на площадке — не
// флешка, а `python -m http.server --directory web-next/dist`; см.
// «Если упадёт интернет» в docs/PITCH.md.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
    assetsInlineLimit: 0,
    // Две страницы, а не одна: лендинг убеждает, карта работает. Разные
    // режимы, разный вес, и грузить Leaflet на лендинге незачем.
    rollupOptions: {
      input: {
        index: resolve(import.meta.dirname, 'index.html'),
        map: resolve(import.meta.dirname, 'map.html'),
        economy: resolve(import.meta.dirname, 'economy.html'),
        timelapse: resolve(import.meta.dirname, 'timelapse.html'),
        forecast: resolve(import.meta.dirname, 'forecast.html'),
        citizen: resolve(import.meta.dirname, 'citizen.html'),
        label: resolve(import.meta.dirname, 'label.html'),
      },
    },
  },
})
