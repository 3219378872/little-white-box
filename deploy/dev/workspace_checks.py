#!/usr/bin/env python3
"""Read-only knowledge and generated-contract checks for the root workspace."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Mapping, Sequence


ROOT_REPOSITORY = "little-white-box"
BACKEND_REPOSITORY = "little-white-box-content-community"
FRONTEND_REPOSITORY = "little-white-box-front"
FORMAL_ID_RE = re.compile(r"(?:INT|SPEC|DES|IMP|EVD)-[A-Za-z0-9][A-Za-z0-9._-]*\Z")
BACKEND_REQUIREMENT_ID_PATTERN = r"[A-Z][A-Z0-9]*-(?:A[0-9]{2}|[0-9]{3}(?:-[0-9]{2})?)"
FRONTEND_REQUIREMENT_ID_PATTERN = r"(?:FX|FQ)-[0-9]{3}"
REQUIREMENT_ID_RE = re.compile(BACKEND_REQUIREMENT_ID_PATTERN + r"\Z")
REQUIREMENT_ID_RES = {
    BACKEND_REPOSITORY: REQUIREMENT_ID_RE,
    FRONTEND_REPOSITORY: re.compile(FRONTEND_REQUIREMENT_ID_PATTERN + r"\Z"),
}
TARGET_ID_PATTERN = (
    r"(?:"
    + FORMAL_ID_RE.pattern.removesuffix(r"\Z")
    + r"|"
    + BACKEND_REQUIREMENT_ID_PATTERN
    + r")"
)
EXTERNAL_REF_RE = re.compile(
    r"(?P<repository>little-white-box(?:-content-community|-front)?)"
    r"@(?P<sha>[0-9a-f]{40}):(?P<target_id>" + TARGET_ID_PATTERN + r")\Z"
)
REQUIREMENT_BULLET_RES = {
    repository: re.compile(
        r"^ {0,3}[-*][ \t]+`(?P<requirement>"
        + requirement_id.pattern.removesuffix(r"\Z")
        + r")`[：:][ \t]*\S"
    )
    for repository, requirement_id in REQUIREMENT_ID_RES.items()
}
TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
REQUIREMENT_TABLE_HEADERS = {"id", "requirement", "条款"}
GENERATED_SDK_FILES = (
    PurePosixPath("api/gateway.dart"),
    PurePosixPath("data/gateway.dart"),
)
IGNORED_KNOWLEDGE_DIRECTORIES = {"archive", "proposals", "templates"}
EVIDENCE_SCOPES = {
    "static",
    "unit",
    "integration",
    "e2e",
    "browser",
    "device",
    "synthetic",
    "human-review",
    "live-provider",
    "production",
}
ROOT_EVIDENCE_PATH = PurePosixPath("deploy/dev/e2e/evidence")
LEGACY_CHILD_EVIDENCE_PATH = PurePosixPath("implementation/evidence")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
FENCE_CONTENT_RE = re.compile(r"(`{3,}|~{3,})(.*)$")
LIST_ITEM_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<marker>[-+*]|[0-9]{1,9}[.)])"
    r"(?:(?P<spacing>[ \t]+)(?P<content>.*))?$"
)
ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
SETEXT_HEADING_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


class CheckError(RuntimeError):
    """A user-actionable workspace validation failure."""


@dataclass(frozen=True)
class Repository:
    name: str
    path: Path
    root_tree_path: str | None
    knowledge_path: PurePosixPath


@dataclass(frozen=True)
class ExternalReference:
    repository: str
    sha: str
    target_id: str


@dataclass(frozen=True)
class WorkspaceState:
    repositories: Mapping[str, Repository]
    revisions: Mapping[str, str]


@dataclass(frozen=True)
class ListContainer:
    marker_indent: int
    content_indent: int


@dataclass(frozen=True)
class FenceCandidate:
    marker: str
    remainder: str
    container: ListContainer | None


def _format_command(command: Sequence[str]) -> str:
    return " ".join(command)


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=text,
            check=False,
        )
    except FileNotFoundError as error:
        raise CheckError(f"missing command: {command[0]}") from error
    except OSError as error:
        raise CheckError(f"could not execute {command[0]}: {error}") from error
    if check and result.returncode != 0:
        stdout = (
            result.stdout.strip()
            if text
            else result.stdout.decode(errors="replace").strip()
        )
        stderr = (
            result.stderr.strip()
            if text
            else result.stderr.decode(errors="replace").strip()
        )
        details = "\n".join(part for part in (stdout, stderr) if part)
        suffix = f"\n{details}" if details else ""
        raise CheckError(
            f"command failed ({result.returncode}): {_format_command(command)}{suffix}"
        )
    return result


def _git_output(repository: Path, *arguments: str) -> str:
    return _run(["git", *arguments], cwd=repository).stdout.strip()


def _git_status(repository: Path) -> bytes:
    return _run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repository,
        text=False,
    ).stdout


def _emit_process_output(result: subprocess.CompletedProcess) -> None:
    if result.stdout:
        sys.stdout.write(result.stdout)
        if not result.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if result.stderr:
        sys.stderr.write(result.stderr)
        if not result.stderr.endswith("\n"):
            sys.stderr.write("\n")


def run_read_only_command(
    repository: Path,
    command: Sequence[str],
    *,
    label: str,
    env: Mapping[str, str],
) -> None:
    """Run a declared read-only child gate and verify its Git state is unchanged."""
    before = _git_status(repository)
    if before:
        raise CheckError(
            f"{label} requires a clean checked-out repository before it can verify "
            f"read-only execution: {repository}"
        )
    before_head = _git_output(repository, "rev-parse", "HEAD")
    print(f"== {label} ==")
    result: subprocess.CompletedProcess | None = None
    command_error: CheckError | None = None
    try:
        result = _run(command, cwd=repository, env=env, check=False)
    except CheckError as error:
        command_error = error
    finally:
        after = _git_status(repository)
        after_head = _git_output(repository, "rev-parse", "HEAD")
    if before != after or before_head != after_head:
        raise CheckError(
            f"{label} modified the checked-out repository; refusing to continue: "
            f"{repository}"
        )
    if command_error is not None:
        raise command_error
    assert result is not None
    _emit_process_output(result)
    if result.returncode != 0:
        raise CheckError(
            f"{label} failed ({result.returncode}): {_format_command(command)}"
        )


def _frontmatter_lines(text: str, *, source: str) -> list[str] | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            frontmatter = lines[1:index]
            _validate_frontmatter_syntax(frontmatter, source=source)
            return frontmatter
    raise CheckError(
        f"{source}: front matter starts with '---' but has no closing delimiter"
    )


def _validate_frontmatter_syntax(lines: Sequence[str], *, source: str) -> None:
    list_key: str | None = None
    seen_keys: set[str] = set()
    for index, line in enumerate(lines, start=2):
        if not line.strip():
            continue
        field = re.fullmatch(
            r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*)[ \t]*:"
            r"(?:[ \t]+(?P<value>.*))?",
            line,
        )
        if field is not None:
            key = field.group("key")
            if key in seen_keys:
                raise CheckError(f"{source}:{index}: duplicate front-matter key {key}")
            seen_keys.add(key)
            value = field.group("value")
            list_key = key if value is None or not value.strip() else None
            continue
        item = re.fullmatch(r"[ \t]+-(?:[ \t]+.*)?", line)
        if item is not None and list_key is not None:
            continue
        raise CheckError(
            f"{source}:{index}: invalid controlled YAML front-matter syntax"
        )


def _unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_document_id(text: str, *, source: str) -> str | None:
    return parse_frontmatter_scalar(text, "id", source=source)


def parse_frontmatter_scalar(text: str, key: str, *, source: str) -> str | None:
    lines = _frontmatter_lines(text, source=source)
    if lines is None:
        return None
    values: list[str] = []
    for line in lines:
        match = re.fullmatch(rf"{re.escape(key)}\s*:\s*(.*?)\s*", line)
        if match:
            values.append(_unquote_scalar(match.group(1)))
    if len(values) > 1:
        raise CheckError(f"{source}: duplicate front-matter {key} fields")
    return values[0] if values else None


def parse_frontmatter_list(
    text: str,
    key: str,
    *,
    source: str,
    allow_inline_empty: bool = False,
) -> list[str] | None:
    lines = _frontmatter_lines(text, source=source)
    if lines is None:
        return None
    field_indexes = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(rf"{re.escape(key)}\s*:.*", line)
    ]
    if not field_indexes:
        return None
    if len(field_indexes) > 1:
        raise CheckError(f"{source}: duplicate {key} fields")

    index = field_indexes[0]
    header = lines[index]
    inline = re.fullmatch(rf"{re.escape(key)}\s*:\s*(.*?)\s*", header)
    assert inline is not None
    inline_value = inline.group(1)
    if inline_value:
        if allow_inline_empty and inline_value == "[]":
            return []
        raise CheckError(f"{source}: {key} must be a YAML block list")

    values: list[str] = []
    for line_number in range(index + 1, len(lines)):
        line = lines[line_number]
        if not line.strip():
            continue
        if not line[:1].isspace():
            break
        item = re.fullmatch(r"\s+-\s+(.+?)\s*", line)
        if item is None:
            raise CheckError(f"{source}:{line_number + 2}: invalid {key} list item")
        values.append(_unquote_scalar(item.group(1)))
    return values


def parse_external_references(text: str, *, source: str) -> list[ExternalReference]:
    raw_values = parse_frontmatter_list(text, "external_upstream", source=source)
    if raw_values is None:
        return []
    if not raw_values:
        raise CheckError(
            f"{source}: external_upstream must contain at least one reference"
        )
    duplicates = sorted(
        value for value, count in Counter(raw_values).items() if count > 1
    )
    if duplicates:
        raise CheckError(
            f"{source}: external_upstream contains duplicate reference {duplicates[0]!r}"
        )

    references: list[ExternalReference] = []
    for value in raw_values:
        match = EXTERNAL_REF_RE.fullmatch(value)
        if match is None:
            raise CheckError(
                f"{source}: invalid external_upstream reference {value!r}; "
                "expected repo@<40 lowercase hex sha>:<formal or requirement ID>"
            )
        reference = ExternalReference(**match.groupdict())
        if not FORMAL_ID_RE.fullmatch(reference.target_id):
            requirement_id = REQUIREMENT_ID_RES.get(reference.repository)
            if requirement_id is None or not requirement_id.fullmatch(
                reference.target_id
            ):
                raise CheckError(
                    f"{source}: invalid requirement target for "
                    f"{reference.repository}: {reference.target_id}"
                )
        references.append(reference)
    return references


def _indent_width(value: str) -> int:
    width = 0
    for character in value:
        if character == "\t":
            width += 4 - (width % 4)
        else:
            width += 1
    return width


def _dedent_columns(line: str, width: int) -> str | None:
    if width == 0:
        return line
    cursor = 0
    indentation = 0
    while cursor < len(line) and line[cursor] in {" ", "\t"}:
        if line[cursor] == "\t":
            indentation += 4 - (indentation % 4)
        else:
            indentation += 1
        cursor += 1
        if indentation >= width:
            return line[cursor:]
    return None


def _list_containers(line: str) -> tuple[list[ListContainer], str] | None:
    offset = 0
    containers: list[ListContainer] = []
    while True:
        item = LIST_ITEM_RE.fullmatch(line[offset:])
        if item is None:
            break
        marker_start = offset + item.start("marker")
        marker_indent = _indent_width(line[:marker_start])
        content = item.group("content")
        if content is None:
            content_start = len(line)
            content_indent = marker_indent + len(item.group("marker")) + 1
        else:
            content_start = offset + item.start("content")
            content_indent = _indent_width(line[:content_start])
        containers.append(
            ListContainer(
                marker_indent=marker_indent,
                content_indent=content_indent,
            )
        )
        offset = content_start
        if content is None:
            break
    if not containers:
        return None
    return containers, line[offset:]


def _list_container(line: str) -> tuple[ListContainer, str] | None:
    result = _list_containers(line)
    if result is None:
        return None
    containers, content = result
    return containers[-1], content


def _updated_list_context(
    containers: Sequence[ListContainer], line: str
) -> list[ListContainer]:
    list_items = _list_containers(line)
    if list_items is not None:
        line_containers, _ = list_items
        first_marker_indent = line_containers[0].marker_indent
        ancestors = [
            container
            for container in containers
            if container.content_indent <= first_marker_indent
        ]
        return [*ancestors, *line_containers]
    if not line.strip():
        return list(containers)
    content = line.lstrip(" \t")
    indentation = _indent_width(line[: len(line) - len(content)])
    return [
        container for container in containers if container.content_indent <= indentation
    ]


def _fence_candidate(
    line: str, continuation_container: ListContainer | None = None
) -> FenceCandidate | None:
    list_content = _list_container(line)
    if list_content is not None:
        container, content = list_content
        opening = FENCE_CONTENT_RE.fullmatch(content)
    elif continuation_container is not None:
        container = continuation_container
        content = _dedent_columns(line, container.content_indent)
        opening = FENCE_OPEN_RE.fullmatch(content) if content is not None else None
    else:
        container = None
        opening = FENCE_OPEN_RE.fullmatch(line)
    if opening is None:
        return None
    marker, remainder = opening.groups()
    return FenceCandidate(marker, remainder, container)


def _fence_opening(
    line: str, continuation_container: ListContainer | None = None
) -> FenceCandidate | None:
    candidate = _fence_candidate(line, continuation_container)
    if candidate is None:
        return None
    if candidate.marker[0] == "`" and "`" in candidate.remainder:
        return None
    return candidate


def _fence_closes(line: str, fence: FenceCandidate) -> bool:
    candidate = line.lstrip(" \t")
    indentation = _indent_width(line[: len(line) - len(candidate)])
    if fence.container is None:
        if indentation > 3:
            return False
    elif not (
        fence.container.content_indent
        <= indentation
        <= fence.container.content_indent + 3
    ):
        return False
    return (
        re.fullmatch(
            rf"{re.escape(fence.marker[0])}{{{len(fence.marker)},}}[ \t]*",
            candidate,
        )
        is not None
    )


def _fence_container_ended(line: str, fence: FenceCandidate) -> bool:
    if fence.container is None or not line.strip():
        return False
    content = line.lstrip(" \t")
    indentation = _indent_width(line[: len(line) - len(content)])
    return indentation < fence.container.content_indent


def _inline_block_boundary(line: str, opener_container: ListContainer | None) -> bool:
    if not line.strip():
        return True

    structural_line = line
    if opener_container is not None:
        structural_line = _dedent_columns(line, opener_container.content_indent)
        if structural_line is None:
            return True
    if (
        ATX_HEADING_RE.match(structural_line) is not None
        or SETEXT_HEADING_RE.fullmatch(structural_line) is not None
        or _fence_opening(structural_line) is not None
    ):
        return True

    candidate = _list_container(line)
    if candidate is not None:
        return True
    return False


def _inline_code_span_end(
    lines: Sequence[str],
    start_line: int,
    start_column: int,
    continuation_container: ListContainer | None,
) -> tuple[int, int] | None:
    opening_line = lines[start_line]
    fence_candidate = _fence_candidate(opening_line, continuation_container)
    same_line_only = (
        fence_candidate is not None
        and fence_candidate.marker[0] == "`"
        and "`" in fence_candidate.remainder
    )
    list_item = _list_container(opening_line)
    opener_container = list_item[0] if list_item is not None else continuation_container
    marker_end = start_column
    while marker_end < len(opening_line) and opening_line[marker_end] == "`":
        marker_end += 1
    marker_length = marker_end - start_column

    for line_index in range(start_line, len(lines)):
        if same_line_only and line_index > start_line:
            return None
        line = lines[line_index]
        if line_index > start_line and _inline_block_boundary(line, opener_container):
            return None
        candidate = marker_end if line_index == start_line else 0
        while candidate < len(line):
            candidate = line.find("`", candidate)
            if candidate < 0:
                break
            candidate_end = candidate
            while candidate_end < len(line) and line[candidate_end] == "`":
                candidate_end += 1
            if candidate_end - candidate == marker_length:
                return line_index, candidate_end
            candidate = candidate_end
    return None


def _masked_code_span(length: int) -> str:
    """Mask code without promoting its trailing text to line-leading Markdown."""
    return "x" * length


def visible_markdown(text: str) -> str:
    """Drop comments and fenced examples before extracting formal declarations."""
    visible: list[str] = []
    lines = text.splitlines()
    fence: FenceCandidate | None = None
    in_comment = False
    code_span_end: tuple[int, int] | None = None
    list_context: list[ListContainer] = []
    for line_index, line in enumerate(lines):
        if fence is not None:
            if _fence_closes(line, fence):
                fence = None
                continue
            if not _fence_container_ended(line, fence):
                continue
            fence = None

        if code_span_end is None and not in_comment:
            list_context = _updated_list_context(list_context, line)
        continuation_container = list_context[-1] if list_context else None

        visible_parts: list[str] = []
        cursor = 0
        if code_span_end is not None:
            end_line, end_column = code_span_end
            if line_index < end_line:
                visible.append(_masked_code_span(len(line)))
                continue
            visible_parts.append(_masked_code_span(end_column))
            cursor = end_column
            code_span_end = None
        elif not in_comment:
            opening = _fence_opening(line, continuation_container)
            if opening is not None:
                fence = opening
                continue

        while cursor < len(line):
            if in_comment:
                end = line.find("-->", cursor)
                if end < 0:
                    cursor = len(line)
                    break
                in_comment = False
                cursor = end + 3
                continue
            comment_start = line.find("<!--", cursor)
            code_start = line.find("`", cursor)
            if code_start >= 0 and (comment_start < 0 or code_start < comment_start):
                code_end = _inline_code_span_end(
                    lines,
                    line_index,
                    code_start,
                    continuation_container,
                )
                if code_end is None:
                    marker_end = code_start + 1
                    while marker_end < len(line) and line[marker_end] == "`":
                        marker_end += 1
                    visible_parts.append(line[cursor:marker_end])
                    cursor = marker_end
                    continue
                end_line, end_column = code_end
                if end_line == line_index:
                    visible_parts.append(line[cursor:end_column])
                    cursor = end_column
                    continue
                visible_parts.append(line[cursor:code_start])
                visible_parts.append(_masked_code_span(len(line) - code_start))
                code_span_end = code_end
                cursor = len(line)
                continue
            if comment_start < 0:
                visible_parts.append(line[cursor:])
                break
            visible_parts.append(line[cursor:comment_start])
            in_comment = True
            cursor = comment_start + 4
        visible.append("".join(visible_parts))
    return "\n".join(visible)


def _markdown_body(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            return "\n".join(lines[index + 1 :])
    return text


def _table_cells(line: str) -> list[str] | None:
    leading_spaces = len(line) - len(line.lstrip(" "))
    if leading_spaces > 3 or line[leading_spaces:].startswith("\t"):
        return None
    value = line.strip()
    cells: list[str] = []
    current: list[str] = []
    separators = 0
    for character in value:
        if character == "|":
            backslashes = 0
            for previous in reversed(current):
                if previous != "\\":
                    break
                backslashes += 1
            if backslashes % 2 == 0:
                cells.append("".join(current).strip())
                current = []
                separators += 1
                continue
        current.append(character)
    if separators == 0:
        return None
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def _optional_code_value(value: str) -> str | None:
    candidate = value.strip()
    starts = candidate.startswith("`")
    ends = candidate.endswith("`")
    if starts != ends:
        return None
    if starts:
        candidate = candidate[1:-1].strip()
    return candidate


def requirement_definitions(text: str, *, repository: str) -> list[str]:
    requirement_id = REQUIREMENT_ID_RES.get(repository)
    bullet_pattern = REQUIREMENT_BULLET_RES.get(repository)
    if requirement_id is None or bullet_pattern is None:
        raise ValueError(f"unsupported requirement repository: {repository}")
    lines = visible_markdown(_markdown_body(text)).splitlines()
    definitions: list[str] = []
    index = 0
    while index < len(lines):
        bullet = bullet_pattern.match(lines[index])
        if bullet is not None:
            definitions.append(bullet.group("requirement"))

        header = _table_cells(lines[index])
        separator = _table_cells(lines[index + 1]) if index + 1 < len(lines) else None
        normalized_header = _optional_code_value(header[0]) if header else None
        if (
            header
            and len(header) >= 2
            and all(cell.strip() for cell in header)
            and separator
            and normalized_header is not None
            and normalized_header.casefold() in REQUIREMENT_TABLE_HEADERS
            and len(separator) == len(header)
            and all(TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in separator)
        ):
            index += 2
            while index < len(lines):
                row = _table_cells(lines[index])
                if row is None or len(row) != len(header):
                    break
                requirement = _optional_code_value(row[0])
                if (
                    requirement is not None
                    and requirement_id.fullmatch(requirement)
                    and any(cell.strip() for cell in row[1:])
                ):
                    definitions.append(requirement)
                index += 1
            continue
        index += 1
    return definitions


def _is_formal_document(path: PurePosixPath, document_id: str, repository: str) -> bool:
    if not FORMAL_ID_RE.fullmatch(document_id):
        return False
    if repository == ROOT_REPOSITORY:
        return (
            document_id.startswith("EVD-")
            and path.parent == ROOT_EVIDENCE_PATH
            and path.name == f"{document_id}.md"
        )
    if not path.is_relative_to(PurePosixPath("docs/knowledge")):
        return False
    relative = path.relative_to(PurePosixPath("docs/knowledge"))
    if any(part in IGNORED_KNOWLEDGE_DIRECTORIES for part in relative.parts):
        return False
    prefix = document_id.split("-", 1)[0]
    expected_directory = {
        "INT": "intent",
        "SPEC": "spec",
        "DES": "design",
        "IMP": "implementation",
        "EVD": "evidence",
    }[prefix]
    return (
        relative.parent == PurePosixPath(expected_directory)
        and relative.name == f"{document_id}.md"
    )


class WorkspaceChecker:
    def __init__(
        self,
        root: Path,
        *,
        backend: Path | None = None,
        frontend: Path | None = None,
        environment: Mapping[str, str] | None = None,
        temporary_parent: Path | None = None,
    ) -> None:
        root = root.resolve()
        self.root = root
        self.environment = dict(os.environ if environment is None else environment)
        self.temporary_parent = temporary_parent
        self.repositories = {
            ROOT_REPOSITORY: Repository(
                ROOT_REPOSITORY,
                root,
                None,
                PurePosixPath("deploy/dev/e2e/evidence"),
            ),
            BACKEND_REPOSITORY: Repository(
                BACKEND_REPOSITORY,
                (backend or root / BACKEND_REPOSITORY).resolve(),
                BACKEND_REPOSITORY,
                PurePosixPath("docs/knowledge"),
            ),
            FRONTEND_REPOSITORY: Repository(
                FRONTEND_REPOSITORY,
                (frontend or root / FRONTEND_REPOSITORY).resolve(),
                FRONTEND_REPOSITORY,
                PurePosixPath("docs/knowledge"),
            ),
        }
        self._target_id_cache: dict[tuple[str, str], dict[str, list[str]]] = {}

    def _require_repository(self, repository: Repository) -> str:
        if not repository.path.is_dir() or not (repository.path / ".git").exists():
            raise CheckError(
                f"submodule checkout is missing or uninitialized: {repository.path}; "
                "run git submodule update --init --recursive"
            )
        top_level = Path(
            _git_output(repository.path, "rev-parse", "--show-toplevel")
        ).resolve()
        if top_level != repository.path:
            raise CheckError(
                f"expected a Git checkout at {repository.path}, found {top_level}"
            )
        return _git_output(repository.path, "rev-parse", "--verify", "HEAD^{commit}")

    def inspect_workspace(self) -> WorkspaceState:
        if not (self.root / ".git").exists():
            raise CheckError(f"root is not a Git checkout: {self.root}")
        root_top = Path(
            _git_output(self.root, "rev-parse", "--show-toplevel")
        ).resolve()
        if root_top != self.root:
            raise CheckError(f"expected workspace root {self.root}, found {root_top}")
        root_revision = _git_output(self.root, "rev-parse", "--verify", "HEAD^{commit}")
        revisions: dict[str, str] = {ROOT_REPOSITORY: root_revision}

        for name in (BACKEND_REPOSITORY, FRONTEND_REPOSITORY):
            repository = self.repositories[name]
            tree_entry = _git_output(
                self.root,
                "ls-tree",
                "HEAD",
                "--",
                repository.root_tree_path or "",
            )
            match = re.fullmatch(
                rf"160000 commit ([0-9a-f]{{40}})\t{re.escape(repository.root_tree_path or '')}",
                tree_entry,
            )
            if match is None:
                raise CheckError(
                    f"root HEAD does not contain the expected 160000 gitlink: "
                    f"{repository.root_tree_path}"
                )
            gitlink_revision = match.group(1)
            checkout_revision = self._require_repository(repository)
            if checkout_revision != gitlink_revision:
                raise CheckError(
                    f"submodule HEAD mismatch for {repository.root_tree_path}: "
                    f"root pins {gitlink_revision}, checkout is {checkout_revision}"
                )
            revisions[name] = gitlink_revision
        return WorkspaceState(self.repositories, revisions)

    def _gitlink_at_revision(self, revision: str, tree_path: str) -> str:
        tree_entry = _git_output(self.root, "ls-tree", revision, "--", tree_path)
        match = re.fullmatch(
            rf"160000 commit ([0-9a-f]{{40}})\t{re.escape(tree_path)}",
            tree_entry,
        )
        if match is None:
            raise CheckError(
                f"root revision {revision} has no 160000 gitlink for {tree_path}"
            )
        return match.group(1)

    def _working_documents(self, repository: Repository) -> Iterable[tuple[str, str]]:
        base = repository.path / repository.knowledge_path
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(repository.path)
            if repository.name != ROOT_REPOSITORY and any(
                part in IGNORED_KNOWLEDGE_DIRECTORIES for part in relative.parts
            ):
                continue
            knowledge_relative = path.relative_to(base)
            if repository.name != ROOT_REPOSITORY and knowledge_relative.is_relative_to(
                LEGACY_CHILD_EVIDENCE_PATH
            ):
                continue
            try:
                yield relative.as_posix(), path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise CheckError(f"knowledge document is not UTF-8: {path}") from error

    def _validate_root_evidence_location(self) -> None:
        result = _run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "*.md",
            ],
            cwd=self.root,
            text=False,
        )
        paths = [
            os.fsdecode(raw_path) for raw_path in result.stdout.split(b"\0") if raw_path
        ]
        evidence_root = ROOT_EVIDENCE_PATH
        for raw_path in paths:
            path = PurePosixPath(raw_path)
            if path.is_relative_to(evidence_root):
                continue
            document = self.root / path
            if not document.is_file():
                continue
            text = document.read_text(encoding="utf-8")
            document_id = parse_document_id(text, source=raw_path)
            references = parse_external_references(text, source=raw_path)
            if references or (document_id and document_id.startswith("EVD-")):
                raise CheckError(
                    f"root integration evidence must live under {evidence_root}: {raw_path}"
                )

    @staticmethod
    def _required_scalar(text: str, key: str, *, source: str) -> str:
        value = parse_frontmatter_scalar(text, key, source=source)
        if not value:
            raise CheckError(f"{source}: missing required front-matter field {key}")
        return value

    @staticmethod
    def _required_sha(text: str, key: str, *, source: str) -> str:
        value = WorkspaceChecker._required_scalar(text, key, source=source)
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise CheckError(f"{source}: {key} must be a 40-character lowercase SHA")
        return value

    def _validate_root_evidence_schema(self, state: WorkspaceState) -> int:
        root = state.repositories[ROOT_REPOSITORY]
        evidence_root = ROOT_EVIDENCE_PATH
        evidence_count = 0
        for raw_path, text in self._working_documents(root):
            path = PurePosixPath(raw_path)
            if path == evidence_root / "README.md":
                continue
            source = f"{ROOT_REPOSITORY}:{raw_path}"
            if path.parent != evidence_root:
                raise CheckError(
                    f"{source}: integration evidence files must be direct children of "
                    f"{evidence_root}"
                )
            document_id = self._required_scalar(text, "id", source=source)
            if not document_id.startswith("EVD-") or not FORMAL_ID_RE.fullmatch(
                document_id
            ):
                raise CheckError(f"{source}: id must be a formal EVD-* identifier")
            if path.name != f"{document_id}.md":
                raise CheckError(f"{source}: filename must match id ({document_id}.md)")

            status = self._required_scalar(text, "status", source=source)
            if status not in {"active", "superseded"}:
                raise CheckError(
                    f"{source}: status must be active or superseded, got {status!r}"
                )
            result = self._required_scalar(text, "result", source=source)
            if result not in {"passed", "partial", "failed", "blocked"}:
                raise CheckError(
                    f"{source}: result must be passed, partial, failed, or blocked; "
                    f"got {result!r}"
                )
            updated_at = self._required_scalar(text, "updated_at", source=source)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated_at):
                raise CheckError(f"{source}: updated_at must be a valid YYYY-MM-DD")
            try:
                date.fromisoformat(updated_at)
            except ValueError as error:
                raise CheckError(
                    f"{source}: updated_at must be a valid YYYY-MM-DD"
                ) from error

            observed_commit = self._required_sha(text, "observed_commit", source=source)
            backend_commit = self._required_sha(text, "backend_commit", source=source)
            frontend_commit = self._required_sha(text, "frontend_commit", source=source)
            self._commit_exists_and_is_reachable(
                root, observed_commit, state.revisions[ROOT_REPOSITORY]
            )
            observed_backend = self._gitlink_at_revision(
                observed_commit, BACKEND_REPOSITORY
            )
            observed_frontend = self._gitlink_at_revision(
                observed_commit, FRONTEND_REPOSITORY
            )
            if (
                backend_commit != observed_backend
                or frontend_commit != observed_frontend
            ):
                raise CheckError(
                    f"{source}: backend_commit/frontend_commit must match the gitlinks "
                    f"recorded by observed_commit {observed_commit}"
                )
            if status == "active" and (
                backend_commit != state.revisions[BACKEND_REPOSITORY]
                or frontend_commit != state.revisions[FRONTEND_REPOSITORY]
            ):
                raise CheckError(
                    f"{source}: active evidence must match the current root HEAD gitlinks; "
                    "mark stale evidence superseded and add current evidence"
                )
            if status == "active":
                changed = self._root_changes_outside_evidence(observed_commit)
                if changed:
                    raise CheckError(
                        f"{source}: active evidence is stale because root assets changed "
                        f"after observed_commit: {changed[0]}"
                    )
                dirty = self._root_dirty_paths_outside_evidence()
                if dirty:
                    raise CheckError(
                        f"{source}: active evidence cannot be validated with root "
                        f"changes outside {evidence_root}: {dirty[0]}"
                    )

            commands = parse_frontmatter_list(text, "commands", source=source)
            if not commands or any(not command.strip() for command in commands):
                raise CheckError(
                    f"{source}: commands must be a non-empty YAML block list of "
                    "non-blank commands"
                )
            self._reject_duplicate_items(commands, "commands", source=source)
            covers = parse_frontmatter_list(text, "covers", source=source)
            if not covers:
                raise CheckError(
                    f"{source}: covers must be a non-empty YAML block list"
                )
            self._reject_duplicate_items(covers, "covers", source=source)
            invalid_covers = [
                requirement
                for requirement in covers
                if not REQUIREMENT_ID_RE.fullmatch(requirement)
            ]
            if invalid_covers:
                raise CheckError(
                    f"{source}: invalid requirement in covers: {invalid_covers[0]}"
                )
            scopes = parse_frontmatter_list(text, "scope", source=source)
            if not scopes:
                raise CheckError(f"{source}: scope must be a non-empty YAML block list")
            self._reject_duplicate_items(scopes, "scope", source=source)
            invalid_scopes = sorted(set(scopes) - EVIDENCE_SCOPES)
            if invalid_scopes:
                raise CheckError(
                    f"{source}: invalid evidence scope: {', '.join(invalid_scopes)}"
                )
            references = parse_external_references(text, source=source)
            referenced_requirements = {
                reference.target_id
                for reference in references
                if REQUIREMENT_ID_RE.fullmatch(reference.target_id)
            }
            dangling_covers = sorted(set(covers) - referenced_requirements)
            if dangling_covers:
                raise CheckError(
                    f"{source}: every covers requirement must be an exact "
                    "external_upstream target; missing " + ", ".join(dangling_covers)
                )
            referenced_repositories = {reference.repository for reference in references}
            required_repositories = {BACKEND_REPOSITORY, FRONTEND_REPOSITORY}
            if not required_repositories.issubset(referenced_repositories):
                raise CheckError(
                    f"{source}: external_upstream must reference both child repositories"
                )

            artifacts = parse_frontmatter_list(
                text,
                "artifacts",
                source=source,
                allow_inline_empty=True,
            )
            if artifacts is not None:
                if any(not artifact.strip() for artifact in artifacts):
                    raise CheckError(
                        f"{source}: artifacts must not contain blank items"
                    )
                self._reject_duplicate_items(artifacts, "artifacts", source=source)
            if status == "active" and result == "passed" and artifacts:
                if all(
                    artifact == "/tmp" or artifact.startswith("/tmp/")
                    for artifact in artifacts
                ):
                    raise CheckError(
                        f"{source}: active passed evidence cannot use only /tmp artifacts"
                    )
            evidence_count += 1
        return evidence_count

    @staticmethod
    def _reject_duplicate_items(
        values: Sequence[str], key: str, *, source: str
    ) -> None:
        duplicates = sorted(
            value for value, count in Counter(values).items() if count > 1
        )
        if duplicates:
            raise CheckError(
                f"{source}: {key} contains duplicate item {duplicates[0]!r}"
            )

    @staticmethod
    def _outside_root_evidence(paths: Iterable[str]) -> list[str]:
        return [
            raw_path
            for raw_path in paths
            if not PurePosixPath(raw_path).is_relative_to(ROOT_EVIDENCE_PATH)
        ]

    def _root_changes_outside_evidence(self, observed_commit: str) -> list[str]:
        result = _run(
            [
                "git",
                "log",
                "--format=",
                "--name-only",
                "-z",
                f"{observed_commit}..HEAD",
                "--",
            ],
            cwd=self.root,
            text=False,
        )
        paths = [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
        return self._outside_root_evidence(paths)

    def _root_dirty_paths_outside_evidence(self) -> list[str]:
        paths: list[str] = []
        for arguments in (
            ("diff", "--name-only", "-z", "--"),
            ("diff", "--cached", "--name-only", "-z", "--"),
            ("ls-files", "--others", "--exclude-standard", "-z", "--"),
        ):
            result = _run(["git", *arguments], cwd=self.root, text=False)
            paths.extend(
                os.fsdecode(path) for path in result.stdout.split(b"\0") if path
            )
        return self._outside_root_evidence(paths)

    def _commit_exists_and_is_reachable(
        self, repository: Repository, sha: str, pinned_revision: str
    ) -> None:
        exists = _run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=repository.path,
            check=False,
        )
        if exists.returncode != 0:
            raise CheckError(
                f"external_upstream commit is unavailable in {repository.name}: {sha}"
            )
        reachable = _run(
            ["git", "merge-base", "--is-ancestor", sha, pinned_revision],
            cwd=repository.path,
            check=False,
        )
        if reachable.returncode == 1:
            raise CheckError(
                f"external_upstream commit {repository.name}@{sha} is not reachable "
                f"from pinned revision {pinned_revision}"
            )
        if reachable.returncode != 0:
            raise CheckError(
                f"could not compare revisions in {repository.name}: {sha} and {pinned_revision}"
            )

    def _target_ids_at_revision(
        self, repository: Repository, revision: str
    ) -> dict[str, list[str]]:
        cache_key = (repository.name, revision)
        cached = self._target_id_cache.get(cache_key)
        if cached is not None:
            return cached
        tree = _run(
            [
                "git",
                "ls-tree",
                "-r",
                "--name-only",
                "-z",
                revision,
                "--",
                repository.knowledge_path.as_posix(),
            ],
            cwd=repository.path,
            text=False,
        )
        paths = [
            os.fsdecode(raw_path) for raw_path in tree.stdout.split(b"\0") if raw_path
        ]
        target_ids: dict[str, list[str]] = {}
        for raw_path in paths:
            path = PurePosixPath(raw_path)
            if path.suffix != ".md":
                continue
            blob = _run(
                ["git", "show", f"{revision}:{raw_path}"],
                cwd=repository.path,
            ).stdout
            document_id = parse_document_id(
                blob, source=f"{repository.name}@{revision}:{raw_path}"
            )
            if document_id and _is_formal_document(path, document_id, repository.name):
                target_ids.setdefault(document_id, []).append(raw_path)
                if (
                    document_id.startswith("SPEC-")
                    and parse_frontmatter_scalar(
                        blob,
                        "status",
                        source=f"{repository.name}@{revision}:{raw_path}",
                    )
                    == "approved"
                ):
                    for requirement in requirement_definitions(
                        blob, repository=repository.name
                    ):
                        target_ids.setdefault(requirement, []).append(raw_path)
        self._target_id_cache[cache_key] = target_ids
        return target_ids

    def _reference_limits(
        self,
        source_repository: Repository,
        path: str,
        text: str,
        state: WorkspaceState,
    ) -> Mapping[str, str]:
        if (
            source_repository.name != ROOT_REPOSITORY
            or PurePosixPath(path) == ROOT_EVIDENCE_PATH / "README.md"
        ):
            return state.revisions
        source = f"{source_repository.name}:{path}"
        return {
            ROOT_REPOSITORY: self._required_sha(text, "observed_commit", source=source),
            BACKEND_REPOSITORY: self._required_sha(
                text, "backend_commit", source=source
            ),
            FRONTEND_REPOSITORY: self._required_sha(
                text, "frontend_commit", source=source
            ),
        }

    def validate_external_upstream(self, state: WorkspaceState) -> int:
        self._validate_root_evidence_location()
        self._validate_root_evidence_schema(state)
        count = 0
        for source_repository in state.repositories.values():
            for path, text in self._working_documents(source_repository):
                source = f"{source_repository.name}:{path}"
                reference_limits = self._reference_limits(
                    source_repository, path, text, state
                )
                for reference in parse_external_references(text, source=source):
                    target = state.repositories[reference.repository]
                    pinned_revision = reference_limits[reference.repository]
                    self._commit_exists_and_is_reachable(
                        target, reference.sha, pinned_revision
                    )
                    matches = self._target_ids_at_revision(target, reference.sha).get(
                        reference.target_id, []
                    )
                    if len(matches) != 1:
                        qualifier = "no" if not matches else str(len(matches))
                        raise CheckError(
                            f"{source}: external_upstream target must resolve to exactly one "
                            "formal document or approved-SPEC requirement; "
                            f"found {qualifier} matches for {reference.repository}@"
                            f"{reference.sha}:{reference.target_id}"
                        )
                    count += 1
        return count

    def _child_environment(self) -> dict[str, str]:
        environment = dict(self.environment)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return environment

    def knowledge_check(self) -> None:
        state = self.inspect_workspace()
        reference_count = self.validate_external_upstream(state)
        environment = self._child_environment()
        run_read_only_command(
            self.repositories[BACKEND_REPOSITORY].path,
            ["make", "engineering-lint"],
            label="backend knowledge gate",
            env=environment,
        )
        run_read_only_command(
            self.repositories[FRONTEND_REPOSITORY].path,
            ["make", "knowledge-check"],
            label="frontend knowledge gate",
            env=environment,
        )
        print(
            "knowledge-check passed: gitlinks aligned; child gates passed; "
            f"{reference_count} external_upstream reference(s) validated"
        )

    def _python_supports_grpc_tools(self, executable: Path) -> bool:
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return False
        result = _run(
            [str(executable), "-c", "import grpc_tools.protoc"],
            cwd=self.root,
            check=False,
        )
        return result.returncode == 0

    def _generation_python(self) -> Path:
        explicit_python = self.environment.get("BACKEND_GENERATE_PYTHON", "").strip()
        explicit_bin = self.environment.get("GENERATE_PYTHON_BIN_DIR", "").strip()
        if explicit_python:
            candidate = Path(explicit_python).expanduser()
            if not candidate.is_absolute():
                candidate = self.root / candidate
            candidate = candidate.absolute()
            if self._python_supports_grpc_tools(candidate):
                return candidate
            raise CheckError(
                "BACKEND_GENERATE_PYTHON is not an executable Python with "
                f"grpc_tools.protoc: {candidate}"
            )
        if explicit_bin:
            candidate = Path(explicit_bin).expanduser()
            if not candidate.is_absolute():
                candidate = self.root / candidate
            candidate = (candidate / "python3").absolute()
            if self._python_supports_grpc_tools(candidate):
                return candidate
            raise CheckError(
                "GENERATE_PYTHON_BIN_DIR does not contain an executable python3 "
                f"with grpc_tools.protoc: {candidate}"
            )

        candidates: list[Path] = []
        path_python = shutil.which(
            "python3", path=self.environment.get("PATH", os.defpath)
        )
        if path_python:
            candidates.append(Path(path_python).absolute())
        backend = self.repositories[BACKEND_REPOSITORY].path
        candidates.extend(
            [
                backend / ".venv" / "bin" / "python3",
                backend / "venv" / "bin" / "python3",
                Path("/tmp/xbh-assistant-generate-venv/bin/python3"),
            ]
        )
        checked: set[Path] = set()
        for candidate in candidates:
            candidate = candidate.absolute()
            if candidate in checked:
                continue
            checked.add(candidate)
            if self._python_supports_grpc_tools(candidate):
                return candidate
        raise CheckError(
            "backend generation prerequisite missing: grpc_tools.protoc; set "
            "BACKEND_GENERATE_PYTHON to a suitable Python executable, prepend its "
            "bin directory to PATH, or set GENERATE_PYTHON_BIN_DIR. Install the "
            "declared dependency with: python3 -m pip install --requirement "
            f"{BACKEND_REPOSITORY}/scripts/requirements-generate.txt"
        )

    def check_backend_generation(self, pinned_revision: str) -> None:
        backend = self.repositories[BACKEND_REPOSITORY]
        generation_python = self._generation_python()
        with tempfile.TemporaryDirectory(
            prefix="xbh-backend-generate-",
            dir=self.temporary_parent,
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            clone = temporary_root / "backend"
            _run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--no-checkout",
                    "--no-hardlinks",
                    str(backend.path),
                    str(clone),
                ],
                cwd=temporary_root,
            )
            _run(
                ["git", "checkout", "--quiet", "--detach", pinned_revision],
                cwd=clone,
            )
            shim = temporary_root / "python-bin"
            shim.mkdir(mode=0o700)
            python_wrapper = shim / "python3"
            python_wrapper.write_text(
                "#!/bin/sh\nexec " + shlex.quote(str(generation_python)) + ' "$@"\n',
                encoding="utf-8",
            )
            python_wrapper.chmod(0o700)
            environment = self._child_environment()
            environment["PATH"] = os.pathsep.join(
                (str(shim), environment.get("PATH", os.defpath))
            )
            print("== backend generated-code drift ==")
            result = _run(
                ["make", "generate"],
                cwd=clone,
                env=environment,
                check=False,
            )
            _emit_process_output(result)
            if result.returncode != 0:
                raise CheckError(
                    f"backend generation failed ({result.returncode}) in temporary clone"
                )
            drift = _git_status(clone)
            if drift:
                rendered = (
                    drift.replace(b"\x00", b"\n").decode(errors="replace").strip()
                )
                raise CheckError(
                    "backend .api/.proto generated-code drift detected after make generate:\n"
                    + rendered
                )

    def check_frontend_sdk(self, pinned_backend_revision: str) -> None:
        frontend = self.repositories[FRONTEND_REPOSITORY]
        backend = self.repositories[BACKEND_REPOSITORY]
        backend_api_relative = PurePosixPath("app/gateway/gateway.api")
        backend_api_blob = _run(
            ["git", "show", f"{pinned_backend_revision}:{backend_api_relative}"],
            cwd=backend.path,
            text=False,
        )
        with tempfile.TemporaryDirectory(
            prefix="xbh-backend-api-", dir=self.temporary_parent
        ) as temporary_directory:
            backend_api = Path(temporary_directory) / "gateway.api"
            backend_api.write_bytes(backend_api_blob.stdout)
            environment = self._child_environment()
            environment["BACKEND_API"] = str(backend_api)
            run_read_only_command(
                frontend.path,
                ["make", "sdk-check", f"BACKEND_API={backend_api}"],
                label="frontend generated SDK drift",
                env=environment,
            )

        differences: list[str] = []
        for relative in GENERATED_SDK_FILES:
            vendor = frontend.path / "vendor" / "sdk_source" / relative
            application = frontend.path / "lib" / "sdk" / relative
            if not vendor.is_file():
                differences.append(f"missing {vendor.relative_to(frontend.path)}")
            if not application.is_file():
                differences.append(f"missing {application.relative_to(frontend.path)}")
            if vendor.is_file() and application.is_file():
                if vendor.read_bytes() != application.read_bytes():
                    differences.append(
                        f"{vendor.relative_to(frontend.path)} != "
                        f"{application.relative_to(frontend.path)}"
                    )
        if differences:
            raise CheckError(
                "frontend generated SDK copies differ:\n" + "\n".join(differences)
            )

    def contract_check(self) -> None:
        state = self.inspect_workspace()
        self.check_backend_generation(state.revisions[BACKEND_REPOSITORY])
        self.check_frontend_sdk(state.revisions[BACKEND_REPOSITORY])
        print(
            "contract-check passed: gitlinks aligned; backend generation is current; "
            "frontend SDK is current and both generated copies are byte-identical"
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("knowledge", "contract"))
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--frontend", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    checker = WorkspaceChecker(
        arguments.root,
        backend=arguments.backend,
        frontend=arguments.frontend,
    )
    try:
        if arguments.check == "knowledge":
            checker.knowledge_check()
        else:
            checker.contract_check()
    except CheckError as error:
        print(f"workspace check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
