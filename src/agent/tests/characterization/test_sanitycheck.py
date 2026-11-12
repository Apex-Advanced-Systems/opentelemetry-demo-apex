#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Characterization of internal/tools/sanitycheck.py as it behaves at the seed. The script
# runs its checks at import and exits with the number of failing files, so each case runs
# it as a subprocess in a throwaway working directory.

import subprocess
import sys

import pytest
from pathlib import Path

SANITYCHECK_PATH = Path(__file__).resolve().parents[4] / "internal" / "tools" / "sanitycheck.py"


def check(cwd, files):
    for name, content in files.items():
        path = cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return subprocess.run(
        [sys.executable, "-B", str(SANITYCHECK_PATH)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_clean_tree_passes_silently(tmp_path):
    done = check(tmp_path, {"a/x.py": b"x = 1\n", "README.md": b"# t\n"})
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_an_empty_file_fails(tmp_path):
    done = check(tmp_path, {"a.py": b""})
    assert done.returncode == 1
    assert "a.py [FAIL]" in done.stderr
    assert "Empty file found" in done.stderr


def test_a_missing_final_newline_fails(tmp_path):
    done = check(tmp_path, {"a.py": b"x = 1"})
    assert done.returncode == 1
    assert "Missing a blank line before EOF" in done.stderr


def test_non_ascii_fails(tmp_path):
    done = check(tmp_path, {"c.py": b"y = '\xc3\xa9'\n"})
    assert done.returncode == 1
    assert "Non-ASCII character found at Ln:1" in done.stderr


def test_the_tab_check_tests_byte_7_not_byte_9(tmp_path):
    # At the seed the "TAB" check compares each byte with 7 (BEL); a TAB (9) inside a line passes.
    done = check(tmp_path / "bel", {"a.py": b"x =\x071\n"})
    assert done.returncode == 1
    assert "TAB found at Ln:1" in done.stderr
    assert check(tmp_path / "tab", {"c.py": b"x =\t1\n"}).returncode == 0


def test_crlf_is_refused_for_python_and_required_for_cmd(tmp_path):
    done = check(tmp_path, {"a.py": b"x = 1\r\n", "b.cmd": b"echo\r\n"})
    assert done.returncode == 1
    assert "a.py [FAIL]" in done.stderr
    assert "b.cmd" not in done.stderr


def test_indentation_must_be_a_multiple_of_the_languages_width(tmp_path):
    done = check(tmp_path, {"a.py": b"if x:\n   y = 1\n"})
    assert done.returncode == 1
    assert "3 SPC found at Ln:2" in done.stderr
    assert check(tmp_path, {"a.py": b"if x:\n    y = 1\n"}).returncode == 0


def test_trailing_space_fails(tmp_path):
    done = check(tmp_path, {"a.py": b"x = 1 \n"})
    assert done.returncode == 1
    assert "Trailing space found at Ln:1" in done.stderr


def test_mixed_line_endings_fail(tmp_path):
    done = check(tmp_path, {"a.md": b"one\ntwo\r\n"})
    assert done.returncode == 1
    assert "Inconsistent line ending found at Ln:2" in done.stderr


def test_a_utf8_bom_is_only_allowed_in_slnx(tmp_path):
    bom = b"\xef\xbb\xbf"
    assert check(tmp_path, {"a.slnx": bom + b"<x/>\n"}).returncode == 0
    assert check(tmp_path, {"c.py": bom + b"x = 1\n"}).returncode == 1


def test_the_exit_code_counts_failing_files(tmp_path):
    files = {f"f{i}.py": b"x = 1" for i in range(3)}
    assert check(tmp_path, files).returncode == 3


@pytest.mark.parametrize("name", ["Dockerfile", "a/Dockerfile"])
def test_dockerfiles_must_use_lf(tmp_path, name):
    assert check(tmp_path, {name: b"FROM x\r\n"}).returncode == 1
