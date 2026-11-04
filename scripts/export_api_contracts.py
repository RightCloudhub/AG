"""Export public API/SSE schemas or verify drift and conservative compatibility."""

import argparse
import json
import subprocess

from contract_compatibility import breaking_changes

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.stream_contract import sse_schema
from agentic_graphrag.config import ROOT_DIR

CONTRACT_DIR = ROOT_DIR / "configs" / "contracts"


def documents() -> dict[str, dict]:
    return {"openapi-v1.json": create_app().openapi(), "sse-v1.schema.json": sse_schema()}


def baseline(ref: str, name: str) -> dict | None:
    relative = (CONTRACT_DIR / name).relative_to(ROOT_DIR).as_posix()
    subprocess.run(
        ["git", "rev-parse", "--verify", ref], cwd=ROOT_DIR, check=True, capture_output=True
    )
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative}"], cwd=ROOT_DIR, capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.returncode == 0 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed exports drift")
    parser.add_argument("--base-ref", help="Compare compatibility with a local Git base revision")
    args = parser.parse_args()
    errors = []
    for name, document in documents().items():
        path = CONTRACT_DIR / name
        rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != rendered:
                errors.append(f"{name}: schema drift; run scripts/export_api_contracts.py")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
        if args.base_ref and (previous := baseline(args.base_ref, name)) is not None:
            errors.extend(breaking_changes(previous, document, name))
    if errors:
        raise SystemExit("\n".join(errors))
    print("API and SSE contracts: current and compatible")


if __name__ == "__main__":
    main()
