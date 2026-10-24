# Образ для бота и API.
#
# Тяжёлые ML-зависимости сюда не ставятся: бот не обучает модели, он
# сверяет координаты со списком уже найденных объектов. Образ с torch
# весил бы под три гигабайта и собирался бы двадцать минут на каждом
# деплое — при нулевой пользе.

FROM python:3.11-slim AS base

# Геозависимости (shapely, pyproj, geopandas) тянут системные библиотеки.
# Ставим только то, что реально нужно, и чистим кеш apt в том же слое —
# иначе он останется в образе навсегда.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-c1v5 \
        libproj25 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала только манифест: слой с зависимостями переиспользуется,
# пока pyproject.toml не изменился. Правка кода не пересобирает pip.
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --upgrade pip && pip install -e ".[service]"

# Каталог для артефактов прогона. Если положить сюда candidates.geojson,
# бот начнёт сверять сообщения жителей с найденными объектами.
# Без него он тоже работает — просто все сообщения считаются новыми.
RUN mkdir -p /app/outputs
VOLUME ["/app/outputs"]

# Непривилегированный пользователь: контейнер, работающий от root,
# это лишний риск без единой причины.
RUN useradd --create-home --uid 10001 vantage && chown -R vantage:vantage /app
USER vantage

# --------------------------------------------------------------------------- #

FROM base AS bot
CMD ["python", "-m", "vantage.bot"]

FROM base AS api
ENV PORT=8000
CMD ["sh", "-c", "python -m uvicorn 'vantage.api:create_app' --factory --host 0.0.0.0 --port ${PORT}"]
