"""Сертификаты для GDAL на пути с казахскими буквами.

Эта проверка существует потому, что однажды сама себя отключила.

Путь проекта содержит ``Жұмыс үстелі``. Буквы ``ұ`` и ``ү`` отсутствуют в
кодовой странице 1251, а именно её применяет schannel, открывая файл
сертификатов по указанию GDAL. Файл не открывается — и каждое чтение COG
по HTTPS отваливается с «HTTP error code: 0».

Защита от этого была написана сразу: если путь не представим в кодировке
системы, рядом кладётся ASCII-копия. Проверка представимости брала
кодировку из ``locale.getpreferredencoding``.

Потом проект перевели на ``PYTHONUTF8=1`` — ради казахского вывода в
консоль. В режиме UTF-8 Python отвечает на этот вопрос «UTF-8», потому что
говорит о себе, а не о системе. Путь с ``ұ`` в UTF-8 представим прекрасно,
проверка стала возвращать True, копия перестала создаваться, и загрузка
снимков умерла целиком. Снаружи это выглядело как плохая сеть — тем
убедительнее, что requests те же ссылки скачивал: у него свои сертификаты.

Три прогона по кольцу ушли на поиски. Тесты ниже держат обе стороны:
кодировка спрашивается у системы, и режим UTF-8 на ответ не влияет.
"""

from __future__ import annotations

import sys

import pytest

from vantage.env import ansi_encodable, system_ansi_codepage

KAZAKH_PATH = r"C:\Users\Админ\OneDrive\Жұмыс үстелі\проект\cacert.pem"
RUSSIAN_PATH = r"C:\Users\Админ\Рабочий стол\проект\cacert.pem"
ASCII_PATH = r"C:\ProgramData\vantage\vantage_cacert.pem"


@pytest.mark.skipif(sys.platform != "win32", reason="кодовые страницы — свойство Windows")
class TestCodepage:
    def test_asks_the_system_not_python(self):
        """Ответ приходит от Windows, а не от режима интерпретатора."""
        codepage = system_ansi_codepage()
        assert codepage is not None
        assert codepage.startswith("cp"), f"ожидалась кодовая страница вида cpNNNN, получено {codepage}"

    def test_utf8_mode_does_not_change_the_answer(self):
        """Тот самый случай: PYTHONUTF8 не должен влиять на ответ.

        Проверка косвенная и потому надёжная: тесты и так идут в режиме
        UTF-8, и если бы кодировка бралась у Python, здесь стояло бы
        «utf-8», а казахский путь считался бы пригодным.
        """
        assert system_ansi_codepage() != "utf-8"

    def test_kazakh_path_is_rejected(self):
        """Ради этого всё и написано."""
        if system_ansi_codepage() != "cp1251":
            pytest.skip("проверка имеет смысл только на кириллической кодовой странице")
        assert ansi_encodable(KAZAKH_PATH) is False

    def test_russian_path_is_accepted(self):
        """Кириллица в 1251 представима — копировать сертификаты незачем.

        Обратная сторона важна не меньше: слишком строгая проверка
        заставляла бы плодить копии там, где всё и так работает.
        """
        if system_ansi_codepage() != "cp1251":
            pytest.skip("проверка имеет смысл только на кириллической кодовой странице")
        assert ansi_encodable(RUSSIAN_PATH) is True

    def test_ascii_path_is_always_fine(self):
        assert ansi_encodable(ASCII_PATH) is True


class TestBundleIsUsable:
    def test_configured_bundle_is_readable_by_the_system(self):
        """То, на что указывает переменная, должно открываться средствами ОС.

        Проверяется не наличие файла, а именно представимость пути: файл
        может лежать на месте и всё равно быть недоступен GDAL.
        """
        import os
        from pathlib import Path

        bundle = os.environ.get("GDAL_CURL_CA_BUNDLE")
        if not bundle:
            pytest.skip("переменная не выставлена — окружение не готовили")
        assert Path(bundle).exists(), f"файл сертификатов пропал: {bundle}"
        assert ansi_encodable(bundle), (
            f"путь к сертификатам {bundle} не представим в кодировке системы — "
            "чтение растров по HTTPS работать не будет"
        )
