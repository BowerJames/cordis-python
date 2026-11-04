import asyncio
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Mapping

from cordis import Component, Context, Key
from cordis_plugins import GET_ENV

from ..contracts import INJECT, SESSION_STATE, TOOLS, WORKSPACE, StateKey, Tool, ToolSpec, Workspace
from .ignore import hidden_by_git

WORKSPACE_VARIABLE = "WORKSPACE_ROOT"

def workspace(root: Path | None = None) -> Component:
    # The directory every tool works in: the one given, else WORKSPACE_ROOT, else the current directory. The tools are
    # kept from whatever git ignores there, whether or not it is a repository, so what is kept out of version control,
    # such as a .env of keys, is never shown to a model.
    def apply(ctx: Context) -> None:
        configured = ctx.get(GET_ENV)(WORKSPACE_VARIABLE)
        chosen = (root or Path(configured or ".")).resolve()
        if not chosen.is_dir():
            raise NotADirectoryError(f"the workspace {chosen} is not a directory")
        hidden = hidden_by_git(chosen)
        if hidden(chosen):
            raise PermissionError(f"the workspace {chosen} is ignored by its repository, so its tools would see nothing")
        ctx.provide(WORKSPACE, Workspace(root=chosen, hidden=hidden))
    return Component("workspace", apply=apply, requires=(GET_ENV,), provides=(WORKSPACE,))

def _inside(workspace: Workspace, given: str) -> Path:
    # A path a tool may use: inside the workspace, and not hidden there. Hidden paths are refused before anything is
    # looked up, so a tool cannot even tell whether one exists.
    path = (workspace.root / given).resolve()
    if not path.is_relative_to(workspace.root):
        raise PermissionError(f"{given} is outside the workspace")
    if path != workspace.root and workspace.hidden(path):
        raise PermissionError(f"{given} is hidden from the tools, as the workspace ignores it")
    return path

def _files(workspace: Workspace, base: Path) -> Iterator[Path]:
    # Every file a tool may see under base, never looking inside a hidden directory, nor following a link outside.
    if base.is_file():
        yield base
        return
    for directory, directories, files in os.walk(base):
        here = Path(directory)
        directories[:] = [name for name in directories if not workspace.hidden(here / name)]
        for name in files:
            file = here / name
            if not workspace.hidden(file) and file.resolve().is_relative_to(workspace.root):
                yield file

def _shown(root: Path, path: Path) -> str:
    return str(path.relative_to(root)) or "."

type Run = Callable[[Mapping[str, Any]], Awaitable[str]]

def _tool(name: str, spec: ToolSpec, make: Callable[[Context], Run], requires: tuple[Key[Any], ...] = ()) -> Component:
    # A component offering one tool in the session it is loaded in. Offering it is its effect, so the tool is there
    # exactly while the component is; it uses what it requires when it runs.
    def apply(ctx: Context) -> None:
        tool = Tool(spec=spec, run=make(ctx))
        ctx.effect(lambda: ctx.get(TOOLS).offer(tool), f"offer {spec.name}")
    return Component(name, apply=apply, requires=(TOOLS, WORKSPACE, *requires))

# What read remembers in the session: each file it has shown, with when it was last changed.
SEEN: StateKey[dict[Path, float]] = StateKey("read.seen", dict)

def read_tool(limit: int = 400) -> Component:
    spec = ToolSpec(
        name="read",
        description=f"Read a text file, with line numbers. Shows at most {limit} lines from offset (1-based).",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "the file, relative to the workspace"},
            "offset": {"type": "integer", "description": "the first line to show", "minimum": 1},
        }, "required": ["path"]},
    )

    def make(ctx: Context) -> Run:
        async def run(arguments: Mapping[str, Any]) -> str:
            chosen = ctx.get(WORKSPACE)
            root = chosen.root
            path = _inside(chosen, str(arguments["path"]))
            if not path.is_file():
                raise FileNotFoundError(f"{_shown(root, path)} is not a file")
            seen = ctx.get(SESSION_STATE).get(SEEN)
            changed = path.stat().st_mtime
            # Kept across executions for the whole session, so a second read of an unchanged file is pointed out.
            if seen.get(path) == changed:
                ctx.get(INJECT)(f"{_shown(root, path)} was already read in this session and has not changed since.")
            seen[path] = changed
            lines = path.read_text(errors="replace").splitlines()
            start = max(int(arguments.get("offset") or 1), 1)
            shown = lines[start - 1:start - 1 + limit]
            body = "\n".join(f"{number:>6}\t{line}" for number, line in enumerate(shown, start=start))
            rest = len(lines) - (start - 1 + len(shown))
            return body + (f"\n... {rest} more lines; read again with offset {start + len(shown)}" if rest > 0 else "")
        return run

    return _tool("read", spec, make, requires=(SESSION_STATE, INJECT))

def list_dir_tool(limit: int = 500) -> Component:
    spec = ToolSpec(
        name="listDir",
        description="List a directory in the workspace; directories end with /.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "the directory, relative to the workspace (default .)"},
        }},
    )

    def make(ctx: Context) -> Run:
        async def run(arguments: Mapping[str, Any]) -> str:
            chosen = ctx.get(WORKSPACE)
            root = chosen.root
            path = _inside(chosen, str(arguments.get("path") or "."))
            if not path.is_dir():
                raise NotADirectoryError(f"{_shown(root, path)} is not a directory")
            entries = sorted(entry for entry in path.iterdir() if not chosen.hidden(entry))
            names = [entry.name + ("/" if entry.is_dir() else "") for entry in entries[:limit]]
            more = f"\n... {len(entries) - limit} more" if len(entries) > limit else ""
            return ("\n".join(names) or "(empty)") + more
        return run

    return _tool("list dir", spec, make)

def _search(workspace: Workspace, base: Path, pattern: re.Pattern[str], glob: str, limit: int) -> str:
    root = workspace.root
    found: list[str] = []
    # glob matches each file's path below base from the right, as *.py or src/*.py.
    files = sorted(file for file in _files(workspace, base) if file == base or file.relative_to(base).match(glob))
    for file in files:
        if len(found) >= limit:
            break
        try:
            text = file.read_text()
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: nothing a regex should search
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{_shown(root, file)}:{number}: {line.strip()[:200]}")
                if len(found) >= limit:
                    found.append(f"... stopped at {limit} matches")
                    break
    return "\n".join(found) or "no matches"

def grep_tool(limit: int = 100) -> Component:
    spec = ToolSpec(
        name="grep",
        description=f"Search files in the workspace for a regular expression; shows at most {limit} matching lines.",
        parameters={"type": "object", "properties": {
            "pattern": {"type": "string", "description": "a Python regular expression"},
            "path": {"type": "string", "description": "a file or directory to search, relative to the workspace (default .)"},
            "glob": {"type": "string", "description": "which files to search, such as *.py (default every file)"},
        }, "required": ["pattern"]},
    )

    def make(ctx: Context) -> Run:
        async def run(arguments: Mapping[str, Any]) -> str:
            chosen = ctx.get(WORKSPACE)
            base = _inside(chosen, str(arguments.get("path") or "."))
            pattern = re.compile(str(arguments["pattern"]))
            return await asyncio.to_thread(_search, chosen, base, pattern, str(arguments.get("glob") or "*"), limit)
        return run

    return _tool("grep", spec, make)
