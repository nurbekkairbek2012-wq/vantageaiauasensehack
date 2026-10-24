"""@font-face для печати из HTML: все наборы символов, а не только кириллица.

Шрифты лежат в web-next/public/fonts по наборам (latin, latin-ext,
cyrillic, cyrillic-ext), как их раздаёт Google Fonts. Дека и картинка
превью подключали только кириллицу, и всё остальное — латиница «Vantage
AI», цифры, знак тенге — молча уходило в запасной шрифт с засечками. На
титульном слайде это было видно сразу, в коде — никак.

Браузер выбирает файл по unicode-range, поэтому каждый набор объявляется
со своим диапазоном: без диапазона последний объявленный файл накрывает
все символы, и остальные не используются вовсе.
"""

from __future__ import annotations

import base64
from pathlib import Path

FONTS = Path(__file__).resolve().parents[1] / "web-next/public/fonts"

#: Диапазоны наборов — те же, что в CSS Google Fonts.
RANGES = {
    "cyrillic-ext": "U+0460-052F, U+1C80-1C88, U+20B4, U+2DE0-2DFF, U+A640-A69F, U+FE2E-FE2F",
    "cyrillic": "U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116",
    "latin-ext": ("U+0100-02AF, U+0304, U+0308, U+0329, U+1E00-1E9F, U+1EF2-1EFF, U+2020, "
                  "U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF"),
    "latin": ("U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, "
              "U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, "
              "U+2212, U+2215, U+FEFF, U+FFFD"),
}


def font_faces(family: str, stem: str, weights: tuple[int, ...], *, embed: bool = True) -> str:
    """CSS со всеми наборами шрифта ``stem`` (например, 'oswald') для весов.

    ``embed=True`` вшивает файлы как data-URI: так печать не зависит от
    сервера. Иначе — ссылки file://.
    """
    rules = []
    for weight in weights:
        for subset, unicode_range in RANGES.items():
            path = FONTS / f"{stem}-{weight}-{subset}.woff2"
            if not path.exists():
                continue
            src = (f"data:font/woff2;base64,{base64.b64encode(path.read_bytes()).decode()}"
                   if embed else path.as_uri())
            rules.append(
                f"@font-face{{font-family:'{family}';font-weight:{weight};font-display:block;"
                f"src:url('{src}') format('woff2');unicode-range:{unicode_range};}}")
    return "\n".join(rules)
