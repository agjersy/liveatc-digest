"""`latc` command-line interface."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .. import __version__, config, paths
from ..cache import Cache
from ..http import HttpClient, HttpError
from ..sources import liveatc
from ..store import Store

app = typer.Typer(
    name="latc",
    help="LiveATC digest engine: fetch, transcribe, and reconstruct ATC activity.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="View and edit settings and API keys.", no_args_is_help=True)
db_app = typer.Typer(help="Workspace database.", no_args_is_help=True)
cache_app = typer.Typer(help="Raw download cache.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(cache_app, name="cache")

console = Console()
err_console = Console(stderr=True)


class Ctx:
    def __init__(self, settings: config.Settings, as_json: bool) -> None:
        self.settings = settings
        self.json = as_json
        self._store: Store | None = None
        self._cache: Cache | None = None
        self._client: HttpClient | None = None

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.settings.workspace_db)
        return self._store

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            self._cache = Cache(self.settings.cache_dir)
        return self._cache

    @property
    def client(self) -> HttpClient:
        if self._client is None:
            self._client = HttpClient(
                self.settings.liveatc.user_agent,
                timeout_s=self.settings.liveatc.request_timeout_s,
            )
        return self._client


def _emit(ctx: Ctx, payload: Any, render) -> None:
    if ctx.json:
        console.print_json(json.dumps(payload, default=str))
    else:
        render()


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise typer.BadParameter(f"not an ISO-8601 timestamp: {value}") from exc
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@app.callback()
def _root(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Settings file (default: user config dir).")
    ] = None,
) -> None:
    ctx.obj = Ctx(config.load_settings(config_path), json_output)


@app.command()
def version() -> None:
    """Print the engine version."""
    console.print(__version__)


# -- stations ---------------------------------------------------------------------------


@app.command()
def stations(
    ctx: typer.Context, icao: Annotated[str, typer.Argument(help="Airport ICAO, e.g. KBED")]
) -> None:
    """List LiveATC feeds for an airport and remember them in the workspace database."""
    c: Ctx = ctx.obj
    found = liveatc.fetch_stations(c.client, icao)
    for st in found:
        c.store.upsert_station(
            st.icao, st.identifier, st.title, st.role, st.up, [f.as_dict() for f in st.frequencies]
        )
    c.store.audit("stations.list", icao.upper(), count=len(found))

    payload = [
        {
            "icao": st.icao,
            "identifier": st.identifier,
            "title": st.title,
            "role": st.role,
            "up": st.up,
            "frequencies": [f.as_dict() for f in st.frequencies],
        }
        for st in found
    ]

    def render() -> None:
        if not found:
            console.print(f"No feeds found for {icao.upper()}.")
            return
        table = Table(title=f"LiveATC feeds for {icao.upper()}")
        table.add_column("Mount")
        table.add_column("Role")
        table.add_column("Title")
        table.add_column("Status")
        table.add_column("Frequencies")
        for st in found:
            table.add_row(
                st.identifier,
                st.role,
                st.title,
                "UP" if st.up else "DOWN",
                ", ".join(f"{f.mhz} ({f.title})" for f in st.frequencies),
            )
        console.print(table)

    _emit(c, payload, render)


# -- fetch ------------------------------------------------------------------------------


@app.command()
def fetch(
    ctx: typer.Context,
    station: Annotated[str, typer.Argument(help="LiveATC mount, e.g. kbed_twr")],
    start: Annotated[str, typer.Option("--from", help="Start UTC, e.g. 2024-07-28T00:00Z")],
    end: Annotated[str, typer.Option("--to", help="End UTC (exclusive)")],
    icao: Annotated[str | None, typer.Option(help="Airport ICAO (default: from the mount)")] = None,
) -> None:
    """Download the 30-minute archive blocks covering a UTC window into the cache."""
    c: Ctx = ctx.obj
    start_dt, end_dt = parse_utc(start), parse_utc(end)
    airport = (icao or station.split("_")[0]).upper()

    row = c.store.get_station(station)
    archive_id = row["archive_id"] if row else None
    if row is None:
        for st in liveatc.fetch_stations(c.client, airport):
            c.store.upsert_station(
                st.icao,
                st.identifier,
                st.title,
                st.role,
                st.up,
                [f.as_dict() for f in st.frequencies],
            )
        row = c.store.get_station(station)
        if row is None:
            err_console.print(f"[red]Unknown station {station!r} for {airport}.[/red]")
            raise typer.Exit(code=2)
    if not archive_id:
        archive_id = liveatc.resolve_archive_id(c.client, station)
        c.store.set_station_archive_id(station, archive_id)

    blocks = liveatc.plan_blocks(start_dt, end_dt)
    retention = c.settings.liveatc.retention_days
    results: list[dict[str, Any]] = []
    for block in blocks:
        start_iso = block.start_utc.isoformat().replace("+00:00", "Z")
        end_iso = block.end_utc.isoformat().replace("+00:00", "Z")
        try:
            fetched = liveatc.fetch_block(
                c.client, c.cache, c.store, airport, archive_id, block, retention_days=retention
            )
        except liveatc.ArchiveNotAvailable as exc:
            status = str(liveatc.block_status(block, retention_days=retention))
            if status == "available":
                status = "missing"
            c.store.upsert_audio_block(row["id"], airport, start_iso, end_iso, status)
            results.append(
                {"start_utc": start_iso, "status": status, "reason": exc.reason, "url": exc.url}
            )
            continue
        rec = c.store.find_fetch(fetched.url, liveatc.archive.PROVIDER)
        c.store.upsert_audio_block(
            row["id"], airport, start_iso, end_iso, "fetched", rec.id if rec else None
        )
        results.append(
            {
                "start_utc": start_iso,
                "status": "fetched",
                "cached": fetched.cached,
                "sha256": fetched.sha256,
                "path": str(fetched.path),
                "url": fetched.url,
            }
        )
    c.store.audit("fetch.blocks", station, start=start, end=end, count=len(results))

    def render() -> None:
        table = Table(title=f"{station} ({archive_id}) {start} → {end}")
        table.add_column("Block start (UTC)")
        table.add_column("Status")
        table.add_column("Detail")
        for r in results:
            detail = r.get("path") or r.get("reason", "")
            if r.get("cached"):
                detail += "  (cache hit)"
            table.add_row(r["start_utc"], r["status"], detail)
        console.print(table)

    _emit(c, results, render)
    if all(r["status"] != "fetched" for r in results):
        raise typer.Exit(code=1)


# -- config -----------------------------------------------------------------------------


@config_app.command("path")
def config_path() -> None:
    """Print the settings file location."""
    console.print(str(paths.config_file()))


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Print current settings as TOML."""
    c: Ctx = ctx.obj
    if c.json:
        console.print_json(c.settings.model_dump_json())
    else:
        console.print(c.settings.to_toml(), end="", markup=False, highlight=False)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. asr.provider")],
    value: Annotated[str, typer.Argument()],
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Set a settings value and save the file."""
    c: Ctx = ctx.obj
    try:
        updated = config.set_value(c.settings, key, value)
    except KeyError:
        err_console.print(f"[red]Unknown setting {key!r}.[/red]")
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    saved = config.save_settings(updated, config_path)
    console.print(f"Saved {key} to {saved}")


@config_app.command("set-key")
def config_set_key(
    provider: Annotated[
        str, typer.Argument(help="openai | anthropic | adsbexchange | flightaware")
    ],
    value: Annotated[
        str | None, typer.Option("--value", help="Key (omit to be prompted)", hide_input=True)
    ] = None,
) -> None:
    """Store an API key in the OS keychain (never in config or the database)."""
    if provider not in config.KEY_ENV_VARS:
        err_console.print(f"[red]Unknown provider {provider!r}.[/red]")
        raise typer.Exit(code=2)
    secret = value or typer.prompt(f"{provider} API key", hide_input=True)
    config.set_api_key(provider, secret)
    console.print(f"Stored {provider} key in keychain service '{config.KEYRING_SERVICE}'.")


@config_app.command("keys")
def config_keys() -> None:
    """Show which API keys are available (values are never printed)."""
    table = Table(title="API keys")
    table.add_column("Provider")
    table.add_column("Env var")
    table.add_column("Available")
    for provider, env_var in config.KEY_ENV_VARS.items():
        table.add_row(provider, env_var, "yes" if config.get_api_key(provider) else "no")
    console.print(table)


# -- db / cache -------------------------------------------------------------------------


@db_app.command("init")
def db_init(ctx: typer.Context) -> None:
    """Create or migrate the workspace database."""
    c: Ctx = ctx.obj
    store = c.store
    console.print(f"{store.path}  schema={store.schema_version()}")


@db_app.command("info")
def db_info(ctx: typer.Context) -> None:
    """Row counts per table."""
    c: Ctx = ctx.obj
    counts = c.store.counts()
    payload = {"path": str(c.store.path), "schema": c.store.schema_version(), "counts": counts}

    def render() -> None:
        table = Table(title=str(c.store.path))
        table.add_column("Table")
        table.add_column("Rows", justify="right")
        for name, n in counts.items():
            table.add_row(name, str(n))
        console.print(table)

    _emit(c, payload, render)


@cache_app.command("info")
def cache_info(ctx: typer.Context) -> None:
    """Object count and size of the raw download cache."""
    c: Ctx = ctx.obj
    count, total = c.cache.stats()
    payload = {"path": str(c.cache.root), "objects": count, "bytes": total}
    _emit(
        c, payload, lambda: console.print(f"{c.cache.root}: {count} objects, {total / 1e6:.1f} MB")
    )


@cache_app.command("prune")
def cache_prune(
    ctx: typer.Context,
    older_than_days: Annotated[
        int, typer.Option("--older-than", help="Days since last access")
    ] = 90,
) -> None:
    """Delete cached objects not accessed recently."""
    c: Ctx = ctx.obj
    removed = c.cache.prune(older_than_days * 86400)
    c.store.audit("cache.prune", None, removed=removed, older_than_days=older_than_days)
    console.print(f"Removed {removed} objects.")


def main() -> None:  # pragma: no cover
    """Entry point: run the app and turn expected failures into short messages."""
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
    except HttpError as exc:
        err_console.print(f"[red]Network error:[/red] {exc}")
        sys.exit(3)
    except liveatc.ArchiveNotAvailable as exc:
        err_console.print(f"[red]Archive not available:[/red] {exc}")
        sys.exit(3)


if __name__ == "__main__":  # pragma: no cover
    main()
