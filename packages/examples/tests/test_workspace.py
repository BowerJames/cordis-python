import os
from pathlib import Path

from cordis_loader import Entry
from cordis_plugins import environment, run_application
from examples.chat.ignore import hidden_by_git
from examples.chat.tools import workspace


def files(root: Path, contents: dict[str, str]) -> Path:
    # Writes each file, making the directories it is in; a name ending in / is an empty directory.
    for name, text in contents.items():
        path = root / name
        if name.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root

def shown(root: Path) -> list[str]:
    # Every path under root that is not hidden, as a tool would find them: never looking inside a hidden directory.
    hidden = hidden_by_git(root)
    found: list[str] = []
    for directory, directories, names in os.walk(root):
        here = Path(directory)
        directories[:] = sorted(name for name in directories if not hidden(here / name))
        found.extend(f"{(here / name).relative_to(root)}/" for name in directories)
        found.extend(str((here / name).relative_to(root)) for name in names if not hidden(here / name))
    return sorted(found)


def test_what_a_gitignore_names_is_hidden_and_the_rest_shown(tmp_path: Path) -> None:
    root = files(tmp_path, {".git/": "", ".gitignore": ".env\n*.log\n", ".env": "KEY=secret", "app.log": "",
                            "src/.env": "KEY=secret", "src/app.py": "", "notes.txt": ""})
    assert shown(root) == [".gitignore", "notes.txt", "src/", "src/app.py"]

def test_a_pattern_ending_in_a_slash_hides_only_directories_and_everything_in_them(tmp_path: Path) -> None:
    root = files(tmp_path, {".gitignore": "build/\n", "build/out/app.js": "", "src/build": "a file, not a directory"})
    assert shown(root) == [".gitignore", "src/", "src/build"]

def test_a_negated_pattern_shows_again_what_an_earlier_one_hid(tmp_path: Path) -> None:
    root = files(tmp_path, {".gitignore": "*.log\n!keep.log\n", "debug.log": "", "keep.log": ""})
    assert shown(root) == [".gitignore", "keep.log"]

def test_a_nearer_gitignore_applies_below_it_and_wins_over_one_further_up(tmp_path: Path) -> None:
    root = files(tmp_path, {".gitignore": "*.txt\n", "notes.txt": "",
                            "docs/.gitignore": "!readme.txt\n/draft.md\n", "docs/readme.txt": "", "docs/other.txt": "",
                            "docs/draft.md": "", "docs/deeper/draft.md": ""})
    # /draft.md is anchored to docs, so only docs/draft.md matches it.
    assert shown(root) == [".gitignore", "docs/", "docs/.gitignore", "docs/deeper/", "docs/deeper/draft.md",
                           "docs/readme.txt"]

def test_nothing_inside_an_ignored_directory_can_be_shown_again(tmp_path: Path) -> None:
    root = files(tmp_path, {".gitignore": "secret/\n!secret/ok.txt\n", "secret/ok.txt": "", "secret/key.pem": ""})
    hidden = hidden_by_git(root)
    assert hidden(root / "secret" / "ok.txt")
    assert shown(root) == [".gitignore"]

def test_a_workspace_inside_a_repository_follows_the_ignore_files_above_it(tmp_path: Path) -> None:
    files(tmp_path, {".git/info/exclude": "*.pem\n", ".gitignore": ".env\nworkspace/generated/\n",
                     "workspace/.env": "KEY=secret", "workspace/key.pem": "", "workspace/generated/out.py": "",
                     "workspace/app.py": ""})
    assert shown(tmp_path / "workspace") == ["app.py"]

def test_a_gitignore_above_a_workspace_that_is_not_in_a_repository_does_not_apply(tmp_path: Path) -> None:
    # Outside a repository, only the ignore files in the workspace itself count, as git would see no repository at all.
    files(tmp_path, {".gitignore": "*.py\n", "workspace/app.py": ""})
    assert shown(tmp_path / "workspace") == ["app.py"]

def test_a_workspace_its_repository_ignores_is_hidden_with_everything_in_it(tmp_path: Path) -> None:
    files(tmp_path, {".git/": "", ".gitignore": "workspace/\n", "workspace/app.py": ""})
    hidden = hidden_by_git(tmp_path / "workspace")
    assert hidden(tmp_path / "workspace")
    assert hidden(tmp_path / "workspace" / "app.py")

async def test_the_workspace_component_refuses_a_workspace_its_repository_ignores(tmp_path: Path) -> None:
    files(tmp_path, {".git/": "", ".gitignore": "workspace/\n", "workspace/app.py": ""})
    outcome = await run_application([Entry("env", lambda: environment({})),
                                     Entry("workspace", workspace, config={"root": tmp_path / "workspace"})], signals=False)
    [(_, error)] = outcome.failures
    assert isinstance(error, PermissionError)
    assert str(error) == f"the workspace {tmp_path / 'workspace'} is ignored by its repository, so its tools would see nothing"

def test_large_and_generated_directories_are_always_hidden(tmp_path: Path) -> None:
    root = files(tmp_path, {".git/HEAD": "", ".venv/lib/x.py": "", "node_modules/x/index.js": "",
                            "src/__pycache__/app.pyc": "", "src/app.py": ""})
    assert shown(root) == ["src/", "src/app.py"]

def test_a_change_to_a_gitignore_is_seen_the_next_time_it_is_asked(tmp_path: Path) -> None:
    root = files(tmp_path, {".gitignore": "", "notes.txt": ""})
    hidden = hidden_by_git(root)
    assert not hidden(root / "notes.txt")
    gitignore = root / ".gitignore"
    gitignore.write_text("notes.txt\n")
    # The change is told apart by its modification time, which a quick rewrite may not move on every filesystem.
    stat = gitignore.stat()
    os.utime(gitignore, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert hidden(root / "notes.txt")
