"""Extract every SQL string passed to .execute() in the Python services, as JSON on stdout.

Consumed by scripts/check-sql.mjs, which prepares each statement against the migrated schema.
Resolves string constants, concatenation, f-strings and `" AND ".join(conditions)` lists
(every literal assigned or appended to the list is included, so optional filters are checked too).
"""

import ast
import json
import sys
from pathlib import Path

ROOTS = ["packages/signal_core/src", "services/worker/src", "services/api/src"]
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


class Scope:
    def __init__(self, parent: "Scope | None" = None) -> None:
        self.consts: dict[str, str] = dict(parent.consts) if parent else {}
        self.lists: dict[str, list[str]] = {}

    def resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.consts.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.resolve(node.left), self.resolve(node.right)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                inner = self.resolve(
                    value.value if isinstance(value, ast.FormattedValue) else value
                )
                if inner is None:
                    return None
                parts.append(inner)
            return "".join(parts)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
        ):
            sep = self.resolve(node.func.value)
            items = self.lists.get(node.args[0].id)
            return sep.join(items) if sep is not None and items else None
        return None

    def load(self, root: ast.AST) -> None:
        nodes = sorted(
            (n for n in ast.walk(root) if hasattr(n, "lineno")),
            key=lambda n: (n.lineno, n.col_offset),
        )
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and isinstance(node.value, ast.List):
                    self.lists[target.id] = [
                        e.value
                        for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    ]
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.lists
                and node.args
            ):
                value = self.resolve(node.args[0])
                if value is not None:
                    self.lists[node.func.value.id].append(value)
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and not isinstance(node.value, ast.List):
                    value = self.resolve(node.value)
                    if value is not None:
                        self.consts[target.id] = value


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    found: dict[tuple[str, int], dict[str, object]] = {}
    for root in ROOTS:
        for path in sorted((repo / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = path.relative_to(repo).as_posix()
            module = Scope()
            module.load(
                ast.Module(body=[n for n in tree.body if not isinstance(n, FUNCS)], type_ignores=[])
            )
            # Outer functions come first in ast.walk, so an inner function's own scope wins.
            for owner in [tree, *[n for n in ast.walk(tree) if isinstance(n, FUNCS)]]:
                scope = module
                if owner is not tree:
                    scope = Scope(module)
                    scope.load(owner)
                for node in ast.walk(owner):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                        continue
                    if node.func.attr not in ("execute", "executemany") or not node.args:
                        continue
                    found[(rel, node.lineno)] = {
                        "file": rel,
                        "line": node.lineno,
                        "sql": scope.resolve(node.args[0]),
                    }
    json.dump([found[k] for k in sorted(found)], sys.stdout)


if __name__ == "__main__":
    main()
