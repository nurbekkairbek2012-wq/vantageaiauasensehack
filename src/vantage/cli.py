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
from pathlib import Path

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
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
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


@app.command()
def sample(
    n: int = typer.Option(24, "--count", "-n", help="Сколько объектов сгенерировать"),
    seed: int = typer.Option(42, "--seed"),
    output: str | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Сгенерировать СИНТЕТИЧЕСКИЕ артефакты для отладки интерфейса.

    Нужно, чтобы разрабатывать карту и репетировать выступление, не
    дожидаясь полного прогона. Каждый файл помечен флагом is_demo,
    и карта показывает красную полосу, когда его видит.
    """
    from .demo import generate_all

    written = generate_all(output, n=n, seed=seed)
    console.print("[bold red]Это синтетические данные для отладки интерфейса.[/bold red]")
    console.print("На защите показывайте результат настоящего прогона.\n")
    for name, path in written.items():
        console.print(f"  [green]{name}[/green]: {path}")


@app.command()
def web(
    port: int = typer.Option(8080, "--port", "-p"),
    outputs: str | None = typer.Option(None, "--outputs", help="Каталог с артефактами"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Поднять карту локально.

    Артефакты копируются в web/data/ — фронтенд читает только оттуда и
    ничего не знает про пайплайн. Это же делает демонстрацию переносимой:
    каталог web/ целиком работает на любой машине без Python.
    """
    import http.server
    import shutil
    import socketserver
    import threading
    import webbrowser

    from .config import REPO_ROOT

    settings = load_settings()
    source = Path(outputs) if outputs else settings.paths.resolve("outputs")
    web_root = REPO_ROOT / "web"
    data_dir = web_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for name in ("candidates.geojson", "risk_public.geojson", "risk_private.geojson", "story.json"):
        src = source / name
        if src.exists():
            shutil.copy2(src, data_dir / name)
            copied += 1

    if copied == 0:
        console.print(
            "[yellow]Артефактов не найдено.[/yellow] "
            "Запустите [bold]vantage sample[/bold] для отладочных данных "
            "или [bold]vantage run[/bold] для настоящего прогона."
        )
    else:
        console.print(f"Скопировано артефактов: [green]{copied}[/green] → {data_dir}")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_root), **kwargs)

        def end_headers(self):
            # Оболочка не кешируется браузером: во время отладки это
            # экономит часы на «почему не обновилось».
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):  # тише в консоли
            pass

    url = f"http://127.0.0.1:{port}/index.html"
    console.print(f"Карта: [bold cyan]{url}[/bold cyan]  (Ctrl+C — остановить)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            console.print("\nОстановлено.")


#: Единственные файлы, которые разрешено публиковать в открытый доступ.
#: Список намеренно короткий и живёт в коде, а не в скрипте деплоя:
#: расширить его должно быть заметным действием, а не правкой одной
#: строки в YAML, которую никто не проверит.
PUBLIC_WHITELIST = ("candidates.geojson", "risk_public.geojson", "registry.geojson", "story.json")

#: Файлы, попадание которых в публикацию — утечка адресных данных.
PUBLIC_DENYLIST = ("risk_private.geojson", "access.log", "citizen_reports.jsonl")


@app.command()
def publish(
    outputs: str | None = typer.Option(None, "--outputs", help="Откуда брать артефакты"),
    target: str = typer.Option("map-data", "--target", help="Куда складывать для деплоя"),
    allow_demo: bool = typer.Option(False, "--allow-demo", help="Разрешить синтетические данные"),
) -> None:
    """Подготовить данные карты к публикации в интернет.

    Копирует в ``map-data/`` только белый список файлов и проверяет, что
    в публикацию не попал ни один закрытый слой. Отсюда их забирают
    Vercel и GitHub Pages.

    Проверка идёт по факту наличия файла, а не по намерению: белый
    список легко случайно расширить, и без падающей команды этого никто
    не заметит до того момента, когда точные координаты уже в открытом
    доступе.
    """
    import json as _json
    import shutil

    from .config import REPO_ROOT

    settings = load_settings()
    source = Path(outputs) if outputs else settings.paths.resolve("outputs")
    destination = Path(target)
    if not destination.is_absolute():
        destination = REPO_ROOT / destination

    if not source.exists():
        console.print(f"[red]Каталог с артефактами не найден: {source}[/red]")
        console.print("Запустите [bold]vantage run[/bold] или [bold]vantage sample[/bold].")
        raise typer.Exit(code=1)

    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.iterdir():
        if stale.is_file():
            stale.unlink()

    copied: list[str] = []
    for name in PUBLIC_WHITELIST:
        src = source / name
        if src.exists():
            shutil.copy2(src, destination / name)
            copied.append(name)

    if not copied:
        console.print(f"[red]В {source} нет ни одного публикуемого файла.[/red]")
        raise typer.Exit(code=1)

    # --- Проверка утечки ------------------------------------------------ #
    leaks: list[str] = []
    for name in PUBLIC_DENYLIST:
        if (destination / name).exists():
            leaks.append(f"закрытый слой {name}")

    for path in destination.glob("*.geojson"):
        payload = _json.loads(path.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            if "risk" in (feature.get("properties") or {}):
                leaks.append(f"{path.name}: поле risk — точная вероятность")
                break

    if leaks:
        console.print("[bold red]Публикация отменена — обнаружена утечка:[/bold red]")
        for leak in leaks:
            console.print(f"  [red]x[/red] {leak}")
        raise typer.Exit(code=1)

    # --- Проверка на синтетику ------------------------------------------ #
    is_demo = False
    story = destination / "story.json"
    if story.exists():
        is_demo = bool(_json.loads(story.read_text(encoding="utf-8")).get("is_demo"))

    table = Table(title="Готово к публикации", show_header=True)
    table.add_column("Файл", style="cyan")
    table.add_column("Размер", justify="right")
    total = 0
    for name in copied:
        size = (destination / name).stat().st_size
        total += size
        table.add_row(name, f"{size / 1024:,.0f} КБ".replace(",", " "))
    console.print(table)
    console.print(f"Всего: [green]{total / 1024:,.0f} КБ[/green]".replace(",", " "))
    console.print("[green]Закрытых слоёв в публикации нет.[/green]")

    if is_demo:
        console.print(
            "\n[bold red]ВНИМАНИЕ: это синтетические данные.[/bold red] "
            "Карта покажет предупреждающую полосу."
        )
        if not allow_demo:
            console.print(
                "Публикация синтетики в интернет требует явного согласия: "
                "повторите с флагом [bold]--allow-demo[/bold]."
            )
            raise typer.Exit(code=1)

    console.print(f"\nДальше: [bold]git add {target} && git commit && git push[/bold]")


@app.command()
def run(
    config: str | None = typer.Option(None, "--config", "-c"),
    force: bool = typer.Option(False, "--force", help="Пересчитать даже готовые шаги"),
    skip_risk: bool = typer.Option(False, "--skip-risk", help="Пропустить модель прогноза"),
) -> None:
    """Сквозной прогон пайплайна по области из конфигурации.

    Каждый шаг пишет артефакт в outputs/ и пропускается, если результат
    уже есть. Полный прогон по области идёт часами, и падение на седьмом
    шаге не должно означать повтор первых шести.
    """
    from .pipeline import Pipeline, timed

    settings = load_settings(config)
    pipeline = Pipeline(settings, force=force)

    console.rule(f"[bold]Прогон {pipeline.aoi.name}")
    console.print(f"Область: {pipeline.aoi.area_km2:,.0f} км²".replace(",", " "))
    console.print(f"Период: {settings.time.start} .. {settings.time.end}\n")

    with console.status("Поиск сцен в STAC…"):
        stats, seconds = timed(pipeline.step_scenes)
    found = stats.get("sentinel2", {}).get("count", 0)
    pipeline.report.record("scenes", seconds=seconds, sentinel2=found)
    console.print(f"[green]✓[/green] Сцен Sentinel-2: {found}")

    if not found:
        console.print("[red]Сцен не найдено — дальнейшие шаги не имеют смысла.[/red]")
        raise typer.Exit(code=1)

    console.print(
        "\n[yellow]Загрузка растров и детекция изменений по всей области "
        "занимают часы и требуют устойчивой сети.[/yellow]\n"
        "Запускайте их потайлово через Python API:\n"
        "  [dim]from vantage.pipeline import Pipeline[/dim]\n"
        "  [dim]for tile in aoi.tiles(20_000): ...[/dim]\n"
    )
    report_path = pipeline.finish()
    console.print(f"Отчёт о прогоне: [green]{report_path}[/green]")


if __name__ == "__main__":  # pragma: no cover
    app()
