"""`latc` command-line interface (thin layer over :class:`liveatc_digest.engine.Engine`)."""

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
from ..engine import Engine, iso
from ..http import HttpError
from ..sources import liveatc

app = typer.Typer(
    name="latc",
    help="LiveATC digest engine: fetch, transcribe, and reconstruct ATC activity.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="View and edit settings and API keys.", no_args_is_help=True)
db_app = typer.Typer(help="Workspace database.", no_args_is_help=True)
cache_app = typer.Typer(help="Raw download cache.", no_args_is_help=True)
report_app = typer.Typer(help="Saved digests and reports.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(cache_app, name="cache")
app.add_typer(report_app, name="report")

console = Console()
err_console = Console(stderr=True)


class Ctx:
    def __init__(self, settings: config.Settings, as_json: bool) -> None:
        self.settings = settings
        self.json = as_json
        self.engine = Engine(settings, progress=self._progress)

    def _progress(self, msg: str) -> None:
        if not self.json:
            err_console.print(f"[dim]{msg}[/dim]", markup=True, highlight=False)


def _emit(ctx: Ctx, payload: Any, render) -> None:
    if ctx.json:
        console.print_json(json.dumps(payload, default=str))
    else:
        render()


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise typer.BadParameter(f"not an ISO-8601 timestamp: {value}") from exc
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


FromOpt = Annotated[str, typer.Option("--from", help="Start UTC, e.g. 2024-07-28T00:00Z")]
ToOpt = Annotated[str, typer.Option("--to", help="End UTC (exclusive)")]
IcaoOpt = Annotated[str | None, typer.Option(help="Airport ICAO (default: from the mount)")]


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


# -- stations / fetch -------------------------------------------------------------------


@app.command()
def stations(
    ctx: typer.Context, icao: Annotated[str, typer.Argument(help="Airport ICAO, e.g. KBED")]
) -> None:
    """List LiveATC feeds for an airport and remember them in the workspace database."""
    c: Ctx = ctx.obj
    found = c.engine.stations(icao)
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
        for col in ("Mount", "Role", "Title", "Status", "Frequencies"):
            table.add_column(col)
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


@app.command()
def fetch(
    ctx: typer.Context,
    station: Annotated[str, typer.Argument(help="LiveATC mount, e.g. kbed_twr")],
    start: FromOpt,
    end: ToOpt,
    icao: IcaoOpt = None,
) -> None:
    """Download the 30-minute archive blocks covering a UTC window into the cache."""
    c: Ctx = ctx.obj
    try:
        outcomes = c.engine.fetch(station, parse_utc(start), parse_utc(end), icao)
    except LookupError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    results = [o.as_dict() for o in outcomes]

    def render() -> None:
        table = Table(title=f"{station} {start} → {end}")
        for col in ("Block start (UTC)", "Status", "Detail"):
            table.add_column(col)
        for r in results:
            detail = r.get("path") or r.get("reason", "")
            if r.get("cached"):
                detail += "  (cache hit)"
            table.add_row(r["start_utc"], r["status"], detail)
        console.print(table)

    _emit(c, results, render)
    if all(r["status"] != "fetched" for r in results):
        raise typer.Exit(code=1)


# -- transcribe / transcript / digest ---------------------------------------------------


@app.command()
def transcribe(
    ctx: typer.Context,
    station: Annotated[str, typer.Argument(help="LiveATC mount, e.g. kbed_twr")],
    start: FromOpt,
    end: ToOpt,
    icao: IcaoOpt = None,
    asr: Annotated[
        str | None, typer.Option(help="openai | local-whisper (default: settings)")
    ] = None,
    model: Annotated[
        str | None, typer.Option(help="ASR model name (default: provider default)")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-transcribe blocks that already have utterances")
    ] = False,
) -> None:
    """Fetch (if needed), segment, and transcribe a window into UTC-stamped utterances."""
    c: Ctx = ctx.obj
    try:
        outcomes = c.engine.transcribe(
            station, parse_utc(start), parse_utc(end), icao, asr, model, force
        )
    except LookupError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    results = [o.as_dict() for o in outcomes]

    def render() -> None:
        table = Table(title=f"Transcribed {station} {start} → {end}")
        for col in ("Block start (UTC)", "Status", "Utterances", "Note"):
            table.add_column(col)
        for r in results:
            note = "already transcribed" if r.get("skipped") else r.get("reason", "")
            table.add_row(r["start_utc"], r["status"], str(r["utterances"]), note)
        console.print(table)

    _emit(c, results, render)
    if all(r["status"] != "fetched" for r in results):
        raise typer.Exit(code=1)


@app.command()
def transcript(
    ctx: typer.Context,
    icao: Annotated[str, typer.Argument(help="Airport ICAO")],
    start: FromOpt,
    end: ToOpt,
    feeds: Annotated[str | None, typer.Option(help="Comma-separated mounts (default: all)")] = None,
) -> None:
    """Print stored utterances for a window."""
    c: Ctx = ctx.obj
    stations_list = [s.strip() for s in feeds.split(",")] if feeds else None
    views = c.engine.utterance_views(icao, parse_utc(start), parse_utc(end), stations_list)
    payload = [
        {
            "id": v.id,
            "start_utc": iso(v.start_utc),
            "end_utc": iso(v.end_utc),
            "feed": v.feed,
            "phase": v.phase,
            "confidence": v.confidence,
            "text": v.text,
            "normalized": v.normalized_text,
            "callsigns": v.callsigns,
            "events": v.events,
        }
        for v in views
    ]

    def render() -> None:
        if not views:
            console.print("No utterances stored for that window. Run `latc transcribe` first.")
            return
        for v in views:
            conf = "" if v.confidence is None else f" ({v.confidence:.2f})"
            who = ", ".join(v.callsigns) if v.callsigns else "-"
            console.print(
                f"[u{v.id}] {v.start_utc:%H:%M:%S}Z {v.feed} {v.phase} {who}{conf}: {v.text}",
                markup=False,
                highlight=False,
            )

    _emit(c, payload, render)


@app.command()
def digest(
    ctx: typer.Context,
    icao: Annotated[str, typer.Argument(help="Airport ICAO")],
    start: FromOpt,
    end: ToOpt,
    feeds: Annotated[str | None, typer.Option(help="Comma-separated mounts (default: all)")] = None,
    llm: Annotated[
        bool, typer.Option("--llm/--no-llm", help="Add an LLM narrative (validated for grounding)")
    ] = True,
    llm_provider: Annotated[
        str | None, typer.Option(help="openai | anthropic (default: settings)")
    ] = None,
    llm_model: Annotated[str | None, typer.Option(help="LLM model (default: settings)")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Write Markdown to this file")] = None,
) -> None:
    """Build a cited digest of stored utterances and save it as a report version."""
    c: Ctx = ctx.obj
    stations_list = [s.strip() for s in feeds.split(",")] if feeds else None
    saved = c.engine.digest(
        icao, parse_utc(start), parse_utc(end), stations_list, llm, llm_provider, llm_model
    )
    if saved.utterance_count == 0:
        err_console.print("[red]No utterances in that window; run `latc transcribe` first.[/red]")
        raise typer.Exit(code=1)
    if out:
        out.write_text(saved.result.markdown, encoding="utf-8")
    removed = saved.result.validation.removed if saved.result.validation else []
    payload = {
        "reconstruction_id": saved.reconstruction_id,
        "version": saved.version,
        "utterances": saved.utterance_count,
        "llm_provider": saved.result.llm_provider,
        "llm_model": saved.result.llm_model,
        "validator_removed": removed,
        "markdown": saved.result.markdown,
    }

    def render() -> None:
        if not out:
            console.print(saved.result.markdown, markup=False, highlight=False)
        err_console.print(
            f"[dim]Saved as reconstruction {saved.reconstruction_id} v{saved.version}"
            + (f"; validator removed {len(removed)} sentence(s)" if removed else "")
            + (f"; wrote {out}" if out else "")
            + "[/dim]"
        )

    _emit(c, payload, render)


@app.command()
def reextract(
    ctx: typer.Context,
    icao: Annotated[str, typer.Argument(help="Airport ICAO")],
    start: FromOpt,
    end: ToOpt,
) -> None:
    """Re-run callsign/event extraction and phase classification over stored utterances."""
    c: Ctx = ctx.obj
    n = c.engine.reextract(icao, parse_utc(start), parse_utc(end))
    console.print(f"Re-extracted {n} utterances.")


# -- reports ----------------------------------------------------------------------------


@report_app.command("list")
def report_list(ctx: typer.Context, limit: int = 20) -> None:
    """List saved reconstructions and how many versions each has."""
    c: Ctx = ctx.obj
    rows = [dict(r) for r in c.engine.store.list_reports(limit)]

    def render() -> None:
        table = Table(title="Reports")
        for col in ("ID", "Kind", "ICAO", "Window", "Versions", "Created"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                str(r["reconstruction_id"]),
                r["kind"],
                r["icao"] or "",
                f"{r['start_utc']} → {r['end_utc']}",
                str(r["versions"] or 0),
                r["created_at"],
            )
        console.print(table)

    _emit(c, rows, render)


@report_app.command("show")
def report_show(
    ctx: typer.Context,
    reconstruction_id: int,
    version: Annotated[int | None, typer.Option(help="Version (default: latest)")] = None,
    evidence: Annotated[
        bool, typer.Option("--evidence", help="Print the evidence map instead of the body")
    ] = False,
) -> None:
    """Print a saved report version."""
    c: Ctx = ctx.obj
    row = c.engine.store.get_report(reconstruction_id, version)
    if row is None:
        err_console.print("[red]No such report.[/red]")
        raise typer.Exit(code=2)
    if evidence:
        console.print_json(row["evidence_map_json"])
    elif c.json:
        console.print_json(json.dumps({k: row[k] for k in row.keys()}))
    else:
        console.print(row["body_md"], markup=False, highlight=False)


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
    for col in ("Provider", "Env var", "Available"):
        table.add_column(col)
    for provider, env_var in config.KEY_ENV_VARS.items():
        table.add_row(provider, env_var, "yes" if config.get_api_key(provider) else "no")
    console.print(table)


# -- db / cache -------------------------------------------------------------------------


@db_app.command("init")
def db_init(ctx: typer.Context) -> None:
    """Create or migrate the workspace database."""
    c: Ctx = ctx.obj
    store = c.engine.store
    console.print(f"{store.path}  schema={store.schema_version()}")


@db_app.command("info")
def db_info(ctx: typer.Context) -> None:
    """Row counts per table."""
    c: Ctx = ctx.obj
    store = c.engine.store
    counts = store.counts()
    payload = {"path": str(store.path), "schema": store.schema_version(), "counts": counts}

    def render() -> None:
        table = Table(title=str(store.path))
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
    count, total = c.engine.cache.stats()
    payload = {"path": str(c.engine.cache.root), "objects": count, "bytes": total}
    _emit(
        c,
        payload,
        lambda: console.print(f"{c.engine.cache.root}: {count} objects, {total / 1e6:.1f} MB"),
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
    removed = c.engine.cache.prune(older_than_days * 86400)
    c.engine.store.audit("cache.prune", None, removed=removed, older_than_days=older_than_days)
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
