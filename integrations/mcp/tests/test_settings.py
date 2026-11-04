import sys

import pytest
from pydantic import ValidationError

from ag_mcp.__main__ import main, serve
from ag_mcp.settings import Settings


def test_process_configuration_uses_settings_and_masks_secret(monkeypatch):
    monkeypatch.setenv("AG_MCP_API_KEY", "sensitive-placeholder")
    monkeypatch.setenv("AG_MCP_KB_ID", "contracts")
    settings = Settings()
    assert settings.kb_id == "contracts"
    assert settings.api_key.get_secret_value() == "sensitive-placeholder"
    assert "sensitive-placeholder" not in str(settings)


@pytest.mark.parametrize(
    "url", ["file:///private", "http://user:key@localhost", "http://host?key=secret"]
)
def test_bad_process_endpoint_is_rejected(url):
    with pytest.raises(ValidationError):
        Settings(base_url=url)


def test_invalid_cli_configuration_prints_no_secret_or_rpc_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ag-mcp"])
    monkeypatch.setenv("AG_MCP_BASE_URL", "http://user:secret@localhost")
    with pytest.raises(SystemExit) as exit_info:
        main()
    output = capsys.readouterr()
    assert exit_info.value.code == 2 and output.out == ""
    assert "secret" not in output.err and "Invalid AG_MCP configuration" in output.err


async def test_serve_passes_bound_configuration_and_closes_client(monkeypatch):
    seen = []

    async def run(client, *, kb_id):
        seen.append((client, kb_id))

    monkeypatch.setattr("ag_mcp.__main__.run_stdio", run)
    await serve(Settings(kb_id="contracts"))
    assert seen[0][1] == "contracts"
    assert seen[0][0]._http.is_closed
