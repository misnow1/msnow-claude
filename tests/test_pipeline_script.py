"""The file-length script, run as `sh scripts/check-file-length` with a stand-in scc."""

import os
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT

# The script is a plain file that Copier copies as it is. The tests run the template's file.
SCRIPT = REPO_ROOT / "template" / "scripts" / "check-file-length"
HEADER = "Language,Provider,Filename,Lines,Code,Comments,Blanks,Complexity,Bytes,ULOC"
FAKE_SCC = """#!/bin/sh
# A stand-in for scc. It prints the file that holds the output for the last argument, if any.
for last; do :; done
here=$(dirname "$0")
echo "$last" >> "$here/calls"
out="$here/out/$(basename "$last")"
if [ -f "$out" ]; then cat "$out"; fi
exit 0
"""


def row(name: str, code: int, header: str = HEADER) -> str:
    """The CSV that scc prints for a Python file, when it counts `code` code lines."""
    quoted = f'"{name}"' if "," in name else name
    return f"{header}\nPython,{quoted},{quoted},{code + 3},{code},2,1,0,100,0\n"


class Checker:
    """Runs the generated script with a stand-in scc on PATH, in a work directory."""

    def __init__(self, root: Path) -> None:
        self.work = root / "work"
        self.bin = root / "bin"
        (self.bin / "out").mkdir(parents=True)
        self.work.mkdir()
        scc = self.bin / "scc"
        scc.write_text(FAKE_SCC)
        scc.chmod(0o755)

    def scc_prints(self, name: str, output: str) -> None:
        (self.bin / "out" / Path(name).name).write_text(output)

    def scc_calls(self) -> list[str]:
        calls = self.bin / "calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def write(self, name: str, text: str) -> None:
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run(self, *files: str, limit: str = "500") -> subprocess.CompletedProcess[str]:
        for name in files:
            if not (self.work / name).exists() and name != "gone.py":
                self.write(name, "x = 1\n")
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(
            ["sh", str(SCRIPT), "--limit", limit, *files],
            cwd=self.work,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def checker(tmp_path: Path) -> Checker:
    return Checker(tmp_path)


def test_script_fails_above_the_limit_and_names_the_file(checker: Checker) -> None:
    checker.scc_prints("big.py", row("big.py", 501))

    result = checker.run("big.py")

    assert result.returncode == 1
    assert "big.py: 501 code lines, the limit is 500" in result.stdout


def test_script_passes_at_the_limit(checker: Checker) -> None:
    checker.scc_prints("edge.py", row("edge.py", 500))

    result = checker.run("edge.py")

    assert result.returncode == 0, result.stdout + result.stderr


def test_script_checks_every_file_before_it_fails(checker: Checker) -> None:
    checker.scc_prints("a.py", row("a.py", 600))
    checker.scc_prints("ok.py", row("ok.py", 5))
    checker.scc_prints("b.py", row("b.py", 700))

    result = checker.run("a.py", "ok.py", "b.py")

    assert result.returncode == 1
    assert "a.py: 600 code lines" in result.stdout
    assert "b.py: 700 code lines" in result.stdout
    assert "ok.py" not in result.stdout


def test_script_finds_the_code_column_by_its_name_from_the_end(checker: Checker) -> None:
    # A column added or moved after Code must not give a wrong number.
    wider = "Language,Provider,Filename,Lines,Code,Comments,Blanks,Complexity,Cognitive,Bytes,ULOC"
    checker.scc_prints("wide.py", wider + "\nPython,wide.py,wide.py,900,800,50,50,0,0,9,0\n")
    moved = "Language,Provider,Filename,Code,Lines,Comments,Blanks,Complexity,Bytes,ULOC"
    checker.scc_prints("moved.py", moved + "\nPython,moved.py,moved.py,40,900,50,50,0,9,0\n")

    result = checker.run("wide.py", "moved.py")

    assert result.returncode == 1
    assert "wide.py: 800 code lines" in result.stdout
    assert "moved.py" not in result.stdout


def test_script_counts_a_path_with_a_comma(checker: Checker) -> None:
    checker.scc_prints("we,ird.py", row("we,ird.py", 501))

    result = checker.run("we,ird.py")

    assert result.returncode == 1
    assert "we,ird.py: 501 code lines" in result.stdout


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("", "scc counted nothing"),
        (HEADER + "\n", "scc counted nothing"),
        (
            "Language,Provider,Filename,Lines,Bytes\nPython,x.py,x.py,1,9\n",
            "scc gave no code count",
        ),
        (row("x.py", 5).replace(",5,", ",many,", 1), "scc gave no code count"),
    ],
    ids=["no output", "header only", "no Code column", "not a number"],
)
def test_script_does_not_pass_when_scc_gives_no_usable_count(
    checker: Checker, output: str, message: str
) -> None:
    checker.scc_prints("x.py", output)

    result = checker.run("x.py")

    assert result.returncode == 1
    assert f"x.py: {message}" in result.stdout + result.stderr


@pytest.mark.parametrize("text", ["", "\n\n  \n\t\n"], ids=["zero bytes", "only whitespace"])
def test_script_passes_an_empty_file_without_asking_scc(checker: Checker, text: str) -> None:
    checker.write("__init__.py", text)

    result = checker.run("__init__.py")

    assert result.returncode == 0, result.stdout + result.stderr
    assert checker.scc_calls() == []


def test_script_skips_a_file_that_is_gone_without_a_message(checker: Checker) -> None:
    result = checker.run("gone.py")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (result.stdout, result.stderr) == ("", "")
    assert checker.scc_calls() == []


def test_script_rejects_a_path_with_a_newline(checker: Checker) -> None:
    result = checker.run("a\nb.py")

    assert result.returncode == 1
    assert "newline" in result.stdout
    assert checker.scc_calls() == []


GENERATED = {
    "go": ("a.go", "// Code generated by protoc-gen-go. DO NOT EDIT.\npackage a\n"),
    "protoc": ("a_pb2.py", "# Generated by the protocol buffer compiler.  DO NOT EDIT!\nx = 1\n"),
    "grpc": ("a_grpc.py", "# Generated by the gRPC Python plugin. DO NOT EDIT!\nx = 1\n"),
    "at-generated": ("a.rs", "// @generated\nfn f() {}\n"),
    "prost": ("b.rs", "// This file is @generated by prost-build.\nfn f() {}\n"),
    "line 10": ("late.go", "\n" * 9 + "// Code generated by x. DO NOT EDIT.\n"),
}


@pytest.mark.parametrize("kind", GENERATED)
def test_script_skips_and_says_so_for_a_generated_file(checker: Checker, kind: str) -> None:
    name, text = GENERATED[kind]
    checker.write(name, text)
    checker.scc_prints(name, row(name, 900))

    result = checker.run(name)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{name}: skipped, generated" in result.stdout
    assert checker.scc_calls() == []


@pytest.mark.parametrize(
    "text",
    [
        "# do not edit this list\nx = 1\n",
        'x = "Code generated by tool. DO NOT EDIT."\n',
        "\n" * 10 + "// Code generated by x. DO NOT EDIT.\n",
        "# TODO: remove the @generated marker\nx = 1\n",
        "# this file is not @generated\nx = 1\n",
    ],
    ids=[
        "a bare comment",
        "a string, not a comment",
        "after line 10",
        "a TODO about the marker",
        "a comment that denies the marker",
    ],
)
def test_script_still_checks_a_file_that_only_looks_generated(checker: Checker, text: str) -> None:
    checker.write("x.py", text)
    checker.scc_prints("x.py", row("x.py", 900))

    result = checker.run("x.py")

    assert result.returncode == 1
    assert "x.py: 900 code lines" in result.stdout


def test_script_prints_a_path_with_a_backslash_as_it_is(checker: Checker) -> None:
    # Some shells read a backslash escape in `echo`, and `\\c` would cut the message short.
    name = "a\\cb.py"
    checker.scc_prints(name, row(name, 600))

    result = checker.run(name)

    assert result.returncode == 1
    assert f"{name}: 600 code lines, the limit is 500" in result.stdout
