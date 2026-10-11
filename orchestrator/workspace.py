"""Git checkouts the harness owns, and the plain-file copies agents work on.

Agents never see a `.git` directory. A sandboxed agent that could write into `.git` could
plant hooks or `core.fsmonitor` settings that run on the host the next time the harness
calls git. So each agent gets an export of the tree, and the harness copies back only
regular files under the allowed paths into its own clone before committing.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from . import config, gh

if sys.version_info < (3, 13):  # older Path.rglob follows symlinked directories
    raise RuntimeError("the orchestrator requires Python 3.13+")

MAX_FILE_BYTES = 512 * 1024
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv"}
SAFE_GIT = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *SAFE_GIT, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed in {repo}: {proc.stderr.strip()[-500:]}")
    return proc.stdout


def harness_clone(team: config.Team, name: str, agent: str) -> Path:
    """A private clone for one agent role, updated with that agent's token."""
    path = config.WORK_DIR / "git" / name
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        gh.git_as(team, agent, str(path.parent), "clone", "--quiet", f"https://github.com/{team.repo}.git", name)
    gh.git_as(team, agent, str(path), "fetch", "--quiet", "--prune", "origin")
    return path


def checkout(repo: Path, branch: str, start: str) -> None:
    git(repo, "checkout", "--quiet", "-B", branch, start)
    git(repo, "reset", "--quiet", "--hard", start)
    git(repo, "clean", "-fdxq")


def remote_branch_exists(repo: Path, branch: str) -> bool:
    return bool(git(repo, "branch", "-r", "--list", f"origin/{branch}").strip())


def copy_tree_no_links(src: Path, dest: Path) -> None:
    """Copy regular files and directories only; symlinks are dropped, never followed."""
    def ignore(dirpath: str, names: list[str]) -> list[str]:
        return [n for n in names if n == ".git" or os.path.islink(os.path.join(dirpath, n))]
    shutil.copytree(src, dest, ignore=ignore, symlinks=True)


def export_tree(repo: Path, dest: Path) -> None:
    """Copy the checked-out tree (without .git or symlinks) to an agent workspace directory."""
    if dest.is_symlink():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    copy_tree_no_links(repo, dest)


def safe_write(path: Path, data: str) -> None:
    """Write a harness file into an agent-writable directory without following a planted symlink."""
    if path.is_symlink() or path.exists():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(data)


def safe_write_bytes(path: Path, data: bytes) -> None:
    if path.is_symlink() or path.exists():
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def contained(root: Path, path: Path) -> bool:
    """True if `path` is inside `root` with no symlink anywhere between them."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            return False
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


def read_agent_file(root: Path, path: Path, limit: int = MAX_FILE_BYTES) -> bytes | None:
    """Read a file an agent produced, refusing symlinks, escapes and oversized files."""
    if not contained(root, path):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None
        data = fh.read(limit + 1)
    return data if len(data) <= limit else None


def _regular_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if root.is_symlink() or not root.is_dir():
        return files
    for p in root.rglob("*"):  # Python >= 3.13 does not descend into symlinked directories
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts) or p.name == ".DS_Store":
            continue
        if p.is_symlink() or not p.is_file():
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        files[p.relative_to(root).as_posix()] = p
    return files


def import_changes(agent_tree: Path, repo: Path, allowed_paths: list[str],
                   protected: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Mirror allowed paths from the agent's copy into the harness clone.

    Paths under `protected` (e.g. the tester's acceptance tests) are never changed or deleted.
    Returns (changed paths inside the allowed area, ignored paths the agent touched outside it
    or under a protected prefix).
    """
    protected = protected or []
    if not contained(agent_tree.parent, agent_tree):
        raise RuntimeError(f"agent workspace {agent_tree} is a symlink; refusing to import")
    agent_files = _regular_files(agent_tree)
    repo_files = _regular_files(repo)
    def is_protected(rel: str) -> bool:
        return any(rel.startswith(x) for x in protected)

    outside = sorted(
        rel for rel, p in agent_files.items()
        if (not any(rel.startswith(a) for a in allowed_paths) or is_protected(rel))
        and (rel not in repo_files or repo_files[rel].read_bytes() != p.read_bytes())
    ) + sorted(rel for rel in repo_files if is_protected(rel) and rel not in agent_files)
    for prefix in allowed_paths:
        for rel, p in repo_files.items():
            if rel.startswith(prefix) and rel not in agent_files and not is_protected(rel):
                p.unlink()
        for rel, p in agent_files.items():
            if rel.startswith(prefix) and not is_protected(rel):
                data = read_agent_file(agent_tree, p)
                if data is None:
                    continue
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not contained(repo, target.parent / "x"):
                    continue  # a symlinked directory in the clone; never write through it
                if target.is_symlink():
                    target.unlink()
                safe_write_bytes(target, data)
    git(repo, "add", "-A", "--", *allowed_paths)
    changed = [l for l in git(repo, "diff", "--cached", "--name-only").splitlines() if l]
    return changed, outside
