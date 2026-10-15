import json

from typer.testing import CliRunner

from liveatc_digest.cli import main as cli
from liveatc_digest.http import HttpClient
from tests.conftest import FakeResponse

runner = CliRunner()
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 100


def _wire(monkeypatch, session):
    monkeypatch.setattr(
        cli.Ctx,
        "client",
        property(lambda self: HttpClient("t", session=session, sleep=lambda s: None)),
    )


def test_version():
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0 and result.stdout.strip()


def test_stations_json(home, session, search_html, monkeypatch):
    _wire(monkeypatch, session)
    session.add(
        "https://www.liveatc.net/search/?icao=kbed", FakeResponse(200, search_html.encode())
    )
    result = runner.invoke(cli.app, ["--json", "stations", "KBED"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [d["identifier"] for d in data] == ["kbed_twr", "kbed_app"]
    assert data[0]["role"] == "tower"


def test_fetch_resolves_archive_id_and_reports_missing(
    home, session, search_html, archive_html, monkeypatch
):
    _wire(monkeypatch, session)
    session.add(
        "https://www.liveatc.net/search/?icao=kbed", FakeResponse(200, search_html.encode())
    )
    session.add(
        "https://www.liveatc.net/archive.php?m=kbed_twr", FakeResponse(200, archive_html.encode())
    )
    # Use a window far in the past so it reports "expired" without a network call.
    result = runner.invoke(
        cli.app,
        ["--json", "fetch", "kbed_twr", "--from", "2020-01-01T00:00Z", "--to", "2020-01-01T01:00Z"],
    )
    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert [r["status"] for r in rows] == ["expired", "expired"]
    assert "https://www.liveatc.net/archive.php?m=kbed_twr" in session.calls


def test_config_show_and_set(home):
    result = runner.invoke(cli.app, ["config", "set", "asr.provider", "local-whisper"])
    assert result.exit_code == 0, result.output
    shown = runner.invoke(cli.app, ["--json", "config", "show"])
    assert json.loads(shown.stdout)["asr"]["provider"] == "local-whisper"
    bad = runner.invoke(cli.app, ["config", "set", "asr.provider", "nope"])
    assert bad.exit_code == 2


def test_db_info(home):
    result = runner.invoke(cli.app, ["--json", "db", "info"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["schema"] == "001_initial.sql"
