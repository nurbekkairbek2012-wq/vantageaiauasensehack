"""Тесты генератора акта.

Главное, что проверяется, — не вёрстка PDF, а правило
«модель предлагает, человек подтверждает». Автоматически сгенерированный
юридический документ на основе вероятностной модели — прямая
ответственность, и защита от случайной выгрузки должна быть в коде,
а не в договорённости между участниками команды.
"""

from __future__ import annotations

from datetime import date

import pytest

reportlab = pytest.importorskip("reportlab", reason="нужен reportlab (pip install -e .[service])")

from vantage.act import (  # noqa: E402
    ActDraft,
    ActNotApprovedError,
    Approval,
    register_cyrillic_font,
    render_pdf,
)


def make_act(**overrides) -> ActDraft:
    defaults = dict(
        candidate_id="C00042",
        latitude=51.208134,
        longitude=71.612455,
        area_m2=5_400.0,
        detected_date=date(2026, 8, 17),
        appeared_date=date(2022, 5, 15),
        evidence_text="сработало признаков: 4 из 5. падение растительности — 86%; рост открытого грунта — 74%",
        signals={"ndvi_drop": 0.86, "bsi_rise": 0.74, "pmli_response": 0.41},
        model_probability=0.91,
        damage_p10_kzt=3_912_644,
        damage_p50_kzt=13_268_247,
        damage_p90_kzt=25_960_534,
        mass_t_p50=1_620.0,
        co2e_t_p50=310.0,
        penalty_article="ст. 344, ч. 2-1",
        penalty_article_title=(
            "Образование стихийных свалок (выброс отходов вне специально установленных "
            "мест) с использованием транспортных средств"
        ),
        penalty_mrp=100,
        penalty_kzt=432_500.0,
        verification_providers=2,
        verification_texture=0.68,
    )
    defaults.update(overrides)
    return ActDraft(**defaults)


# --------------------------------------------------------------------------- #
#  Жизненный цикл документа
# --------------------------------------------------------------------------- #


class TestApprovalWorkflow:
    def test_new_act_is_a_draft(self):
        act = make_act()
        assert act.status == "draft"
        assert not act.is_official

    def test_approval_makes_it_official(self):
        act = make_act().approve("Абикаев Сабырали", "технический координатор")
        assert act.status == "approved"
        assert act.is_official
        assert act.approval.reviewer_name == "Абикаев Сабырали"

    def test_approval_records_position_and_time(self):
        act = make_act().approve("Иванов И.И.", "главный специалист", note="выезд подтвердил")
        assert act.approval.reviewer_position == "главный специалист"
        assert act.approval.note == "выезд подтвердил"
        assert act.approval.approved_at is not None

    def test_cannot_approve_without_name(self):
        """Подпись «система» юридически бессмысленна."""
        with pytest.raises(ValueError, match="имени"):
            make_act().approve("   ", "должность")

    def test_cannot_approve_without_position(self):
        with pytest.raises(ValueError, match="должност"):
            make_act().approve("Иванов И.И.", "")

    def test_cannot_approve_twice(self):
        act = make_act().approve("Иванов И.И.", "специалист")
        with pytest.raises(RuntimeError, match="уже подтверждён"):
            act.approve("Петров П.П.", "другой специалист")

    def test_approval_requires_explicit_call(self):
        """Статус нельзя получить побочным эффектом — только явным вызовом."""
        act = make_act()
        render_pdf(act, _tmp_path())
        assert act.status == "draft"


class TestStrictExport:
    def test_official_export_of_draft_is_blocked(self, tmp_path):
        """Ключевая защита: непроверенный документ не выгружается
        как официальный, а падает с внятной ошибкой."""
        with pytest.raises(ActNotApprovedError, match="не подтверждён"):
            render_pdf(make_act(), tmp_path / "act.pdf", allow_draft=False)

    def test_official_export_works_after_approval(self, tmp_path):
        act = make_act().approve("Иванов И.И.", "специалист")
        path = render_pdf(act, tmp_path / "act.pdf", allow_draft=False)
        assert path.exists()

    def test_draft_export_is_allowed_by_default(self, tmp_path):
        """Черновик посмотреть можно — просто он помечен как черновик."""
        path = render_pdf(make_act(), tmp_path / "draft.pdf")
        assert path.exists()


# --------------------------------------------------------------------------- #
#  Рендеринг
# --------------------------------------------------------------------------- #


class TestRendering:
    def test_cyrillic_font_is_available(self):
        """Без TTF с кириллицей PDF выйдет с пустыми квадратами.

        Ошибка тихая: документ создастся и будет нечитаемым.
        """
        regular, bold = register_cyrillic_font()
        assert regular and bold

    def test_produces_non_trivial_pdf(self, tmp_path):
        path = render_pdf(make_act(), tmp_path / "act.pdf")
        assert path.stat().st_size > 2_000
        assert path.read_bytes().startswith(b"%PDF")

    def test_draft_and_approved_differ(self, tmp_path):
        draft = render_pdf(make_act(), tmp_path / "draft.pdf").read_bytes()
        approved = render_pdf(
            make_act().approve("Иванов И.И.", "специалист"), tmp_path / "ok.pdf"
        ).read_bytes()
        assert draft != approved
        # Черновик несёт водяной знак и предупреждение — он крупнее
        assert len(draft) > 1_000

    def test_handles_missing_optional_fields(self, tmp_path):
        act = make_act(
            appeared_date=None,
            model_probability=None,
            evidence_text="",
            verification_providers=0,
            verification_texture=None,
        )
        assert render_pdf(act, tmp_path / "sparse.pdf").exists()

    def test_creates_parent_directory(self, tmp_path):
        path = render_pdf(make_act(), tmp_path / "deep" / "nested" / "act.pdf")
        assert path.exists()


class TestFormatting:
    def test_coordinates_are_navigator_ready(self):
        assert make_act().coordinates_text() == "51.208134, 71.612455"

    def test_month_is_russian_not_english(self):
        """strftime('%B') берёт локаль C и печатает May вместо мая.

        Сбой тихий: документ на русском языке выходит с английским
        месяцем, и это замечают только на защите.
        """
        from vantage.act import format_month_year

        assert format_month_year(date(2022, 5, 15)) == "мая 2022"
        assert format_month_year(date(2021, 12, 1)) == "декабря 2021"

    def test_all_months_are_covered(self):
        from vantage.act import format_month_year

        for month in range(1, 13):
            assert format_month_year(date(2024, month, 1)).split()[0].isalpha()

    def test_damage_is_shown_as_range(self):
        """Точечная цифра не переживает вопрос «откуда», диапазон переживает."""
        text = make_act().damage_text()
        assert "–" in text
        assert "медианная оценка" in text

    def test_large_numbers_use_thin_spaces(self):
        assert "3 912 644" in make_act().damage_text()


# --------------------------------------------------------------------------- #
#  Сборка из пайплайна
# --------------------------------------------------------------------------- #


class TestFromPipeline:
    def test_assembles_from_pipeline_outputs(self):
        import geopandas as gpd
        import numpy as np
        from shapely.geometry import box

        from vantage.config import load_economics
        from vantage.explain import physical_evidence
        from vantage.money import assess

        econ = load_economics()
        candidates = gpd.GeoDataFrame(
            {
                "candidate_id": ["C00007"],
                "area_m2": [5_400.0],
                "break_date": [np.datetime64("2022-05-15")],
                "probability": [0.88],
                "verify_providers": [2],
                "verify_texture": [0.61],
                "geometry": [box(71.60, 51.20, 71.62, 51.21)],
            },
            crs="EPSG:4326",
        )
        assessment = assess(5_400.0, econ, iterations=2_000, seed=1)
        evidence = physical_evidence("C00007", ndvi_drop=0.30, bsi_rise=0.20, probability=0.88)

        act = ActDraft.from_pipeline(candidates.iloc[0], assessment, evidence, econ)

        assert act.candidate_id == "C00007"
        assert act.penalty_article == "ст. 344, ч. 2-1"
        assert act.penalty_mrp == 100
        assert act.penalty_kzt == 100 * 4325
        assert act.appeared_date == date(2022, 5, 15)
        assert act.damage_p10_kzt < act.damage_p50_kzt < act.damage_p90_kzt
        assert act.status == "draft"

    def test_missing_break_date_does_not_crash(self):
        import geopandas as gpd
        import numpy as np
        from shapely.geometry import box

        from vantage.config import load_economics
        from vantage.money import assess

        econ = load_economics()
        row = gpd.GeoDataFrame(
            {
                "candidate_id": ["C1"],
                "area_m2": [1000.0],
                "break_date": [np.datetime64("NaT")],
                "geometry": [box(71.6, 51.2, 71.61, 51.21)],
            },
            crs="EPSG:4326",
        ).iloc[0]
        act = ActDraft.from_pipeline(row, assess(1000.0, econ, iterations=500, seed=2), None, econ)
        assert act.appeared_date is None


class TestApprovalDataclass:
    def test_rejects_blank_reviewer(self):
        with pytest.raises(ValueError):
            Approval(reviewer_name="", reviewer_position="специалист")


def _tmp_path():
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp()) / "act.pdf"
