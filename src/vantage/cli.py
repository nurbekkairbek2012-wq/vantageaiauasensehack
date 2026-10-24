"""Командный интерфейс VANTAGE.

Каждый шаг пайплайна — отдельная команда с явными входом и выходом на диске.
Это принципиально: на защите нужно уметь показать промежуточный артефакт
любого шага, а не «чёрный ящик, который что-то посчитал за ночь».

    vantage info                  — что сконфигурировано
    vantage doctor                — проверка готовности к сдаче
    vantage scenes                — сколько сцен доступно по AOI и периоду
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import __version__
from .aoi import AOI
from .config import load_economics, load_settings

app = typer.Typer(
    name="vantage",
    help="VANTAGE — обнаружение несанкционированных свалок по спутниковым данным.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=verbose)],
    )


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный лог"),
) -> None:
    _setup_logging(verbose)


@app.command()
def version() -> None:
    """Версия пакета."""
    console.print(f"VANTAGE [bold]{__version__}[/bold]")


@app.command()
def info(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Путь к default.yaml"),
) -> None:
    """Показать текущую конфигурацию: область, период, ключевые пороги."""
    settings = load_settings(config)
    aoi = AOI.from_settings(settings)

    table = Table(title="Конфигурация VANTAGE", show_header=False, title_style="bold")
    table.add_column("Параметр", style="cyan", no_wrap=True)
    table.add_column("Значение")

    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    table.add_row("Область", f"{aoi.name}")
    table.add_row("bbox (WGS84)", f"{min_lon:.4f}, {min_lat:.4f}, {max_lon:.4f}, {max_lat:.4f}")
    table.add_row("Площадь", f"{aoi.area_km2:,.0f} км²".replace(",", " "))
    table.add_row("Рабочая проекция", settings.project.crs_working)
    table.add_row("Период", f"{settings.time.start} .. {settings.time.end}")
    table.add_row("Месяцы анализа", ", ".join(map(str, settings.time.valid_months)))
    table.add_row("Sentinel-2", f"{settings.sentinel2.collection}, каналы {', '.join(settings.sentinel2.bands)}")
    table.add_row("Порог падения NDVI", f"{settings.change.min_ndvi_drop}")
    table.add_row("Порог роста BSI", f"{settings.change.min_bsi_rise}")
    table.add_row(
        "Кольцо от жилья",
        f"{settings.context.min_distance_to_settlement_m:,.0f}..{settings.context.max_distance_to_settlement_m:,.0f} м".replace(",", " "),
    )
    table.add_row("Площадь кандидата", f"{settings.context.min_area_m2:,.0f}..{settings.context.max_area_m2:,.0f} м²".replace(",", " "))
    console.print(table)


@app.command()
def doctor(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Проверка готовности к сдаче.

    Главное, что проверяется, — не код, а честность цифр: ни один
    экономический параметр не должен остаться без источника. На Q&A вопрос
    «откуда эта цифра» задают всегда, и ответ «мы взяли примерно» стоит
    дороже, чем отсутствие самого расчёта.
    """
    problems: list[str] = []
    warnings: list[str] = []

    settings = load_settings(config)
    aoi = AOI.from_settings(settings)
    if aoi.area_km2 > 20_000:
        warnings.append(f"AOI очень большая ({aoi.area_km2:,.0f} км²) — полный прогон будет долгим")

    econ = load_economics()
    todos = econ.unresolved_sources()
    for path in todos:
        problems.append(f"economics: не указан источник для «{path}»")

    if "TODO" in str(econ.raw.get("penalty", {}).get("code_article", "")):
        problems.append("economics: не указана статья Экологического кодекса РК")

    console.rule("[bold]Проверка готовности")
    if problems:
        console.print(f"[bold red]Проблем: {len(problems)}[/bold red]")
        for p in problems:
            console.print(f"  [red]✗[/red] {p}")
    else:
        console.print("[bold green]✓ Блокирующих проблем нет[/bold green]")

    for w in warnings:
        console.print(f"  [yellow]![/yellow] {w}")

    if problems:
        raise typer.Exit(code=1)


@app.command()
def scenes(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    collection: str = typer.Option("s2", "--collection", help="s2 | s1 | landsat"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Сохранить список в JSON"),
) -> None:
    """Сколько сцен реально доступно по нашей области и периоду.

    Первая команда, которую стоит запустить: если сцен мало, все дальнейшие
    рассуждения о временных рядах не имеют смысла.
    """
    from .catalog import StacCatalog, summarize

    settings = load_settings(config)
    aoi = AOI.from_settings(settings)
    cat = StacCatalog()

    with console.status(f"Запрос к STAC ({collection})…"):
        if collection == "s2":
            found = cat.search_sentinel2(aoi, settings)
        elif collection == "s1":
            found = cat.search_sentinel1(aoi, settings)
        elif collection == "landsat":
            found = cat.search_landsat(aoi, settings)
        else:
            raise typer.BadParameter("collection должен быть s2, s1 или landsat")

    stats = summarize(found)
    if not stats["count"]:
        console.print("[red]Сцен не найдено — проверьте AOI и период.[/red]")
        raise typer.Exit(code=1)

    table = Table(title=f"Сцены: {collection}", show_header=False)
    table.add_column("", style="cyan")
    table.add_column("")
    table.add_row("Всего сцен", str(stats["count"]))
    table.add_row("Первая", stats["first"])
    table.add_row("Последняя", stats["last"])
    if stats["mean_cloud_pct"] is not None:
        table.add_row("Средняя облачность", f"{stats['mean_cloud_pct']}%")
    table.add_row("По годам", ", ".join(f"{y}: {n}" for y, n in stats["per_year"].items()))
    console.print(table)

    if output:
        payload = [
            {"id": s.id, "datetime": s.datetime, "cloud_cover": s.cloud_cover, "bbox": list(s.bbox)}
            for s in found
        ]
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        console.print(f"Сохранено: [green]{output}[/green]")


if __name__ == "__main__":  # pragma: no cover
    app()
