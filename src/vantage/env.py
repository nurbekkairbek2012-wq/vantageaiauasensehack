"""Подготовка окружения GDAL до первого импорта гео-библиотек.

Зачем этот модуль вообще существует
-----------------------------------
Проект живёт по пути с казахскими буквами (``Жұмыс үстелі``). Библиотека
``pyogrio`` при импорте выполняет::

    os.environ.setdefault("GDAL_CURL_CA_BUNDLE", certifi.where())

Путь к ``certifi`` лежит внутри проекта, то есть тоже содержит ``ұ``, ``ү``,
``і``. Этих символов нет в кодовой странице Windows (cp1251), поэтому
schannel — реализация TLS в Windows — получает битую строку и отбрасывает
файл сертификатов целиком::

    CURL error: schannel: invalid path name for CA file 'C:\...\Ж?мыс ?стел?\...'

Следствие: **ни один COG по HTTP не читается**. Весь пайплайн выше
``catalog`` работает, а загрузка растров падает. Ошибку при этом не видно:
rasterio пытается декодировать сообщение GDAL как UTF-8, натыкается на
cp1251 и падает с ``UnicodeDecodeError``, потеряв исходный текст.

Лечение: положить копию ``cacert.pem`` по пути, состоящему только из
ASCII, и выставить переменные **до** импорта ``pyogrio`` — тогда его
``setdefault`` уже ничего не переопределит.

Модуль намеренно не имеет тяжёлых зависимостей и вызывается из
``vantage/__init__.py`` первой строкой.
"""

from __future__ import annotations

import contextlib
import locale
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

#: Переменные, которыми GDAL и PROJ ищут файл корневых сертификатов.
CA_ENV_VARS = ("GDAL_CURL_CA_BUNDLE", "PROJ_CURL_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")

#: Имя копии сертификатов в ASCII-каталоге.
CA_FILENAME = "vantage_cacert.pem"


def system_ansi_codepage() -> str | None:
    """Кодовая страница ОС — та, которой пользуется schannel.

    Спрашивается у Windows напрямую, а НЕ через
    ``locale.getpreferredencoding``. Разница смертельна: при
    ``PYTHONUTF8=1`` Python отвечает «UTF-8», потому что говорит о себе, а
    не о системе. Настоящая ANSI-страница остаётся прежней (здесь 1251), и
    именно её применяет GDAL, открывая файл сертификатов.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetACP()}"
    except Exception:  # pragma: no cover — не Windows или урезанный ctypes
        return locale.getpreferredencoding(False)


def ansi_encodable(text: str) -> bool:
    """Представим ли путь в кодовой странице ОС.

    Именно это, а не «есть ли не-ASCII символы», определяет, поймёт ли
    путь schannel: кириллица в cp1251 представима, а казахские ``ұ`` и
    ``ү`` — нет. Проверять сам факт наличия юникода было бы слишком
    строго и заставляло бы копировать сертификаты там, где всё работает.

    ── Как эта проверка сама себя отключила ────────────────────────────

    Сначала кодировка бралась из ``locale.getpreferredencoding(False)``.
    Работало ровно до того дня, когда весь проект перевели на
    ``PYTHONUTF8=1`` — ради вывода на казахском в консоль. В режиме UTF-8
    Python начинает отвечать «UTF-8» на этот вопрос, потому что вопрос
    он понимает как «в чём я работаю», а не «что у системы».

    Путь с ``ұ`` и ``ү`` в UTF-8 представим прекрасно. Проверка стала
    возвращать True, копия сертификатов по ASCII-пути перестала
    создаваться, и КАЖДОЕ чтение COG по HTTPS начало отваливаться с
    «HTTP error code: 0». Снаружи это выглядело как плохая сеть — тем
    убедительнее, что requests те же ссылки скачивал: у него свои
    сертификаты и своя реализация TLS.

    Мораль по месту: спрашивать надо ту систему, которая будет читать
    файл, а не ту, которая задаёт вопрос.
    """
    if sys.platform != "win32":
        return True
    encoding = system_ansi_codepage()
    if encoding is None:
        return True
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _candidate_dirs() -> list[Path]:
    """Каталоги, куда можно положить ASCII-копию сертификатов.

    Порядок от самого стабильного к самому доступному: ProgramData
    переживает перезагрузку и чистку временных файлов, TEMP — нет, но
    доступен на запись всегда.
    """
    dirs: list[Path] = []
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        dirs.append(Path(program_data) / "vantage")
    dirs.append(Path(tempfile.gettempdir()) / "vantage")
    return dirs


def _install_ca_copy(source: Path) -> Path | None:
    """Скопировать cacert.pem в первый пригодный ASCII-каталог."""
    for directory in _candidate_dirs():
        if not ansi_encodable(str(directory)):
            continue
        target = directory / CA_FILENAME
        try:
            directory.mkdir(parents=True, exist_ok=True)
            # Копируем, только если файла нет или он устарел: сертификаты
            # обновляются вместе с certifi, и молча использовать
            # прошлогоднюю копию хуже, чем скопировать лишний раз.
            if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
                shutil.copyfile(source, target)
            return target
        except OSError as exc:  # каталог недоступен на запись — пробуем следующий
            log.debug("Не удалось подготовить %s: %s", target, exc)
    return None


def ensure_gdal_ca_bundle() -> str | None:
    """Гарантировать, что GDAL найдёт корневые сертификаты.

    Возвращает путь к использованному файлу или ``None``, если ничего
    делать не потребовалось (или не удалось — тогда в логе предупреждение).
    Функция идемпотентна: повторный вызов ничего не меняет.
    """
    existing = os.environ.get("GDAL_CURL_CA_BUNDLE")
    if existing and ansi_encodable(existing) and Path(existing).exists():
        return existing

    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi тянется зависимостями
        return None

    source = Path(certifi.where())
    if ansi_encodable(str(source)):
        # Путь и так пригоден — пусть pyogrio выставляет его сам.
        return None

    target = _install_ca_copy(source)
    if target is None:  # pragma: no cover - только при закрытых на запись каталогах
        log.warning(
            "Путь к сертификатам %s не представим в кодировке системы, а положить "
            "копию по ASCII-пути не удалось. Чтение растров по HTTPS работать не будет.",
            source,
        )
        return None

    for name in CA_ENV_VARS:
        os.environ[name] = str(target)
    log.debug("Сертификаты для GDAL: %s", target)
    return str(target)


def configure(*, gdal_cache_mb: int = 512) -> None:
    """Полная подготовка окружения перед работой с растрами.

    Помимо сертификатов задаёт настройки чтения COG по сети. Значения
    выбраны под сценарий «много мелких range-запросов к облаку»:

    ``GDAL_DISABLE_READDIR_ON_OPEN`` — без него GDAL на каждое открытие
    ассета перечисляет соседние объекты в контейнере, что для облачного
    хранилища означает лишний круг запросов на каждый снимок.

    ``CPL_VSIL_CURL_ALLOWED_EXTENSIONS`` — не давать GDAL искать
    сопутствующие файлы (.aux.xml, .msk), которых у COG в облаке нет.
    """
    ensure_gdal_ca_bundle()

    # ── И ещё раз, после pyogrio ────────────────────────────────────────
    #
    # Одного вызова мало, и это стоило целого вечера прогонов.
    #
    # configure() отрабатывает при импорте пакета vantage — то есть ДО
    # geopandas. Дальше geopandas тянет pyogrio, а тот при импорте
    # безусловно выставляет GDAL_CURL_CA_BUNDLE в путь certifi внутри
    # виртуального окружения. Наше значение он затирает.
    #
    # Для обычного проекта это безобидно. Здесь путь содержит казахские
    # буквы, которых нет в кодовой странице системы; schannel такой файл
    # сертификатов не открывает, и КАЖДОЕ чтение COG по HTTPS отваливается
    # с «HTTP error code: 0». Снаружи это выглядело как плохая сеть:
    # requests те же ссылки скачивал, а GDAL — нет.
    #
    # Поэтому pyogrio импортируется здесь, явно и первым, и сразу после
    # этого сертификаты выставляются заново. Порядок важнее вежливости:
    # молча полагаться на то, что чужая библиотека не тронет переменную,
    # уже не выходит.
    try:
        import pyogrio  # noqa: F401
    except ImportError:  # pragma: no cover — pyogrio тянется geopandas
        pass
    else:
        ensure_gdal_ca_bundle()

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.TIF,.tiff,.jp2")
    os.environ.setdefault("GDAL_CACHEMAX", str(gdal_cache_mb))
    os.environ.setdefault("VSI_CACHE", "TRUE")
    os.environ.setdefault("VSI_CACHE_SIZE", "67108864")

    # Повторы на уровне curl. Плитка стоит несколько минут и состоит из
    # сотен range-запросов; один оборванный не должен её ронять. На прогоне
    # по кольцу без этого две плитки из одиннадцати падали с «Chunk and
    # warp failed» — то есть просто по обрыву соединения.
    # Ретраям добавлен список кодов. Причина не в скорости, а в том, как
    # неудачное чтение выглядит на этой машине. При отказе GDAL составляет сообщение, подмешивая туда строку
    # системного форматтера ошибок Windows — а он на русской локали отдаёт
    # cp1251. rasterio читает сообщение как UTF-8 и падает с
    # UnicodeDecodeError прямо в rasterio/_err.pyx, ещё до того, как ошибка
    # станет RasterioIOError.
    #
    # Из-за этого не работает ни один перехватчик выше по стеку:
    # fail_on_error=False в odc-stac ловит ошибки rasterio, а тут до них
    # дело не доходит. Одно случайное 403 или 500 на восемьсот сцен роняет
    # плитку целиком, и это стоило двух прогонов подряд.
    #
    # Чинить декодирование негде — оно внутри скомпилированного модуля.
    # Значит, до сообщения не надо доводить: пусть GDAL переспрашивает сам,
    # пока ответ не придёт.
    # Таймауты. Без них повисшее соединение вешает весь прогон: GDAL ждёт
    # ответа вечно, повторы не начинаются, процесс жив и ничего не делает.
    # Поймано по замеру — время процессора застыло на 735 секундах, а
    # настенное шло дальше; со стороны это выглядело как «медленная плитка»,
    # хотя работа не шла вовсе.
    #
    # Значения с запасом: снимок читается сотнями range-запросов по
    # несколько сотен килобайт, и минуты на один хватает с большим избытком.
    os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "15")
    os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
    # Ни 404, ни 403 в списке нет, и оба отсутствуют по одной причине:
    # повтор их не лечит.
    #
    # 404 — файла нет, и от ожидания он не появится.
    #
    # 403 — истекла подпись ссылки. Planetary Computer подписывает ссылки
    # примерно на час; плитка идёт до двадцати минут, и на длинном прогоне
    # подпись успевает протухнуть. Воскреснуть она не может: нужна новая,
    # а её даёт только новый поиск по каталогу.
    #
    # Пока 403 стоял в списке, прогон вставал намертво: GDAL уходил в
    # повторы с растущей паузой — сорок пять секунд, потом больше — на
    # каждой из сотен ссылок, и до повтора плитки, который как раз
    # перевыпустил бы подписи, дело не доходило никогда. Сорок пять минут
    # на одной плитке без единой строчки в логе.
    #
    # Теперь отказ приходит сразу, плитка падает, повтор плитки заново
    # ищет сцены и получает свежие подписи.
    os.environ.setdefault("GDAL_HTTP_RETRY_CODES", "429,500,502,503,504")

    # Склейка диапазонов здесь намеренно НЕ включается — ни
    # GDAL_HTTP_MULTIRANGE, ни GDAL_HTTP_MERGE_CONSECUTIVE_RANGES.
    # Идея выглядит правильной: объединить соседние range-запросы в один и
    # сэкономить круги. На хранилище Azure, где лежит Sentinel-2, это дало
    # поток «Request for ... failed with response_code=206» — при том что
    # 206 это успешный ответ, — и плитку, висящую двадцать минут вместо
    # трёх. Оптимизация, не проверенная замером, обошлась дороже своего
    # отсутствия.


def configure_console() -> None:
    """Перевести stdout/stderr в UTF-8.

    На Windows поток по умолчанию открыт в кодовой странице системы
    (cp1251), в которой нет ни ``²``, ни ``₸``, ни ``→``. Любая команда,
    печатающая «км²» или сумму в тенге, падает с ``UnicodeEncodeError`` —
    то есть ``vantage info`` не работает на чистой машине вообще.

    ``errors="replace"`` оставлен намеренно: если консоль всё же не умеет
    UTF-8, лучше показать вопросительный знак вместо символа, чем уронить
    команду на выводе.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # поток подменён (тесты, перенаправление)
            continue
        with contextlib.suppress(ValueError, OSError):  # экзотические потоки
            reconfigure(encoding="utf-8", errors="replace")


__all__ = [
    "CA_ENV_VARS",
    "CA_FILENAME",
    "ansi_encodable",
    "configure",
    "configure_console",
    "ensure_gdal_ca_bundle",
]
