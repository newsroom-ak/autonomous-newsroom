"""Git checkouts the harness owns, and the plain-file copies agents work on.

Agents never see a `.git` directory. A sandboxed agent that could write into `.git` could
plant hooks or `core.fsmonitor` settings that run on the host the next time the harness
calls git. So each agent gets an export of the tree, and the harness copies back only
regular files under the allowed paths into its own clone before committing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config, gh

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


def export_tree(repo: Path, dest: Path) -> None:
    """Copy the checked-out tree (without .git) to an agent workspace directory."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(repo, dest, ignore=shutil.ignore_patterns(".git"), symlinks=False)


def _regular_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if not root.exists():
        return files
    for p in root.rglob("*"):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts) or p.name == ".DS_Store":
            continue
        if p.is_symlink() or not p.is_file():
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        files[p.relative_to(root).as_posix()] = p
    return files


def import_changes(agent_tree: Path, repo: Path, allowed_paths: list[str]) -> tuple[list[str], list[str]]:
    """Mirror allowed paths from the agent's copy into the harness clone.

    Returns (changed paths inside allowed area, paths the agent touched outside it, ignored).
    """
    agent_files = _regular_files(agent_tree)
    repo_files = _regular_files(repo)
    outside = sorted(
        rel for rel, p in agent_files.items()
        if not any(rel.startswith(a) for a in allowed_paths)
        and (rel not in repo_files or repo_files[rel].read_bytes() != p.read_bytes())
    )
    for prefix in allowed_paths:
        for rel, p in repo_files.items():
            if rel.startswith(prefix) and rel not in agent_files:
                p.unlink()
        for rel, p in agent_files.items():
            if rel.startswith(prefix):
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, target)
    git(repo, "add", "-A", "--", *allowed_paths)
    changed = [l for l in git(repo, "diff", "--cached", "--name-only").splitlines() if l]
    return changed, outside
