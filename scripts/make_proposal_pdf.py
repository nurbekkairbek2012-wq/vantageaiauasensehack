"""Предложение для акимата и партнёров — из Markdown в PDF.

Текст живёт в docs/PROPOSAL*.md: его правят как обычный документ, а
числа в русской и английской версиях сторожит tests/test_docs_numbers.py
— после пересчёта старое число в предложении не переживёт тестов.

Печать — тем же Chromium, что и дека, и теми же шрифтами сайта: документ,
который несут в акимат, выглядит как продукт, а не как выгрузка.

    python scripts/make_proposal_pdf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

#: источник -> (имя файла для людей, имя на сайте). На сайте — латиницей:
#: кириллица и пробелы в адресе ломаются при пересылке ссылки.
SOURCES = {
    "PROPOSAL.md": ("Vantage AI — предложение акимату.pdf", "proposal_ru.pdf"),
    "PROPOSAL_KZ.md": ("Vantage AI — әкімдікке ұсыныс.pdf", "proposal_kz.pdf"),
    "PROPOSAL_EN.md": ("Vantage AI — proposal.pdf", "proposal_en.pdf"),
}
OUT = ROOT / "docs/proposal"
PUBLIC = ROOT / "web-next/public/docs"

# Служебное письмо: Times New Roman, только чёрный, поля 20/15/20/30 мм —
# тот же вид, что у акта и паспортов. До 27 сентября предложение было в
# фирменных фиолетовых цветах: в папке с документами акимата оно выглядело
# рекламной листовкой, а не письмом.
CSS = """
@page { size: A4; margin: 20mm 15mm 20mm 30mm; }
* { box-sizing: border-box; }
body { font-family: 'Times New Roman', Times, 'Liberation Serif', serif; font-size: 12pt;
       line-height: 1.3; color: #000; }
h1 { font-size: 14pt; font-weight: 700; line-height: 1.25; text-align: center; margin: 0 0 6pt; }
h2 { font-size: 12pt; font-weight: 700; margin: 12pt 0 4pt; }
p { margin: 0 0 6pt; text-align: justify; }
strong { color: #000; }
a { color: #000; text-decoration: underline; }
hr { border: 0; border-top: 0.5pt solid #000; margin: 8pt 0 10pt; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt; font-size: 11pt; }
th, td { padding: 2pt 4pt; border: 0.5pt solid #000; vertical-align: top; text-align: left; }
th { font-weight: 700; }
th:empty { border: 0; padding: 0; }
thead tr:has(th:empty) { display: none; }
td:last-child { text-align: right; }
ul, ol { margin: 0 0 6pt 16pt; padding: 0; } li { margin: 0 0 2pt; }
h2, table, li { break-inside: avoid; }
"""


def render(markdown: str) -> str:
    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark", {"html": True}).enable("table").render(markdown)
    return (f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        PUBLIC.mkdir(parents=True, exist_ok=True)
        for source, (name, web_name) in SOURCES.items():
            path = ROOT / "docs" / source
            if not path.exists():
                continue
            page.set_content(render(path.read_text(encoding="utf-8")), wait_until="load")
            page.wait_for_timeout(500)
            target = OUT / name
            page.pdf(path=str(target), format="A4", print_background=True,
                     margin={"top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
            # Рядом с декой — там, где команда держит материалы подачи.
            (ROOT.parent / name).write_bytes(target.read_bytes())
            (PUBLIC / web_name).write_bytes(target.read_bytes())
            print(f"   {source} → {target.relative_to(ROOT)}")
        browser.close()
    print("── предложения собраны (копии — рядом с декой)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
