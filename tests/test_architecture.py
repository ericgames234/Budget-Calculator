"""The layering conventions from DESIGN.md, checked automatically.

A rule that is only written down drifts. These tests read each module's
import statements (without running them) and fail if a module reaches
outward or takes on a job that belongs to another layer.
"""

import ast
import unittest

from . import SRC

PACKAGE = SRC / "budget_manager"

# Which package modules each module may import. Imports point inward only.
ALLOWED = {
    "models": set(),
    "validation": {"models"},
    "services": {"models", "validation"},
    "storage": {"models", "validation"},
    "cli": {"models", "validation", "services", "storage"},
}

# Standard-library modules that signal a job outside a module's responsibility.
FORBIDDEN_STDLIB = {
    "services": {"csv", "os", "pathlib", "tempfile", "sys", "argparse", "builtins"},  # no I/O
    "validation": {"csv", "os", "pathlib", "tempfile", "sys", "argparse"},  # no I/O
    "models": {"csv", "os", "pathlib", "tempfile", "sys", "argparse"},  # no I/O
    "storage": {"argparse", "sys"},  # no user interaction
    "cli": {"decimal", "csv", "os", "tempfile", "re"},  # no money maths, file handling or rules
}


def imports_of(module: str) -> tuple[set[str], set[str]]:
    """Return (package modules imported, top-level stdlib modules imported)."""
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    package, stdlib = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:  # from .x import ... / from . import x
            if node.module:
                package.add(node.module)
            else:
                package.update(alias.name for alias in node.names if alias.name in ALLOWED)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            stdlib.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            stdlib.update(alias.name.split(".")[0] for alias in node.names)
    return package, stdlib


class Layering(unittest.TestCase):
    def test_every_module_is_covered(self):
        on_disk = {p.stem for p in PACKAGE.glob("*.py") if not p.stem.startswith("_")}
        self.assertEqual(on_disk, set(ALLOWED), "add new modules to ALLOWED with their permitted imports")

    def test_dependencies_point_inward(self):
        for module, allowed in ALLOWED.items():
            with self.subTest(module=module):
                package, _ = imports_of(module)
                self.assertLessEqual(package, allowed, f"{module} imports outside its layer")

    def test_each_module_sticks_to_its_responsibility(self):
        for module, forbidden in FORBIDDEN_STDLIB.items():
            with self.subTest(module=module):
                _, stdlib = imports_of(module)
                self.assertFalse(stdlib & forbidden, f"{module} imports {stdlib & forbidden}")

    def test_pure_layers_never_print_or_prompt(self):
        for module in ("models", "validation", "services", "storage"):
            with self.subTest(module=module):
                tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
                calls = {
                    node.func.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                }
                self.assertFalse(calls & {"print", "input"}, f"{module} talks to the user")


if __name__ == "__main__":
    unittest.main()
