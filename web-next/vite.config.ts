import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// base: './' — сборка должна открываться и с GitHub Pages из подкаталога, и
// файлом с флешки, если на площадке не будет вообще ничего. Абсолютные пути
// ломают оба этих случая.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist', assetsInlineLimit: 0 },
})
