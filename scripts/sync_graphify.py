"""Refresh the working graph and publish a graph of the source already on GitHub."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile


ROOT = Path(__file__).resolve().parents[1]
GITHUB = "https://github.com/Kahan-007/Project_Mentalist_v2.git"
SOURCE_REF = "refs/heads/india-screener-full-project"
GRAPH_REF = "refs/heads/graphify-snapshot"
GRAPH_PATH = "graphify-out/graph.json"
SOURCE_MARKER = "SOURCE_COMMIT"


def git(cwd, *args):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, timeout=35,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
    ).stdout.strip()


def refresh(cwd):
    executable = shutil.which("graphify")
    local_executable = ROOT / ".local" / "graphify" / "bin" / "graphify.exe"
    if executable is None and local_executable.is_file():
        executable = str(local_executable)
    command = [executable] if executable else [sys.executable, "-m", "graphify"]
    return subprocess.run(
        [*command, "update", ".", "--force"], cwd=cwd, check=True, timeout=50,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
    )


def build_snapshot(repo, source, work):
    # Limit extraction to project source from the published commit.
    filenames = git(repo, "ls-tree", "-r", "--name-only", "-z", source, "--",
                    "india_screener", "scripts", "tests").split("\0")
    code = [name for name in filenames if Path(name).suffix in {".py", ".js"}]
    if not code:
        raise ValueError("No published application source found")
    archive = work / "source.zip"
    git(repo, "archive", "--format=zip", "-o", str(archive), source, "--", *code)
    source_dir = work / "source"
    with zipfile.ZipFile(archive) as files:
        files.extractall(source_dir)
    refresh(source_dir)
    graph = source_dir / GRAPH_PATH
    if not json.loads(graph.read_text(encoding="utf-8"))["nodes"]:
        raise ValueError("Graphify generated an empty GitHub graph")
    return graph


def publish_snapshot(state):
    refs = {
        ref: sha for sha, ref in (
            line.split() for line in git(ROOT, "ls-remote", GITHUB, SOURCE_REF, GRAPH_REF).splitlines()
        )
    }
    source = refs[SOURCE_REF]
    previous = refs.get(GRAPH_REF)
    receipt = state / "published.txt"
    if previous and receipt.is_file() and receipt.read_text(encoding="utf-8").split() == [source, previous]:
        return False

    with TemporaryDirectory(prefix="publish-", dir=state) as directory:
        work = Path(directory)
        repo = work / "snapshot"
        git(work, "init", "--quiet", str(repo))
        if previous:
            git(repo, "fetch", "--depth=1", "--no-tags", GITHUB, previous)
            if git(repo, "show", f"{previous}:{SOURCE_MARKER}") == source:
                receipt.write_text(f"{source} {previous}\n", encoding="utf-8")
                return False

        # Use the local object only when its SHA matches the source advertised by GitHub.
        source_repo = ROOT
        try:
            git(ROOT, "cat-file", "-e", f"{source}^{{commit}}")
        except subprocess.CalledProcessError:
            git(repo, "fetch", "--depth=1", "--no-tags", GITHUB, source)
            source_repo = repo
        graph = build_snapshot(source_repo, source, work)
        destination = repo / GRAPH_PATH
        destination.parent.mkdir()
        shutil.copyfile(graph, destination)
        (repo / SOURCE_MARKER).write_text(source + "\n", encoding="utf-8")
        git(repo, "add", "--", GRAPH_PATH, SOURCE_MARKER)
        tree = git(repo, "write-tree")
        parents = ["-p", previous] if previous else []
        name = git(ROOT, "config", "user.name")
        email = git(ROOT, "config", "user.email")
        commit = git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}",
                     "commit-tree", tree, *parents, "-m", f"Refresh Graphify for {source[:12]}")
        git(repo, "push", GITHUB, f"{commit}:{GRAPH_REF}")
        receipt.write_text(f"{source} {commit}\n", encoding="utf-8")
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", action="store_true", help="Read the Codex Stop event from stdin")
    parser.add_argument("--from-parent", action="store_true", help="Skip inside the checkout itself")
    parser.add_argument("--local-only", action="store_true", help="Refresh only the working graph")
    args = parser.parse_args()
    if args.hook:
        event = json.load(sys.stdin)
        if event.get("stop_hook_active"):
            return
    if args.from_parent and Path.cwd().resolve().is_relative_to(ROOT):
        return

    state = ROOT / ".local" / "graphify-sync"
    state.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(state / "lock.sqlite", timeout=30)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        refresh(ROOT)
        if args.local_only:
            print("Refreshed the local Graphify graph.")
            return
        published = publish_snapshot(state)
        status = "published" if published else "already current"
        print(f"Refreshed the local Graphify graph. GitHub snapshot {status}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        print(f"Graphify sync failed: {detail.strip()}", file=sys.stderr)
        sys.exit(1)
