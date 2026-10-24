"""HTTP-сервис VANTAGE.

Два контура в одном приложении:

    /public/*   — открытая часть. Агрегированные зоны риска, сводная
                  статистика. Ни точных координат, ни сумм по объектам,
                  ни актов. Токен не нужен.

    /private/*  — закрытая часть для оператора вывоза и экологической
                  службы. Точные координаты, доказательная цепочка,
                  оценка ущерба, формирование и подтверждение актов.
                  Каждое обращение записывается в журнал.

Реплика на защите звучит так: «обычный житель видит зону, служба —
точку и акт». Это не ограничение функциональности, а осознанное
разделение: карта, основанная на вероятностной модели и привязанная
к конкретным дворам, работала бы как публичное обвинение людей,
которых никто не проверял.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import load_economics, load_settings
from .security import (
    CAN_APPROVE_ACTS,
    AccessLog,
    Role,
    TokenRegistry,
    has_at_least,
)
from .store import Store, candidate_to_record

log = logging.getLogger(__name__)


def create_app(
    *,
    store: Store | None = None,
    tokens: TokenRegistry | None = None,
    access_log: AccessLog | None = None,
    artifacts_dir: str | Path | None = None,
):
    """Собрать приложение FastAPI.

    Все зависимости передаются снаружи — так их можно подменить в тестах
    и не поднимать реальный пайплайн ради проверки прав доступа.
    """
    from fastapi import Depends, FastAPI, Header, HTTPException, Query
    from fastapi.responses import FileResponse

    settings = load_settings()
    economics = load_economics()

    data = store if store is not None else Store.load(artifacts_dir or settings.paths.resolve("outputs"))
    registry = tokens if tokens is not None else TokenRegistry.from_env()
    journal = access_log if access_log is not None else AccessLog(
        settings.paths.resolve("outputs") / "access.log"
    )
    acts_dir = settings.paths.resolve("outputs") / "acts"

    app = FastAPI(
        title="VANTAGE API",
        version="0.1.0",
        description=(
            "Обнаружение несанкционированных свалок по спутниковым данным. "
            "Публичный контур отдаёт зоны риска, закрытый — точные объекты и акты."
        ),
    )

    # ------------------------------------------------------------------ #
    #  Аутентификация
    # ------------------------------------------------------------------ #

    def current_role(authorization: str | None = Header(default=None)) -> Role:
        """Определить роль запроса. Без заголовка — публичный доступ.

        Зависимость объявляется через значение по умолчанию
        (``role: Role = Depends(current_role)``), а не через
        ``Annotated[Role, Depends(...)]``. Причина: в модуле включён
        ``from __future__ import annotations``, поэтому все аннотации —
        строки, и FastAPI разрешает их через глобали модуля. Локальный
        алиас, объявленный внутри create_app, в глобали не попадает,
        и параметр молча превращается в обязательный query-параметр.
        Сбой проявляется как 422 на каждой защищённой ручке.
        """
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return registry.resolve(token)

    def require_role(role: Role, minimum: Role, action: str, target: str) -> None:
        if not has_at_least(role, minimum):
            # 404, а не 403: сам факт существования объекта с таким
            # идентификатором — уже информация, которой публичная роль
            # обладать не должна.
            raise HTTPException(status_code=404, detail="объект не найден")
        journal.record(role, action, target)

    # ------------------------------------------------------------------ #
    #  Служебное
    # ------------------------------------------------------------------ #

    @app.get("/health", tags=["служебное"])
    def health() -> dict:
        return {
            "status": "ok",
            "objects": data.n_candidates,
            "tokens_configured": len(registry),
        }

    # ------------------------------------------------------------------ #
    #  Публичный контур
    # ------------------------------------------------------------------ #

    @app.get("/public/summary", tags=["публичное"])
    def public_summary() -> dict:
        """Сводка без адресных данных: сколько объектов, площадь, ущерб суммарно."""
        summary = data.summary()
        summary["disclaimer"] = (
            "Данные получены методом дистанционного зондирования и являются оценкой "
            "вероятности, а не результатом проверки. Точные координаты доступны "
            "уполномоченным организациям."
        )
        return summary

    @app.get("/public/risk", tags=["публичное"])
    def public_risk() -> dict:
        """Зоны риска укрупнённой сеткой, только класс риска.

        Точная вероятность и точные границы здесь не отдаются намеренно —
        см. модуль :mod:`vantage.risk`, функция ``aggregate_public``.
        """
        if data.risk_public is None:
            return {"type": "FeatureCollection", "features": []}
        public = data.risk_public
        columns = [c for c in ("risk_class",) if c in public.columns]
        return public[[*columns, "geometry"]].to_crs("EPSG:4326").__geo_interface__

    # ------------------------------------------------------------------ #
    #  Закрытый контур
    # ------------------------------------------------------------------ #

    @app.get("/private/candidates", tags=["закрытое"])
    def list_candidates(
        role: Role = Depends(current_role),
        limit: int = Query(200, ge=1, le=2000),
        min_probability: float = Query(0.0, ge=0.0, le=1.0),
    ) -> dict:
        """Список объектов с точными координатами."""
        require_role(role, "operator", "list_candidates", f"limit={limit}")

        if data.candidates is None or data.candidates.empty:
            return {"count": 0, "items": []}

        rows = data.candidates.to_crs("EPSG:4326")
        if "probability" in rows.columns and min_probability > 0:
            rows = rows[rows["probability"] >= min_probability]
        rows = rows.head(limit)

        return {
            "count": len(rows),
            "items": [candidate_to_record(row) for _, row in rows.iterrows()],
        }

    @app.get("/private/candidates/{candidate_id}", tags=["закрытое"])
    def get_candidate(candidate_id: str, role: Role = Depends(current_role)) -> dict:
        """Карточка объекта: координаты, доказательства, ущерб."""
        require_role(role, "operator", "get_candidate", candidate_id)

        row = data.get_candidate(candidate_id)
        if row is None:
            raise HTTPException(status_code=404, detail="объект не найден")

        from ..explain import physical_evidence

        wgs = data.candidates.to_crs("EPSG:4326")
        wgs_row = wgs[wgs["candidate_id"] == candidate_id].iloc[0]
        record = candidate_to_record(wgs_row, include_geometry=True)

        evidence = physical_evidence(
            candidate_id,
            ndvi_drop=float(row.get("ndvi_drop", float("nan"))),
            bsi_rise=float(row.get("bsi_rise", float("nan"))),
            probability=record.get("probability"),
        )
        record["evidence"] = {
            "text": evidence.to_text(),
            "signals": evidence.strength,
            "n_agreeing": evidence.n_agreeing,
            "combined_score": evidence.combined_score,
        }
        return record

    @app.post("/private/candidates/{candidate_id}/act", tags=["закрытое"])
    def create_act(candidate_id: str, role: Role = Depends(current_role)) -> dict:
        """Сформировать ЧЕРНОВИК акта.

        Именно черновик. Официальным документ становится только после
        подтверждения человеком через отдельную ручку.
        """
        require_role(role, "operator", "create_act", candidate_id)

        row = data.get_candidate(candidate_id)
        if row is None:
            raise HTTPException(status_code=404, detail="объект не найден")

        from ..act import ActDraft, render_pdf
        from ..explain import physical_evidence
        from ..money import assess

        wgs = data.candidates.to_crs("EPSG:4326")
        wgs_row = wgs[wgs["candidate_id"] == candidate_id].iloc[0]

        assessment = assess(float(row.get("area_m2", 0.0)) or 1.0, economics)
        evidence = physical_evidence(
            candidate_id,
            ndvi_drop=float(row.get("ndvi_drop", float("nan"))),
            bsi_rise=float(row.get("bsi_rise", float("nan"))),
        )
        act = ActDraft.from_pipeline(wgs_row, assessment, evidence, economics)
        data.acts[candidate_id] = act

        path = acts_dir / f"{candidate_id}_draft.pdf"
        render_pdf(act, path)

        return {
            "candidate_id": candidate_id,
            "status": act.status,
            "pdf": f"/private/acts/{candidate_id}/pdf",
            "warning": "документ является черновиком и требует подтверждения человеком",
        }

    @app.post("/private/acts/{candidate_id}/approve", tags=["закрытое"])
    def approve_act(
        candidate_id: str,
        reviewer_name: str = Query(..., min_length=2),
        reviewer_position: str = Query(..., min_length=2),
        note: str = Query(""),
        role: Role = Depends(current_role),
    ) -> dict:
        """Подтвердить акт человеком.

        Доступно только экологической службе. Оператору вывоза —
        сознательно нет: у него коммерческий интерес в объёме работ,
        и подтверждать собственный заказ он не должен.
        """
        if role not in CAN_APPROVE_ACTS:
            raise HTTPException(
                status_code=403,
                detail="подтверждать акты может только экологическая служба",
            )
        journal.record(role, "approve_act", candidate_id)

        act = data.acts.get(candidate_id)
        if act is None:
            raise HTTPException(status_code=404, detail="черновик акта не найден, сформируйте его")
        if act.is_official:
            raise HTTPException(status_code=409, detail="акт уже подтверждён")

        from ..act import render_pdf

        act.approve(reviewer_name, reviewer_position, note=note)
        path = acts_dir / f"{candidate_id}_approved.pdf"
        render_pdf(act, path, allow_draft=False)

        return {
            "candidate_id": candidate_id,
            "status": act.status,
            "reviewer": reviewer_name,
            "position": reviewer_position,
            "pdf": f"/private/acts/{candidate_id}/pdf",
        }

    @app.get("/private/acts/{candidate_id}/pdf", tags=["закрытое"])
    def download_act(candidate_id: str, role: Role = Depends(current_role)):
        """Скачать акт. Отдаётся подтверждённая версия, если она есть."""
        require_role(role, "operator", "download_act", candidate_id)

        act = data.acts.get(candidate_id)
        if act is None:
            raise HTTPException(status_code=404, detail="акт не найден")

        suffix = "approved" if act.is_official else "draft"
        path = acts_dir / f"{candidate_id}_{suffix}.pdf"
        if not path.exists():
            raise HTTPException(status_code=404, detail="файл акта не найден")
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    @app.get("/private/access-log", tags=["закрытое"])
    def access_log_view(
        limit: int = Query(50, ge=1, le=500),
        role: Role = Depends(current_role),
    ) -> dict:
        """Журнал обращений к точным данным.

        Виден только службе: это контроль за тем, кто и когда смотрел
        адресные данные.
        """
        if not has_at_least(role, "akimat"):
            raise HTTPException(status_code=404, detail="не найдено")
        return {"entries": journal.recent(limit)}

    app.state.store = data
    app.state.tokens = registry
    app.state.access_log = journal
    return app


__all__ = ["create_app"]
