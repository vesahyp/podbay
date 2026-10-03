"""Past (no-longer-running) Claude Code sessions, so they can be resumed
with `claude --resume <session-id>` after a reboot.

Transcripts live under ~/.claude/projects/<slug>/<session-id>.jsonl, some
tens of MB. Never json.load()s a whole file: candidate files are sorted by
mtime first, only the `limit` newest are read at all, and each of those is
read via bounded head/tail slices (mirroring sources.py's _tail_lines).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .sources import PROJECTS_DIR, TAIL_BYTES, _assistant_entries, _tail_lines, _user_entry_text

HEAD_BYTES = 64 * 1024
TITLE_MAX_LEN = 80
CHUNK_BYTES = 256 * 1024
SNIPPET_WIDTH = 70
RG_TIMEOUT = 2.0
FALLBACK_FILE_LIMIT = 100


@dataclass
class PastSession:
    session_id: str
    cwd: str
    project_dir: str  # the project slug directory's basename
    ended_at: datetime  # transcript mtime, local tz (matches sources.py)
    title: str
    size_bytes: int


@dataclass
class SessionMatch:
    session: PastSession
    snippet: str  # ~70-char excerpt, single line, whitespace collapsed
    where: str  # "title" (title or cwd matched) or "conversation"


def _head_lines(path: Path, head_bytes: int) -> list[str]:
    """Mirror of sources._tail_lines, but from the start of the file:
    drops a trailing partial line when the read stopped short of EOF."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            data = fh.read(head_bytes)
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if size > head_bytes:
        lines = lines[:-1]
    return lines


def _trim_title(text: str, max_len: int = TITLE_MAX_LEN) -> str:
    line = " ".join(text.split())
    if len(line) > max_len:
        line = line[: max_len - 1].rstrip() + "…"
    return line


# A session whose transcript starts with a slash command (/exit, /model, ...)
# would otherwise be titled with the raw command markup.
_COMMAND_SPANS = re.compile(
    r"<(command-name|command-message|command-args|local-command-stdout)>.*?</\1>",
    re.DOTALL,
)


def _clean_prompt(text: str) -> str:
    return _COMMAND_SPANS.sub("", text).strip()


def _decode_slug(slug: str) -> str:
    """Lossy inverse of sources._slug_for_cwd -- last resort only, since a
    cwd containing a literal '-' can't be told apart from the separator."""
    return slug.replace("-", "/")


def _session_info(path: Path) -> tuple[str | None, str | None] | None:
    """(cwd, title_source) from the transcript's head (cwd, first user
    prompt) and tail (cwd fallback, newest away_summary). None means no
    usable content was found at all (an empty file) -- caller skips it.
    A malformed line is simply skipped, not treated as "no content"."""
    saw_line = False
    cwd: str | None = None
    first_prompt: str | None = None

    for raw in _head_lines(path, HEAD_BYTES):
        line = raw.strip()
        if not line:
            continue
        saw_line = True
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cwd is None and record.get("cwd"):
            cwd = record["cwd"]
        if (
            first_prompt is None
            and record.get("type") == "user"
            and not record.get("isMeta")
            and not record.get("isSidechain")
        ):
            text = _clean_prompt(_user_entry_text((record.get("message") or {}).get("content")))
            if text:
                first_prompt = text

    summary: str | None = None
    for raw in _tail_lines(path, TAIL_BYTES):
        line = raw.strip()
        if not line:
            continue
        saw_line = True
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cwd is None and record.get("cwd"):
            cwd = record["cwd"]
        if record.get("type") == "system" and record.get("subtype") == "away_summary" and record.get("content"):
            summary = record["content"]  # keeps overwriting -- tail order, so the last one wins

    if not saw_line:
        return None
    return cwd, (summary or first_prompt)


def _build_session(path: Path, mtime: float) -> PastSession | None:
    """Shared by list_past_sessions and search_sessions, so a session's
    title/cwd/ended_at are always derived the same way regardless of which
    path found it."""
    info = _session_info(path)
    if info is None:
        return None
    cwd, title_source = info
    if not title_source:
        return None  # nothing was ever said in it (a /exit or /model session): not worth resuming
    if cwd is None:
        cwd = _decode_slug(path.parent.name)
    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = 0
    return PastSession(
        session_id=path.stem,
        cwd=cwd,
        project_dir=path.parent.name,
        ended_at=datetime.fromtimestamp(mtime),
        title=_trim_title(title_source),
        size_bytes=size_bytes,
    )


def list_past_sessions(
    projects_dir: Path = PROJECTS_DIR,
    exclude_ids: set[str] | None = None,
    limit: int = 100,
    since_days: int | None = 30,
) -> list[PastSession]:
    """Newest first. `exclude_ids` are the live sessions (already shown
    elsewhere); anything older than `since_days` is dropped before the file
    read, not after, so a huge old transcript never costs a read."""
    exclude_ids = exclude_ids or set()
    if not projects_dir.is_dir():
        return []

    candidates: list[tuple[float, Path]] = []
    for path in projects_dir.glob("*/*.jsonl"):
        if path.stem in exclude_ids:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, path))

    candidates.sort(key=lambda item: item[0], reverse=True)

    if since_days is not None:
        cutoff = time.time() - since_days * 86400
        candidates = [c for c in candidates if c[0] >= cutoff]

    candidates = candidates[:limit]

    sessions: list[PastSession] = []
    for mtime, path in candidates:
        session = _build_session(path, mtime)
        if session is not None:
            sessions.append(session)
    return sessions


def _is_session_transcript(path: Path, projects_dir: Path) -> bool:
    """<slug>/<session-id>.jsonl only -- never a nested subagents/ file."""
    try:
        rel = path.relative_to(projects_dir)
    except ValueError:
        return False
    return len(rel.parts) == 2 and path.suffix == ".jsonl"


def _iter_lines_chunked(path: Path, chunk_bytes: int = CHUNK_BYTES):
    """Stream a file's lines without loading it whole, so a caller can bail
    out (stop iterating) after the first hit without reading the rest of a
    huge transcript."""
    try:
        with path.open("rb") as fh:
            buf = b""
            while True:
                chunk = fh.read(chunk_bytes)
                if not chunk:
                    break
                buf += chunk
                lines = buf.split(b"\n")
                buf = lines.pop()
                for raw in lines:
                    yield raw.decode("utf-8", errors="replace")
            if buf:
                yield buf.decode("utf-8", errors="replace")
    except OSError:
        return


def _record_text(record: dict) -> str:
    """Human-readable text out of a transcript record, reusing the same
    extraction sources.py uses for the transcript pane and titles."""
    rtype = record.get("type")
    content = (record.get("message") or {}).get("content")
    if rtype == "user":
        return _user_entry_text(content)
    if rtype == "assistant":
        entries = _assistant_entries(content, None)
        return "\n".join(e["text"] for e in entries if e["role"] == "assistant")
    return ""


def _snippet_from_text(text: str, query: str, width: int = SNIPPET_WIDTH) -> str:
    """A single-line excerpt centred on the match, whitespace collapsed."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    idx = collapsed.lower().find(query.lower())
    if idx == -1:
        return _trim_title(collapsed, width)
    end = min(len(collapsed), max(idx - width // 2, 0) + width)
    start = max(0, end - width)
    snippet = collapsed[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(collapsed):
        snippet = snippet + "…"
    return snippet


def _find_snippet(path: Path, query: str) -> str | None:
    """First matching record's text, or (if that record can't be parsed
    into human-readable text) the cleaned raw line itself. None means the
    file was unreadable or nothing in it actually matched."""
    query_lower = query.lower()
    for raw in _iter_lines_chunked(path):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("isSidechain"):
            continue
        record_text = _record_text(record)
        if record_text and query_lower in record_text.lower():
            return _snippet_from_text(record_text, query)
        if query_lower in line.lower():
            return _snippet_from_text(line, query)
    return None


def _rg_matching_files(query: str, projects_dir: Path, timeout: float = RG_TIMEOUT) -> list[Path] | None:
    """None means rg is missing, failed, or timed out -- caller falls back
    to the bounded pure-Python scan."""
    try:
        proc = subprocess.run(
            [
                "rg", "--fixed-strings", "--ignore-case", "--files-with-matches", "--null",
                "--glob", "!**/subagents/**", "--", query, str(projects_dir),
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode not in (0, 1):  # 1 == ran fine, no matches
        return None
    return [Path(p) for p in proc.stdout.split("\0") if p]


def _fallback_matching_files(
    query_lower: str, projects_dir: Path, limit_files: int = FALLBACK_FILE_LIMIT
) -> list[Path]:
    """Pure-Python stand-in for rg: newest `limit_files` transcripts only,
    each read in bounded chunks and abandoned at the first hit."""
    candidates: list[tuple[float, Path]] = []
    for path in projects_dir.glob("*/*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, path))
    candidates.sort(key=lambda item: item[0], reverse=True)

    matches: list[Path] = []
    for _, path in candidates[:limit_files]:
        for raw in _iter_lines_chunked(path):
            if query_lower in raw.lower():
                matches.append(path)
                break
    return matches


def search_sessions(
    query: str,
    projects_dir: Path = PROJECTS_DIR,
    limit: int = 40,
    exclude_ids: set[str] | None = None,
    include_ids: set[str] | None = None,
) -> list[SessionMatch]:
    """Case-insensitive, fixed-string search over every session transcript's
    full text (title, cwd and conversation), newest first within each group
    and a title/cwd hit always ranked above a conversation-only one. Never
    raises: a missing rg, a killed subprocess or a vanished/unreadable file
    just means fewer results, not an error.

    `include_ids`, when given, restricts results to that id set (used by the
    main table to ask "which of my open sessions mention X" without
    scanning the corpus any differently); `exclude_ids` wins when both are
    given."""
    query = query.strip()
    if not query or not projects_dir.is_dir():
        return []
    exclude_ids = exclude_ids or set()
    query_lower = query.lower()

    paths = _rg_matching_files(query, projects_dir)
    if paths is None:
        paths = _fallback_matching_files(query_lower, projects_dir)

    matches: list[SessionMatch] = []
    seen_ids: set[str] = set()
    for path in paths:
        if not _is_session_transcript(path, projects_dir):
            continue
        session_id = path.stem
        if session_id in exclude_ids or session_id in seen_ids:
            continue
        if include_ids is not None and session_id not in include_ids:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        session = _build_session(path, mtime)
        if session is None:
            continue
        seen_ids.add(session_id)

        if query_lower in session.title.lower() or query_lower in session.cwd.lower():
            matches.append(SessionMatch(session=session, snippet=session.title, where="title"))
            continue
        snippet = _find_snippet(path, query)
        if snippet is None:
            continue  # rg matched something outside anything we can turn into text
        matches.append(SessionMatch(session=session, snippet=snippet, where="conversation"))

    matches.sort(key=lambda m: (0 if m.where == "title" else 1, -m.session.ended_at.timestamp()))
    return matches[:limit]
