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

CSS = """
@page { size: A4; margin: 16mm 16mm 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: 'Golos', sans-serif; font-size: 10.4pt; line-height: 1.45; color: #1c1530; }
h1 { font-family: 'Oswald', sans-serif; font-weight: 600; font-size: 24pt; line-height: 1.1;
     color: #2a1260; margin: 0 0 6pt; }
h2 { font-family: 'Oswald', sans-serif; font-weight: 500; font-size: 14pt; color: #4c1d95;
     margin: 16pt 0 6pt; border-bottom: 1px solid #e3dcf5; padding-bottom: 3pt; }
p { margin: 0 0 7pt; }
strong { color: #1c1530; }
a { color: #5b21b6; text-decoration: none; }
hr { border: 0; border-top: 2px solid #7c3aed; margin: 10pt 0 12pt; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt; font-size: 9.8pt; }
th, td { padding: 4pt 6pt; border-bottom: 1px solid #ece7f7; vertical-align: top; text-align: left; }
th { color: #6b5b95; font-weight: 500; }
td:last-child { text-align: right; white-space: nowrap; }
ul, ol { margin: 0 0 8pt 16pt; padding: 0; } li { margin: 0 0 3pt; }
h2, table, li { break-inside: avoid; }
"""


def render(markdown: str) -> str:
    from fontfaces import font_faces
    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark", {"html": True}).enable("table").render(markdown)
    fonts = font_faces("Golos", "golos-text", (400, 500, 600)) + \
        font_faces("Oswald", "oswald", (500, 600))
    return (f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<style>{fonts}{CSS}</style></head><body>{body}</body></html>")


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
