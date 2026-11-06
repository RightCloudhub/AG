"""Knowledge selection scopes real browser modules without external services."""

import shutil
import subprocess

import pytest

from agentic_graphrag.config import ROOT_DIR


def test_open_configuration_disables_required_review_and_resets_dependent_fields():
    panel = (ROOT_DIR / "web/static/js/knowledge/config_panel.js").read_text()
    assert 'value="required" :disabled="form.build_mode === \'open\'"' in panel
    assert '@change="changeMode"' in panel
    assert '@change="changeProfile"' in panel


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for JS validation")
@pytest.mark.parametrize("module", ["web_knowledge_runtime.mjs", "web_summary_runtime.mjs"])
def test_browser_knowledge_scope_and_configuration(module):
    result = subprocess.run(
        ["node", "--test", f"tests/unit/{module}"],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
