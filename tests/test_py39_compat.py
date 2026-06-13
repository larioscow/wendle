"""requires-python >=3.9 conformance: a PEP 604 union (`X | Y`) inside a RUNTIME-evaluated
annotation crashes Python 3.9 at import time (`type.__or__` is 3.10+). With
`from __future__ import annotations` every annotation becomes a lazy string, so the syntax is
safe. This scans every repo module once — enforcing the whole bug class away, not just the one
file it bit (tests/test_demo_hook_frida.py shipped `Exception | None` without the import and
broke 3.9 collection of the entire suite).

Conservative by design: function-local AnnAssign annotations (never evaluated) are also
flagged — adding the future import there is free and keeps the rule simple.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("wendle", "scripts", "tests")


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )


def _union_annotation_lines(tree: ast.Module):
    """Line numbers of every `|` inside an annotation 3.9 would evaluate eagerly."""
    anns = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None and arg.annotation is not None:
                    anns.append(arg.annotation)
            if node.returns is not None:
                anns.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
    for ann in anns:
        if any(isinstance(s, ast.BinOp) and isinstance(s.op, ast.BitOr) for s in ast.walk(ann)):
            yield ann.lineno


def test_no_runtime_pep604_unions_without_future_import():
    offenders = []
    for scope in SCOPES:
        for path in sorted((ROOT / scope).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            if _has_future_annotations(tree):
                continue
            offenders += [f"{path.relative_to(ROOT)}:{n}" for n in _union_annotation_lines(tree)]
    assert not offenders, (
        "PEP 604 unions evaluated at runtime break Python 3.9 (requires-python >=3.9); add "
        "`from __future__ import annotations` to: " + ", ".join(offenders)
    )
