"""Тесты подготовки окружения GDAL.

Модуль существует из-за конкретной поломки, а не из общих соображений.
Проект живёт по пути с казахскими буквами (``Жұмыс үстелі``). Библиотека
``pyogrio`` при импорте выставляет путь к сертификатам через
``os.environ.setdefault("GDAL_CURL_CA_BUNDLE", certifi.where())``, а certifi
лежит внутри проекта. Символов ``ұ``, ``ү``, ``і`` нет в кодовой странице
Windows, schannel получает битую строку и отбрасывает файл сертификатов::

    CURL error: schannel: invalid path name for CA file 'C:\\...\\Ж?мыс ?стел?\\...'

Следствие: ни один COG по HTTPS не читается. Весь пайплайн выше загрузки
растров работает, а сама загрузка падает — причём ошибку не видно, потому
что rasterio пытается декодировать сообщение GDAL как UTF-8, натыкается на
cp1251 и падает с ``UnicodeDecodeError``, потеряв исходный текст.

Проверяется здесь ровно то, что чинит поломку: распознавание непригодного
пути и то, что переменные выставляются **до** импорта pyogrio, а его
``setdefault`` уже ничего не переопределит.
"""

from __future__ import annotations

import locale
import os
import sys
from pathlib import Path

import pytest

from vantage.env import (
    CA_ENV_VARS,
    ansi_encodable,
    configure,
    configure_console,
    ensure_gdal_ca_bundle,
)


class TestAnsiEncodable:
    def test_plain_ascii_is_fine_everywhere(self):
        assert ansi_encodable(r"C:\ProgramData\vantage\cacert.pem")

    @pytest.mark.skipif(sys.platform != "win32", reason="проверка про кодовую страницу Windows")
    def test_kazakh_letters_are_not_representable_in_cp1251(self):
        """Тот самый случай: кириллица проходит, казахские буквы — нет."""
        if locale.getpreferredencoding(False).lower() not in {"cp1251", "windows-1251"}:
            pytest.skip("система не на cp1251")
        assert ansi_encodable(r"C:\Users\Админ\cacert.pem")
        assert not ansi_encodable("C:\\Users\\Жұмыс үстелі\\cacert.pem")

    @pytest.mark.skipif(sys.platform == "win32", reason="вне Windows ограничения нет")
    def test_outside_windows_everything_is_representable(self):
        assert ansi_encodable("/home/пользователь/Жұмыс/cacert.pem")


class TestCaBundle:
    def test_result_is_usable_or_absent(self):
        """Функция либо даёт существующий файл, либо честно ничего не делает."""
        result = ensure_gdal_ca_bundle()
        if result is not None:
            assert Path(result).exists()
            assert ansi_encodable(result)

    def test_is_idempotent(self):
        first = ensure_gdal_ca_bundle()
        second = ensure_gdal_ca_bundle()
        assert first == second

    def test_all_variants_point_to_one_file(self):
        """GDAL, PROJ и curl ищут сертификаты разными переменными.

        Выставить одну из трёх — значит починить одну библиотеку из трёх
        и получить ту же ошибку на следующем шаге пайплайна.
        """
        if ensure_gdal_ca_bundle() is None:
            pytest.skip("путь к certifi и так пригоден — переменные не трогаются")
        values = {os.environ[name] for name in CA_ENV_VARS if name in os.environ}
        assert len(values) == 1


class TestConfigure:
    def test_sets_cloud_reading_options(self):
        configure()
        # Без этой опции GDAL на каждое открытие ассета перечисляет соседние
        # объекты в контейнере — лишний круг запросов на каждый снимок,
        # а снимков в прогоне полтысячи на плитку.
        assert os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] == "EMPTY_DIR"
        assert ".tif" in os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"]

    def test_does_not_override_explicit_settings(self):
        os.environ["GDAL_CACHEMAX"] = "64"
        try:
            configure()
            assert os.environ["GDAL_CACHEMAX"] == "64"
        finally:
            del os.environ["GDAL_CACHEMAX"]

    def test_console_reconfiguration_survives_replaced_streams(self, capsys):
        """CLI печатает «км²» и «₸» — в cp1251 этих символов нет.

        До перевода потока в UTF-8 команда ``vantage info`` падала с
        UnicodeEncodeError на выводе, то есть не работала вообще.
        Функция обязана молча пережить и подменённый поток тестов.
        """
        configure_console()
        print("4 834 км² · 12 млн ₸")
        assert "км" in capsys.readouterr().out
