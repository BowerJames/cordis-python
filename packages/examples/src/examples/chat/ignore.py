# What a workspace keeps from its tools: whatever git ignores there, worked out from the ignore files themselves, so no
# git process is needed and a workspace that is not a repository is treated the same way.
from pathlib import Path
from typing import Callable

from pathspec import GitIgnoreSpec

# Never shown, whatever any ignore file says: git's own directory, and directories that are large, generated, or both.
ALWAYS_HIDDEN = frozenset({".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"})

def repository_of(root: Path) -> Path | None:
    # The top of the repository root is in, if it is in one.
    return next((directory for directory in (root, *root.parents) if (directory / ".git").exists()), None)

def hidden_by_git(root: Path) -> Callable[[Path], bool]:
    # Whether a path in root is hidden, by git's rules: every .gitignore from the top of the repository down to the
    # path's own directory applies, each to the paths below it, and the nearer one wins; .git/info/exclude applies
    # below them all. Nothing inside an ignored directory can be included again, so when the repository ignores root
    # itself, hidden(root) is true and so is everything in it.
    root = root.resolve()
    top = repository_of(root) or root
    # Each ignore file's rules, kept until the file changes, so an edit to one is seen by the next tool call.
    read: dict[Path, tuple[int, GitIgnoreSpec | None]] = {}

    def rules(file: Path) -> GitIgnoreSpec | None:
        try:
            changed = file.stat().st_mtime_ns
        except OSError:
            return None
        if file not in read or read[file][0] != changed:
            read[file] = (changed, GitIgnoreSpec.from_lines(file.read_text(errors="replace").splitlines()))
        return read[file][1]

    def ignored(path: Path, is_dir: bool) -> bool:
        # The last pattern to match wins, read from the least to the most specific file.
        between = path.parent.relative_to(top).parts
        files = [(top, top / ".git" / "info" / "exclude"),
                 *((top.joinpath(*between[:depth]), top.joinpath(*between[:depth], ".gitignore"))
                   for depth in range(len(between) + 1))]
        verdict: bool | None = None
        for base, file in files:
            spec = rules(file)
            if spec is None:
                continue
            matched = spec.check_file(path.relative_to(base).as_posix() + ("/" if is_dir else "")).include
            if matched is not None:
                verdict = matched
        return verdict is True

    def hidden(path: Path) -> bool:
        parts = path.relative_to(top).parts
        # Each directory on the way down from the top first, as nothing inside a hidden one can be shown.
        for depth, name in enumerate(parts, start=1):
            current = top.joinpath(*parts[:depth])
            if name in ALWAYS_HIDDEN or ignored(current, is_dir=depth < len(parts) or current.is_dir()):
                return True
        return False

    return hidden
