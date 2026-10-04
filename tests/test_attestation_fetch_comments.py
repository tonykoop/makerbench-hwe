"""fetch_pr_comments must work on gh releases without ``--slurp`` (#987)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from makerbench.attestation import fetch_pr_comments, load_comments

PAGES = [
    [
        {"id": 1, "body": "first\nline two", "user": {"login": "a"}},
        {"id": 2, "body": "brace } and [bracket", "user": {"login": "b"}},
    ],
    [{"id": 3, "body": "", "user": {"login": "c"}}],
]


def _install_fake_gh(tmp_path, monkeypatch, pages=PAGES):
    """Fake gh 2.45: rejects --slurp, emits one JSON line per `.[]` element."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pages_file = tmp_path / "pages.json"
    pages_file.write_text(json.dumps(pages), encoding="utf-8")
    argv_log = tmp_path / "argv.json"
    script = bindir / "gh"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json, sys
            args = sys.argv[1:]
            with open({str(argv_log)!r}, "w", encoding="utf-8") as fh:
                json.dump(args, fh)
            if "--slurp" in args:
                sys.stderr.write("unknown flag: --slurp\\n")
                sys.exit(1)
            assert args[:2] == ["api", "repos/o/r/issues/7/comments"], args
            assert "--paginate" in args
            jq = args[args.index("--jq") + 1]
            assert jq == ".[]", jq
            with open({str(pages_file)!r}, encoding="utf-8") as fh:
                pages = json.load(fh)
            for page in pages:
                for item in page:
                    sys.stdout.write(json.dumps(item) + "\\n")
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    return argv_log


@pytest.mark.skipif(os.name == "nt", reason="fake gh uses a shebang script")
def test_fetch_pr_comments_pages_without_slurp(tmp_path, monkeypatch):
    argv_log = _install_fake_gh(tmp_path, monkeypatch)

    comments = fetch_pr_comments("o/r", 7)

    assert [c["id"] for c in comments] == [1, 2, 3]
    assert comments[0]["body"] == "first\nline two"
    assert "--slurp" not in json.loads(argv_log.read_text(encoding="utf-8"))
    assert load_comments(None, repo="o/r", pr=7) == comments


@pytest.mark.skipif(os.name == "nt", reason="fake gh uses a shebang script")
def test_fetch_pr_comments_empty(tmp_path, monkeypatch):
    _install_fake_gh(tmp_path, monkeypatch, pages=[[]])
    assert fetch_pr_comments("o/r", 7) == []


@pytest.mark.skipif(os.name == "nt", reason="fake gh uses a shebang script")
def test_fake_gh_rejects_slurp(tmp_path, monkeypatch):
    _install_fake_gh(tmp_path, monkeypatch)
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            ["gh", "api", "repos/o/r/issues/7/comments", "--paginate", "--slurp"],
            check=True,
            capture_output=True,
        )


def test_fetch_pr_comments_flattens_page_arrays(monkeypatch):
    """Pretty-printed or page-array output still flattens to one comment list."""
    stdout = json.dumps(PAGES[0], indent=2) + "\n" + json.dumps(PAGES[1][0]) + "\n"

    def fake_run(cmd, **kwargs):
        assert "--slurp" not in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert [c["id"] for c in fetch_pr_comments("o/r", 7)] == [1, 2, 3]
