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
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import __version__
from .aoi import AOI
from .config import load_economics, load_settings
from .env import configure_console

# Вывод переводится в UTF-8 до создания Console: rich запоминает кодировку
# потока при инициализации, и после этого менять её поздно.
configure_console()

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


def _check_raster_access() -> tuple[bool, str]:
    """Прочитать окно настоящего COG из облака.

    Самая полезная проверка во всём ``doctor``, потому что ловит поломку,
    которая иначе выглядит как «интернет не работает». На пути с
    казахскими буквами GDAL не может открыть файл сертификатов, schannel
    отбрасывает его целиком, и ни один снимок не читается — при этом
    поиск сцен в STAC продолжает работать, а сообщение об ошибке
    теряется внутри rasterio (см. env.py). Отличить это от настоящих
    сетевых проблем можно только фактическим чтением пикселей.
    """
    import pystac_client
    import rasterio

    from .catalog import PLANETARY_COMPUTER_STAC

    try:
        import planetary_computer

        client = pystac_client.Client.open(
            PLANETARY_COMPUTER_STAC, modifier=planetary_computer.sign_inplace
        )
        item = next(
            iter(
                client.search(
                    collections=["sentinel-2-l2a"],
                    bbox=[71.40, 51.05, 71.50, 51.10],
                    datetime="2024-07-01/2024-07-31",
                    limit=1,
                ).items()
            )
        )
    except StopIteration:
        return False, "STAC не вернул ни одной сцены по контрольному запросу"
    except Exception as exc:
        return False, f"STAC недоступен: {type(exc).__name__}"

    try:
        with rasterio.open(item.assets["B04"].href) as dataset:
            dataset.read(1, window=((0, 64), (0, 64)))
    except UnicodeDecodeError:
        # Ровно тот случай: сообщение GDAL пришло в кодовой странице
        # системы, и rasterio не смог его прочитать.
        return False, "GDAL не может открыть файл сертификатов — проверьте GDAL_CURL_CA_BUNDLE"
    except Exception as exc:
        return False, f"чтение COG не удалось: {type(exc).__name__}"

    return True, f"снимок {item.id[:24]}… читается"


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", "-c"),
    network: bool = typer.Option(
        False, "--network", "-n", help="Проверить доступ к STAC и чтение снимков"
    ),
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

    if network:
        console.print("\n[bold]Доступ к данным[/bold]")
        bundle = os.environ.get("GDAL_CURL_CA_BUNDLE")
        if bundle:
            console.print(f"  [dim]сертификаты для GDAL: {bundle}[/dim]")
        with console.status("Читаю окно настоящего снимка…"):
            ok, detail = _check_raster_access()
        if ok:
            console.print(f"  [green]OK[/green] {detail}")
        else:
            console.print(f"  [red]x[/red] {detail}")
            console.print(
                "  [dim]Без этого прогон дойдёт до поиска сцен и остановится: "
                "STAC отвечает, а пиксели не читаются.[/dim]"
            )
            problems.append(f"данные: {detail}")

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
    outputs: str | None = typer.Option(None, "--outputs", help="Каталог с артефактами прогона"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
    build: bool = typer.Option(True, "--build/--no-build", help="Пересобрать сайт перед показом"),
) -> None:
    """Поднять сайт локально.

    Сайт собирается Vite из ``web-next``. Артефакты прогона копируются в
    ``web-next/public/data`` — фронтенд читает только оттуда и ничего не
    знает про пайплайн.

    Собранный ``dist`` самодостаточен: открывается и с локального сервера,
    и с флешки, и без интернета. Последнее не украшение, а требование —
    на площадке сети может не быть.
    """
    import http.server
    import shutil
    import socketserver
    import subprocess
    import threading
    import webbrowser

    from .config import REPO_ROOT

    settings = load_settings()
    source = Path(outputs) if outputs else settings.paths.resolve("outputs")
    site = REPO_ROOT / "web-next"
    data_dir = site / "public" / "data"

    if not site.exists():
        console.print(f"[red]Каталог сайта не найден: {site}[/red]")
        raise typer.Exit(code=1)

    data_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in PUBLIC_WHITELIST:
        candidate = source / name
        if candidate.exists():
            shutil.copy2(candidate, data_dir / name)
            copied += 1

    if copied:
        console.print(f"Скопировано артефактов: [green]{copied}[/green] → {data_dir}")
    else:
        console.print(
            f"[yellow]В {source} нет публикуемых артефактов.[/yellow] "
            "Сайт покажет данные прошлой сборки, если они есть."
        )

    dist = site / "dist"
    if build:
        if not (site / "node_modules").exists():
            console.print("Ставлю зависимости (первый запуск)…")
            if subprocess.run(["npm", "ci"], cwd=site, shell=True).returncode:
                console.print("[red]npm ci не отработал.[/red]")
                raise typer.Exit(code=1)
        console.print("Собираю сайт…")
        if subprocess.run(["npm", "run", "build"], cwd=site, shell=True).returncode:
            console.print("[red]Сборка не прошла.[/red]")
            raise typer.Exit(code=1)

    if not dist.exists():
        console.print(
            f"[red]Сборки нет: {dist}[/red]\n"
            "Запустите без [bold]--no-build[/bold] или соберите руками: "
            "[bold]cd web-next && npm run build[/bold]"
        )
        raise typer.Exit(code=1)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        def end_headers(self):
            # Оболочка не кешируется браузером: во время отладки это
            # экономит часы на «почему не обновилось».
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):  # тише в консоли
            pass

    url = f"http://127.0.0.1:{port}/index.html"
    console.print(f"Сайт: [bold cyan]{url}[/bold cyan]  (Ctrl+C — остановить)")
    console.print(
        "[dim]Страницы: index.html — лендинг, map.html — карта, "
        "timelapse.html — как росло, forecast.html — прогноз, "
        "citizen.html — гражданский контур[/dim]"
    )
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
PUBLIC_WHITELIST = (
    "candidates.geojson",
    "risk_public.geojson",
    "registry.geojson",
    "story.json",
    # Метрики модели — это качество прогноза, а не адреса объектов.
    # Публиковать их не только безопасно, но и нужно: без файла карта
    # честно пишет «модель не обучена», и на защите это выглядит хуже,
    # чем измеренные цифры.
    "metrics.json",
    # Маршрут объезда — ответ модели прогноза. Точной вероятности по
    # ячейкам в нём нет: её снимает scripts/make_patrol.py, потому что по
    # градиенту уверенности восстанавливается вся модель.
    "patrol.geojson",
)

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
    bbox: str | None = typer.Option(
        None, "--bbox", help="Своя область: min_lon,min_lat,max_lon,max_lat"
    ),
    name: str = typer.Option("run", "--name", help="Имя области — в него именуются плитки и модель"),
    tile_size: float = typer.Option(5000.0, "--tile-size", help="Сторона плитки, м"),
    outputs: str | None = typer.Option(None, "--outputs", "-o", help="Куда писать артефакты"),
    limit: int | None = typer.Option(None, "--limit", help="Взять только первые N плиток"),
    force: bool = typer.Option(False, "--force", help="Пересчитать даже готовые плитки"),
    skip_model: bool = typer.Option(False, "--skip-model", help="Не обучать сеть"),
    skip_signals: bool = typer.Option(False, "--skip-signals", help="Без радара и тепла"),
    skip_verify: bool = typer.Option(False, "--skip-verify", help="Без доверификации тайлами"),
    skip_risk: bool = typer.Option(False, "--skip-risk", help="Пропустить модель прогноза"),
) -> None:
    """Сквозной прогон по области: от снимков до файлов карты.

    Область идёт плитками, каждая пишет результат в ``outputs/tiles/`` и при
    повторе берётся из кеша: полный прогон занимает часы, и падение на
    двадцатой плитке не должно означать повтор первых девятнадцати.

    Порядок величин, измеренный на настоящих данных: около десяти минут на
    100 км² за восемь лет. Область из конфигурации — 4834 км², это порядка
    восьми часов. Для первого прогона берите ``--bbox`` поменьше.
    """
    from .orchestrate import run_full

    settings = load_settings(config)

    area = None
    if bbox:
        try:
            values = tuple(float(v) for v in bbox.split(","))
        except ValueError as exc:
            raise typer.BadParameter("bbox должен быть четырьмя числами через запятую") from exc
        if len(values) != 4:
            raise typer.BadParameter("bbox должен быть четырьмя числами через запятую")
        area = AOI.from_bbox(values, name=name, crs_working=settings.project.crs_working)

    pipeline_aoi = area or AOI.from_settings(settings)
    console.rule(f"[bold]Прогон {pipeline_aoi.name}")
    console.print(f"Область: {pipeline_aoi.area_km2:,.0f} км²".replace(",", " "))
    console.print(f"Период: {settings.time.start} .. {settings.time.end}")
    console.print(f"Плитка: {tile_size:,.0f} м\n".replace(",", " "))

    estimate_min = pipeline_aoi.area_km2 * 6.3 / 60
    if estimate_min > 20:
        console.print(
            f"[yellow]Ожидаемое время загрузки снимков: около {estimate_min / 60:.1f} ч.[/yellow] "
            "Прерывание безопасно — готовые плитки сохраняются."
        )

    outcome = run_full(
        settings=settings,
        aoi=area,
        outputs=outputs,
        tile_size_m=tile_size,
        limit=limit,
        force=force,
        with_model=not skip_model,
        with_signals=not skip_signals,
        with_verify=not skip_verify,
        with_risk=not skip_risk,
    )

    table = Table(title="Итог прогона", show_header=False)
    table.add_column("", style="cyan", no_wrap=True)
    table.add_column("")
    table.add_row("Кусков по плиткам", str(outcome.raw_candidates))
    table.add_row("Объектов после склейки", str(outcome.merged_candidates))
    table.add_row("Прошли контекстный отсев", str(outcome.kept_candidates))
    if outcome.labels:
        table.add_row(
            "Автоматическая разметка",
            f"+{outcome.labels.get('positive', 0)} / -{outcome.labels.get('negative', 0)}",
        )
    if outcome.signals:
        table.add_row("Признаки", outcome.signals)
    if outcome.verified:
        table.add_row("Доверификация", f"подтверждено {outcome.confirmed} из {outcome.verified}")
    console.print(table)

    if outcome.rejection:
        rejects = Table(title="Почему отсеяно", show_header=True)
        rejects.add_column("Причина", style="cyan")
        rejects.add_column("Объектов", justify="right")
        for reason, count in sorted(outcome.rejection.items(), key=lambda kv: -kv[1]):
            rejects.add_row(reason, str(count))
        console.print(rejects)

    if outcome.model_note:
        console.print(f"[yellow]Сеть не обучена:[/yellow] {outcome.model_note}")

    if not outcome.artifacts:
        console.print("[red]Артефактов не записано — показывать на карте нечего.[/red]")
        raise typer.Exit(code=1)

    console.print("\n[bold]Записано:[/bold]")
    for artifact, path in outcome.artifacts.items():
        console.print(f"  [green]{artifact}[/green]: {path}")
    console.print("\nДальше: [bold]vantage web[/bold]")


if __name__ == "__main__":  # pragma: no cover
    app()
