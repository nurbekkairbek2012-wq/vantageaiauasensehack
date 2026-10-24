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
    config: str | None = typer.Option(None, "--config", "-c", help="Путь к default.yaml"),
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
    config: str | None = typer.Option(None, "--config", "-c"),
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
    for path in econ.unresolved_sources():
        problems.append(f"economics: происхождение параметра «{path}» помечено TODO")

    penalty = econ.raw.get("penalty", {})
    if not penalty.get("articles"):
        problems.append("economics: не заданы статьи КоАП для расчёта штрафа")
    if not econ.raw.get("mrp_kzt", {}).get("value"):
        problems.append("economics: не задан размер МРП")

    documented = econ.documented_parameters()
    estimated = econ.estimated_parameters()

    console.rule("[bold]Проверка готовности")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Происхождение параметров", style="cyan")
    table.add_column("Кол-во", justify="right")
    table.add_column("Что это значит")
    table.add_row("Подтверждено источником", str(len(documented)), "есть ссылка на закон, методику или прайс")
    table.add_row("Инженерная оценка", str(len(estimated)), "открытого источника нет; проверяется Монте-Карло")
    table.add_row("Не объяснено (TODO)", str(len(econ.unresolved_sources())), "блокирует сдачу")
    console.print(table)

    if estimated:
        console.print("\n[bold]На Q&A спросят именно про эти величины:[/bold]")
        for path in estimated:
            console.print(f"  [yellow]~[/yellow] {path}")

    console.print()
    if problems:
        console.print(f"[bold red]Блокирующих проблем: {len(problems)}[/bold red]")
        for p in problems:
            console.print(f"  [red]x[/red] {p}")
    else:
        console.print("[bold green]OK — блокирующих проблем нет[/bold green]")

    for w in warnings:
        console.print(f"  [yellow]![/yellow] {w}")

    if problems:
        raise typer.Exit(code=1)


@app.command()
def scenes(
    config: str | None = typer.Option(None, "--config", "-c"),
    collection: str = typer.Option("s2", "--collection", help="s2 | s1 | landsat"),
    output: str | None = typer.Option(None, "--output", "-o", help="Сохранить список в JSON"),
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


@app.command()
def money(
    area: float = typer.Argument(..., help="Площадь свалки, м²"),
    depth: str = typer.Option("medium", "--depth", help="Класс глубины: shallow | medium | deep"),
    distance: float = typer.Option(15.0, "--distance", help="Расстояние до полигона, км"),
    violator: str = typer.Option(
        "individual",
        "--violator",
        help="Категория нарушителя: individual | official_or_small | medium | large",
    ),
    show_sensitivity: bool = typer.Option(
        False, "--sensitivity", help="Показать вклад допущений в разброс"
    ),
) -> None:
    """Оценить ущерб от одного объекта.

    Результат — диапазон P10…P90, а не одна цифра: в расчёте участвуют
    восемь величин, известных лишь приблизительно, и честный интервал
    переживает вопрос «откуда цифра», а точечная оценка — нет.
    """
    from .money import assess
    from .money import sensitivity as compute_sensitivity

    econ = load_economics()
    result = assess(
        area,
        econ,
        depth_class=depth,  # type: ignore[arg-type]
        distance_to_landfill_km=distance,
        violator=violator,  # type: ignore[arg-type]
    )

    table = Table(title=f"Оценка ущерба: {area:,.0f} м²".replace(",", " "), show_header=False)
    table.add_column("", style="cyan", no_wrap=True)
    table.add_column("")
    for line in result.summary_lines():
        key, _, value = line.partition(": ")
        table.add_row(key, value)
    console.print(table)
    console.print(
        f"[dim]Монте-Карло, {result.iterations:,} итераций; "
        f"штраф считается отдельно и не входит в ущерб[/dim]".replace(",", " ")
    )

    if show_sensitivity:
        sens = compute_sensitivity(area, econ, depth_class=depth)  # type: ignore[arg-type]
        stable = Table(title="Вклад допущений в разброс", show_header=True)
        stable.add_column("Допущение", style="cyan")
        stable.add_column("Корреляция с итогом", justify="right")
        for name, value in sorted(sens.items(), key=lambda kv: -abs(kv[1])):
            stable.add_row(name, f"{value:+.2f}")
        console.print(stable)
        console.print(
            "[dim]Чем выше по модулю — тем сильнее эта величина определяет разброс "
            "и тем важнее уточнить её реальными данными.[/dim]"
        )


if __name__ == "__main__":  # pragma: no cover
    app()
