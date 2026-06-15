"""Hardening tests: error paths, edge cases, and input validation.

Covers gaps identified during production hardening:
  - Non-dict entries in secrets list
  - Directory passed as inventory path
  - Non-UTF-8 file content
  - Malformed JSON
  - Empty inventory (zero secrets)
  - mcp_server importability and missing-path tool call
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tokenrotate.core import (  # noqa: E402
    build_plan,
    load_inventory,
    summarize,
)
from tokenrotate.cli import main  # noqa: E402


class TestLoadInventoryEdgeCases(unittest.TestCase):
    """core.load_inventory must give clear errors for bad inputs."""

    def _write(self, data, *, binary=False, mode="w", encoding="utf-8"):
        suffix = ".json"
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        if binary:
            with open(path, "wb") as fh:
                fh.write(data)
        else:
            with open(path, mode, encoding=encoding) as fh:
                if isinstance(data, str):
                    fh.write(data)
                else:
                    json.dump(data, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_non_dict_entry_in_secrets_list_raises(self):
        """secrets list containing a string/null must raise ValueError with index."""
        path = self._write({"secrets": [{"name": "good"}, "not-a-dict", None]})
        with self.assertRaises(ValueError) as ctx:
            load_inventory(path)
        self.assertIn("secrets[1]", str(ctx.exception))

    def test_null_entry_in_bare_list_raises(self):
        """Bare-list form with a null entry must raise ValueError."""
        path = self._write([{"name": "ok"}, None])
        with self.assertRaises(ValueError) as ctx:
            load_inventory(path)
        self.assertIn("secrets[1]", str(ctx.exception))

    def test_malformed_json_raises(self):
        """A file with invalid JSON must raise json.JSONDecodeError."""
        import json as _json
        path = self._write("{not valid json", binary=False)
        with self.assertRaises(_json.JSONDecodeError):
            load_inventory(path)

    def test_empty_secrets_list_is_valid(self):
        """An inventory with zero secrets is valid; build_plan produces empty plan."""
        path = self._write({"secrets": []})
        inv = load_inventory(path)
        self.assertEqual(len(inv.secrets), 0)
        plan = build_plan(inv)
        self.assertEqual(len(plan.items), 0)
        s = summarize(plan)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["actionable"], 0)

    def test_file_not_found_propagates(self):
        """Missing inventory file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            load_inventory("/no/such/file/ever.json")


class TestCLIErrorPaths(unittest.TestCase):
    """CLI main() must return exit code 2 + write to stderr for all error kinds."""

    def _run(self, argv):
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with io.StringIO() as _out, redirect_stderr(stderr_buf):
            with unittest.mock.patch("sys.stdout", stdout_buf):
                code = main(argv)
        return code, stdout_buf.getvalue(), stderr_buf.getvalue()

    def setUp(self):
        import unittest.mock  # noqa: F401 — ensure available
        self._mock = unittest.mock

    def test_malformed_json_returns_2_with_stderr(self):
        """Malformed JSON file: exit 2, error message on stderr."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            fh.write("{broken json")
        self.addCleanup(os.remove, path)

        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf):
            code = main(["plan", path])
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr_buf.getvalue())

    def test_directory_as_inventory_returns_2(self):
        """Passing a directory as the inventory path: exit 2, clear error."""
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, tmpdir)

        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf):
            code = main(["plan", tmpdir])
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr_buf.getvalue())

    def test_non_utf8_file_returns_2(self):
        """Binary (non-UTF-8) file: exit 2, clear error."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"\xff\xfe" + "not utf8 content".encode("utf-16-le"))
        self.addCleanup(os.remove, path)

        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf):
            code = main(["plan", path])
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr_buf.getvalue())

    def test_non_dict_entry_returns_2(self):
        """Inventory with non-dict secret entry: exit 2, clear error."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"secrets": [{"name": "ok"}, "bad-entry"]}, fh)
        self.addCleanup(os.remove, path)

        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf):
            code = main(["check", path])
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr_buf.getvalue())


class TestMcpServerImportable(unittest.TestCase):
    """mcp_server.py must import without error (no broken top-level imports)."""

    def test_mcp_server_importable(self):
        import importlib
        # If already cached from a previous import attempt that failed, clear it.
        sys.modules.pop("tokenrotate.mcp_server", None)
        mod = importlib.import_module("tokenrotate.mcp_server")
        self.assertTrue(callable(getattr(mod, "serve", None)))

    def test_serve_returns_1_when_mcp_not_installed(self):
        """serve() returns 1 and prints a helpful message when mcp isn't installed."""
        import importlib
        import unittest.mock as mock

        sys.modules.pop("tokenrotate.mcp_server", None)
        # Simulate mcp not being installed by making the import fail.
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def fake_import(name, *args, **kwargs):
            if name == "mcp.server.fastmcp":
                raise ImportError("No module named 'mcp'")
            return real_import(name, *args, **kwargs)

        mod = importlib.import_module("tokenrotate.mcp_server")
        stderr_buf = io.StringIO()
        with mock.patch("builtins.__import__", side_effect=fake_import), \
             redirect_stderr(stderr_buf):
            result = mod.serve()
        self.assertEqual(result, 1)
        self.assertIn("mcp", stderr_buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
