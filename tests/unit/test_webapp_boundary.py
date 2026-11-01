"""The optional Web product must not become a dependency of the GraphRAG core."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_core_has_no_webapp_imports():
    package = ROOT / "src" / "agentic_graphrag"
    for path in package.rglob("*.py"):
        if "webapp" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert "webapp" not in (node.module or ""), path
            if isinstance(node, ast.Import):
                assert all("webapp" not in alias.name for alias in node.names), path


def test_core_api_and_offline_query_do_not_load_identity_dependencies():
    code = """
import importlib.abc
import sys
class NoIdentity(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sqlalchemy', 'alembic', 'argon2', 'jwt', 'psycopg'}:
            raise ImportError('Optional Web dependency loaded: ' + fullname)
        if fullname.startswith('agentic_graphrag.webapp'):
            raise ImportError('Identity module loaded: ' + fullname)
sys.meta_path.insert(0, NoIdentity())
from agentic_graphrag.api.app import create_app
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.contracts import QueryRequest
service = QueryService.create_offline()
create_app(query_service=service)
answer = service.run_query(QueryRequest(question='Who is the CEO of Apex Holdings?'))
assert answer.answer
service.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
