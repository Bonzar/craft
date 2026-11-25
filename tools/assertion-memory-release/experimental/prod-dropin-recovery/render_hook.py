"""Render a review-only candidate from operator-supplied exact installed bytes."""
import argparse
import ast
import hashlib
from pathlib import Path


def render(source, expected, replacement):
    if hashlib.sha256(source).hexdigest() != expected:
        raise ValueError("Baseline hook SHA-256 mismatch")
    original = source.decode("utf-8")
    tree = ast.parse(original)
    functions = [x for x in tree.body if isinstance(x, ast.FunctionDef)
                 and x.name == "trusted_unit_dropins"]
    if len(functions) != 1:
        raise ValueError("Unexpected drop-in function")
    f, = functions
    expected_refs = {"MCP_APPROVED_DROPIN", "MCP_DROPIN_PIN", "checked_file",
                     "UNITS", "UntrustedUnitDropIn"}
    if (f.decorator_list or [x.arg for x in f.args.args] != ["unit", "raw"]
            or not expected_refs <= {x.id for x in ast.walk(f) if isinstance(x, ast.Name)}
            or any(isinstance(x, ast.Name) and x.id in {"MCP_MONIUM_DROPIN", "MCP_MONIUM_PIN"}
                   for x in ast.walk(tree))):
        raise ValueError("Baseline shape differs from reviewed hook")
    lines = original.splitlines(keepends=True)
    candidate = ''.join(lines[:f.lineno-1]) + replacement.rstrip() + '\n' + ''.join(lines[f.end_lineno:])
    compile(candidate, "<review-only-hook>", "exec")
    return candidate.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("approved_sha256")
    parser.add_argument("output", type=Path)
    ns = parser.parse_args()
    if not 0 < ns.source.stat().st_size <= 128 * 1024:
        parser.error("Unexpected baseline hook size")
    candidate = render(ns.source.read_bytes(), ns.approved_sha256,
                       Path(__file__).with_name("transform_policy.txt").read_text())
    with ns.output.open("xb") as out:
        out.write(candidate)
    print(hashlib.sha256(candidate).hexdigest())


if __name__ == "__main__":
    main()
