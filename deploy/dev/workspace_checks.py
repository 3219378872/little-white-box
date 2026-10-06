#!/usr/bin/env python3
"""Read-only knowledge and generated-contract checks for the root workspace.

knowledge: the root gitlinks match both child checkouts, root evidence pages
are well formed, every external_upstream reference resolves to exactly one
formal document or approved SPEC clause at a reachable commit, and both child
knowledge gates pass without modifying their checkouts.

contract: backend generated code has no drift from openapi.yaml/.proto (in a
temporary clone), and the frontend SDK matches the pinned backend OpenAPI.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import os
import json
import hashlib

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "Run just knowledge-setup to install isolated tool dependencies"
    ) from exc
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Mapping, Sequence


# --- Repositories and identifier grammar ------------------------------------
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
# Frontend SDK files generated from the gateway OpenAPI; vendor/sdk_source and
# lib/sdk must hold byte-identical copies.
GENERATED_SDK_FILES = (
    PurePosixPath("api/gateway.dart"),
    PurePosixPath("data/gateway.dart"),
)
# --- Root evidence page vocabulary -------------------------------------------
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
EVIDENCE_STATUSES = {"active", "superseded"}
EVIDENCE_RESULTS = {"passed", "partial", "failed", "blocked"}
# Revisions are derived from observed_commit's gitlinks, never stored.
DERIVED_EVIDENCE_KEYS = {"backend_commit", "frontend_commit", "upstream"}
COVERAGE_GROUP_KEYS = {"requirements", "paths"}

# --- knowledge-export manifest vocabulary ------------------------------------
KNOWLEDGE_LAYERS = {
    "INT": "intent",
    "SPEC": "spec",
    "DES": "design",
    "IMP": "implementation",
    "EVD": "evidence",
}
LAYER_STATUSES = {
    "intent": {"draft", "approved", "retired"},
    "spec": {"draft", "approved", "retired"},
    "design": {"draft", "active", "blocked", "superseded"},
    "implementation": {"active", "retired", "aligned", "unknown", "diverged"},
    "evidence": {"active", "superseded"},
}

COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
TEXT_SHA256_RE = re.compile(r"[0-9a-f]{64}")
ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class CheckError(RuntimeError):
    """A user-actionable workspace validation failure."""


@dataclass(frozen=True)
class Repository:
    """One of the three repositories; root_tree_path is its gitlink path."""

    name: str
    path: Path
    root_tree_path: str | None
    knowledge_path: PurePosixPath


@dataclass(frozen=True)
class ExternalReference:
    """A parsed `repo@<sha>:<target id>` external_upstream entry."""

    repository: str
    sha: str
    target_id: str


@dataclass(frozen=True)
class WorkspaceState:
    """Verified revisions: root HEAD plus the child commits it pins."""

    repositories: Mapping[str, Repository]
    revisions: Mapping[str, str]


# --- Process and Git helpers --------------------------------------------------


def _format_command(command: Sequence[str]) -> str:
    return " ".join(command)


def _decoded(output: str | bytes) -> str:
    return output if isinstance(output, str) else output.decode(errors="replace")


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """Run COMMAND capturing output; with check, failures become CheckError."""
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
        stdout = _decoded(result.stdout).strip()
        stderr = _decoded(result.stderr).strip()
        details = "\n".join(part for part in (stdout, stderr) if part)
        suffix = f"\n{details}" if details else ""
        raise CheckError(
            f"command failed ({result.returncode}): {_format_command(command)}{suffix}"
        )
    return result


def _git_output(repository: Path, *arguments: str) -> str:
    return _run(["git", *arguments], cwd=repository).stdout.strip()


def _git_status(repository: Path) -> bytes:
    """Porcelain status including untracked files; empty means clean."""
    return _run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repository,
        text=False,
    ).stdout


def _git_snapshot(repository: Path) -> tuple[str, bytes]:
    """HEAD plus working-tree status, to prove a child command changed nothing."""
    return _git_output(repository, "rev-parse", "HEAD"), _git_status(repository)


def _parse_gitlink(tree_entry: str, tree_path: str) -> str | None:
    """Commit SHA of a `git ls-tree` line for a 160000 gitlink at TREE_PATH."""
    match = re.fullmatch(
        rf"160000 commit ([0-9a-f]{{40}})\t{re.escape(tree_path)}", tree_entry
    )
    return match.group(1) if match else None


def _emit_process_output(result: subprocess.CompletedProcess) -> None:
    """Forward a captured child's output, newline-terminated."""
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
    # Only a clean checkout can prove that the gate did not write anything.
    if _git_status(repository):
        raise CheckError(
            f"{label} requires a clean checked-out repository before it can verify "
            f"read-only execution: {repository}"
        )
    before = _git_snapshot(repository)
    print(f"== {label} ==")
    result: subprocess.CompletedProcess | None = None
    command_error: CheckError | None = None
    try:
        result = _run(command, cwd=repository, env=env, check=False)
    except CheckError as error:
        command_error = error
    finally:
        # Compare even when the command could not start at all.
        after = _git_snapshot(repository)
    # A write is reported before (and instead of) the gate's own failure.
    if before != after:
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


# --- Front matter and reference parsing --------------------------------------


class UniqueLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate or non-text mapping keys."""


def _unique_mapping(loader, node, deep=False):
    values = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in values:
            raise CheckError(f"duplicate or non-text YAML key: {key!r}")
        values[key] = loader.construct_object(value_node, deep=deep)
    return values


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def parse_frontmatter(text: str, *, source: str) -> dict:
    """The leading `---` YAML block as a dict ({} when there is none)."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    try:
        end = lines.index("---", 1)
        value = yaml.load("\n".join(lines[1:end]), Loader=UniqueLoader)
    except (ValueError, yaml.YAMLError) as exc:
        raise CheckError(f"{source}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckError(f"{source}: frontmatter must be a mapping")
    return value


def parse_frontmatter_scalar(text: str, key: str, *, source: str) -> str | None:
    value = parse_frontmatter(text, source=source).get(key)
    # YAML turns 2026-01-02 into a date; evidence compares it as text.
    if isinstance(value, date):
        value = value.isoformat()
    if value is not None and not isinstance(value, str):
        raise CheckError(f"{source}: {key} must be text")
    return value


def parse_document_id(text: str, *, source: str) -> str | None:
    return parse_frontmatter_scalar(text, "id", source=source)


def require_text_list(value, label: str, *, allow_empty: bool) -> list[str]:
    """VALUE as a duplicate-free list of non-blank strings, else CheckError."""
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        qualifier = "" if allow_empty else "non-empty "
        raise CheckError(f"{label} must be a {qualifier}list of non-blank text")
    if len(set(value)) != len(value):
        raise CheckError(f"{label} has duplicate items")
    return value


def parse_frontmatter_list(text: str, key: str, *, source: str) -> list[str] | None:
    """Front-matter list KEY (None when absent); may be empty."""
    value = parse_frontmatter(text, source=source).get(key)
    if value is None:
        return None
    return require_text_list(value, f"{source}: {key}", allow_empty=True)


def parse_external_references(text: str, *, source: str) -> list[ExternalReference]:
    """All external_upstream entries; present-but-empty is an error."""
    meta = parse_frontmatter(text, source=source)
    if "external_upstream" not in meta:
        return []
    values = parse_frontmatter_list(text, "external_upstream", source=source)
    if not values:
        raise CheckError(f"{source}: external_upstream must be non-empty")
    return [parse_external_reference(value, source=source) for value in values]


def parse_external_reference(value: str, *, source: str) -> ExternalReference:
    """Parse `repo@sha:target`; requirement ids must use the target repo's grammar."""
    match = EXTERNAL_REF_RE.fullmatch(value)
    if not match:
        raise CheckError(f"{source}: invalid external_upstream: {value}")
    repo, sha, target = match.group("repository", "sha", "target_id")
    if not FORMAL_ID_RE.fullmatch(target) and (
        repo not in REQUIREMENT_ID_RES or not REQUIREMENT_ID_RES[repo].fullmatch(target)
    ):
        raise CheckError(
            f"{source}: invalid requirement target for repository: {target}"
        )
    return ExternalReference(repo, sha, target)


def unique_json_object(pairs):
    """json object_pairs_hook that rejects duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckError(f"duplicate knowledge-export JSON key: {key}")
        result[key] = value
    return result


# --- Checker ------------------------------------------------------------------


class WorkspaceChecker:
    """Runs the knowledge and contract checks against one workspace checkout."""

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
                ROOT_EVIDENCE_PATH,
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
        # Exports and tree scans are pure functions of (repository, revision).
        self._target_id_cache: dict[tuple[str, str], dict[str, list[str]]] = {}
        self._manifest_cache: dict[tuple[str, str], dict] = {}

    # --- Workspace and gitlinks --------------------------------------------

    def _require_repository(self, repository: Repository) -> str:
        """HEAD of a child checkout that must be its own Git top level."""
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
        """Verify root HEAD pins exactly the commits checked out in both children."""
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
            tree_path = repository.root_tree_path or ""
            gitlink_revision = _parse_gitlink(
                _git_output(self.root, "ls-tree", "HEAD", "--", tree_path), tree_path
            )
            if gitlink_revision is None:
                raise CheckError(
                    f"root HEAD does not contain the expected 160000 gitlink: "
                    f"{repository.root_tree_path}"
                )
            checkout_revision = self._require_repository(repository)
            if checkout_revision != gitlink_revision:
                raise CheckError(
                    f"submodule HEAD mismatch for {repository.root_tree_path}: "
                    f"root pins {gitlink_revision}, checkout is {checkout_revision}"
                )
            revisions[name] = gitlink_revision
        return WorkspaceState(self.repositories, revisions)

    def _gitlink_at_revision(self, revision: str, tree_path: str) -> str:
        """Child commit pinned by an arbitrary root REVISION."""
        gitlink = _parse_gitlink(
            _git_output(self.root, "ls-tree", revision, "--", tree_path), tree_path
        )
        if gitlink is None:
            raise CheckError(
                f"root revision {revision} has no 160000 gitlink for {tree_path}"
            )
        return gitlink

    # --- Root evidence pages -----------------------------------------------

    def _working_documents(self, repository: Repository) -> Iterable[tuple[str, str]]:
        """(relative path, text) of root evidence Markdown in the working tree.

        Child documents are only ever read from knowledge-export at a fixed
        revision, never from a possibly dirty working tree.
        """
        if repository.name != ROOT_REPOSITORY:
            raise CheckError("child documents must be read through knowledge-export")
        base = repository.path / repository.knowledge_path
        for path in sorted(base.rglob("*.md")):
            if not path.resolve().is_relative_to(base.resolve()):
                raise CheckError(f"root evidence escapes owned directory: {path}")
            yield (
                path.relative_to(repository.path).as_posix(),
                path.read_text(encoding="utf-8"),
            )

    def _validate_root_evidence_location(self) -> None:
        """Evidence-like root Markdown outside ROOT_EVIDENCE_PATH is an error."""
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
        for raw_path in paths:
            path = PurePosixPath(raw_path)
            if path.is_relative_to(ROOT_EVIDENCE_PATH):
                continue
            document = self.root / path
            if not document.is_file():
                continue
            # An EVD id or any external_upstream marks a page as evidence.
            text = document.read_text(encoding="utf-8")
            document_id = parse_document_id(text, source=raw_path)
            references = parse_external_references(text, source=raw_path)
            if references or (document_id and document_id.startswith("EVD-")):
                raise CheckError(
                    f"root integration evidence must live under {ROOT_EVIDENCE_PATH}: {raw_path}"
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
        if not COMMIT_SHA_RE.fullmatch(value):
            raise CheckError(f"{source}: {key} must be a 40-character lowercase SHA")
        return value

    def _validate_root_evidence_schema(self, state: WorkspaceState) -> int:
        """Validate every root EVD page; returns how many pages were checked."""
        count = 0
        root = state.repositories[ROOT_REPOSITORY]
        for raw_path, text in self._working_documents(root):
            path = PurePosixPath(raw_path)
            if path.name == "README.md" and path.parent == ROOT_EVIDENCE_PATH:
                continue
            self._validate_root_evidence_page(state, path, text)
            count += 1
        return count

    def _validate_root_evidence_page(
        self, state: WorkspaceState, path: PurePosixPath, text: str
    ) -> None:
        """One root EVD page: header, observation commit, lists, coverage."""
        raw_path = path.as_posix()
        source = f"{ROOT_REPOSITORY}:{raw_path}"
        meta = parse_frontmatter(text, source=source)
        identity, status, result = self._validate_evidence_header(path, text, source)

        # observed_commit must be reachable from root HEAD; child revisions are
        # derived from its gitlinks rather than stored on the page.
        observed = self._required_sha(text, "observed_commit", source=source)
        root = state.repositories[ROOT_REPOSITORY]
        self._commit_exists_and_is_reachable(
            root, observed, state.revisions[ROOT_REPOSITORY]
        )
        revisions = self._reference_limits(root, raw_path, text, state)
        if status == "active" and DERIVED_EVIDENCE_KEYS & meta.keys():
            raise CheckError(
                f"{source}: child revisions and upstream are derived, not stored"
            )

        self._validate_evidence_lists(text, source)
        self._validate_evidence_artifacts(text, source, status, result)

        # Superseded pages may predate coverage groups.
        groups = meta.get("coverage")
        if status == "superseded" and groups is None:
            return
        if "covers" in meta or not isinstance(groups, list) or not groups:
            raise CheckError(
                f"{source}: use non-empty coverage groups, not duplicated covers"
            )
        seen: set[str] = set()
        involved = {
            ref.repository for ref in parse_external_references(text, source=source)
        }
        for group in groups:
            involved |= self._validate_coverage_group(
                group, source, identity, status, result, revisions, state, seen
            )
        if not {BACKEND_REPOSITORY, FRONTEND_REPOSITORY} <= involved:
            raise CheckError(
                f"{source}: cross-repository evidence must involve both children"
            )

    def _validate_evidence_header(
        self, path: PurePosixPath, text: str, source: str
    ) -> tuple[str, str, str]:
        """Canonical EVD id and file name, status/result vocabulary and date."""
        identity = self._required_scalar(text, "id", source=source)
        if (
            path.parent != ROOT_EVIDENCE_PATH
            or path.name != identity + ".md"
            or not identity.startswith("EVD-")
            or not FORMAL_ID_RE.fullmatch(identity)
        ):
            raise CheckError(
                f"{source}: root evidence must be a canonical direct EVD page"
            )
        status = self._required_scalar(text, "status", source=source)
        result = self._required_scalar(text, "result", source=source)
        if status not in EVIDENCE_STATUSES or result not in EVIDENCE_RESULTS:
            raise CheckError(f"{source}: invalid evidence status/result")
        updated = self._required_scalar(text, "updated_at", source=source)
        try:
            if not ISO_DATE_RE.fullmatch(updated):
                raise ValueError(updated)
            date.fromisoformat(updated)
        except ValueError as exc:
            raise CheckError(f"{source}: invalid evidence date") from exc
        return identity, status, result

    @staticmethod
    def _validate_evidence_lists(text: str, source: str) -> None:
        """commands and scope are required; scope uses the shared vocabulary."""
        for key in ("commands", "scope"):
            values = parse_frontmatter_list(text, key, source=source)
            if not values:
                raise CheckError(f"{source}: {key} must be a non-empty list")
            if key == "scope" and not set(values) <= EVIDENCE_SCOPES:
                raise CheckError(f"{source}: invalid evidence scope")

    def _validate_evidence_artifacts(
        self, text: str, source: str, status: str, result: str
    ) -> None:
        """Repository artifacts must exist; /tmp alone cannot back a pass."""
        artifacts = parse_frontmatter_list(text, "artifacts", source=source) or []
        if (
            status == "active"
            and result == "passed"
            and artifacts
            and all(p.startswith("/tmp/") for p in artifacts)
        ):
            raise CheckError(f"{source}: passed evidence cannot use only /tmp artifacts")
        for artifact in artifacts:
            if artifact.startswith(("https://", "http://", "/tmp/")):
                continue
            self._safe_input(artifact)
            if not (self.root / artifact).is_file():
                raise CheckError(f"{source}: missing durable artifact: {artifact}")

    def _validate_coverage_group(
        self,
        group,
        source: str,
        identity: str,
        status: str,
        result: str,
        revisions: Mapping[str, str],
        state: WorkspaceState,
        seen: set[str],
    ) -> set[str]:
        """Check one {requirements, paths} group; returns repositories it names.

        Each requirement must exist exactly once at the observed revision and
        each input path must have existed there. Active groups also report
        whether their inputs changed since (staleness never rewrites history).
        """
        if not isinstance(group, dict) or set(group) != COVERAGE_GROUP_KEYS:
            raise CheckError(f"{source}: coverage group requires requirements and paths")
        requirements = require_text_list(
            group["requirements"], "coverage requirements", allow_empty=False
        )
        # A non-passed result may record that its inputs are unknown.
        paths = require_text_list(
            group["paths"], "coverage paths", allow_empty=result != "passed"
        )
        involved = set()
        for requirement in requirements:
            involved.add(
                self._validate_coverage_requirement(
                    requirement, source, revisions, state, seen
                )
            )
        for raw in paths:
            self._validate_coverage_input(raw, source, revisions)
        if status == "active":
            reason = self._coverage_changes(group, revisions, state)
            print(
                f"root evidence {identity} [{', '.join(requirements)}]: "
                f"{reason or result + ' at unchanged inputs'}"
            )
        return involved

    def _validate_coverage_requirement(
        self,
        requirement: str,
        source: str,
        revisions: Mapping[str, str],
        state: WorkspaceState,
        seen: set[str],
    ) -> str:
        """`repo:ID` must be unique on the page and resolve once at observation."""
        repo, separator, target = requirement.partition(":")
        if (
            not separator
            or repo not in REQUIREMENT_ID_RES
            or not REQUIREMENT_ID_RES[repo].fullmatch(target)
            or requirement in seen
        ):
            raise CheckError(
                f"{source}: invalid or duplicate qualified requirement: {requirement}"
            )
        seen.add(requirement)
        matches = self._target_ids_at_revision(
            state.repositories[repo], revisions[repo]
        ).get(target, [])
        if len(matches) != 1:
            raise CheckError(
                f"{source}: covered requirement is missing at observation: {requirement}"
            )
        return repo

    def _validate_coverage_input(
        self, raw: str, source: str, revisions: Mapping[str, str]
    ) -> None:
        """An input path must have existed at the observed revision."""
        self._safe_input(raw)
        repo, commit, relative = self._input_revision(raw, revisions)
        exists = _run(
            ["git", "cat-file", "-e", f"{commit}:{'' if relative == '.' else relative}"],
            cwd=repo.path,
            check=False,
        )
        if exists.returncode:
            raise CheckError(f"{source}: input did not exist at observation: {raw}")

    @staticmethod
    def _safe_input(raw: str) -> PurePosixPath:
        """A normalized, relative, non-escaping repository path."""
        path = PurePosixPath(raw)
        if (
            not raw.strip()
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != raw
            or raw == "."
        ):
            raise CheckError(f"unsafe repository input path: {raw}")
        return path

    def _input_revision(self, raw: str, revisions):
        """(repository, revision, repo-relative path) for a root-relative input."""
        path = self._safe_input(raw)
        if path.parts[0] in {BACKEND_REPOSITORY, FRONTEND_REPOSITORY}:
            name = path.parts[0]
            relative = PurePosixPath(*path.parts[1:]).as_posix()
        else:
            name, relative = ROOT_REPOSITORY, raw
        return self.repositories[name], revisions[name], relative

    def _coverage_changes(self, group, observed, state):
        """Why an active group no longer proves the current tree ("" if it does)."""
        if not group["paths"]:
            return "historical inputs unknown; not current proof"
        stale = self._stale_requirement(group["requirements"], observed, state)
        if stale:
            return f"stale requirement {stale}; historical result retained"
        stale = self._stale_input(group["paths"], observed)
        if stale:
            return f"stale input {stale}; historical result retained"
        return ""

    def _stale_requirement(self, qualified_ids, observed, state) -> str | None:
        """First requirement whose SPEC owner or text fingerprint changed."""
        for qualified in qualified_ids:
            repo, requirement = qualified.split(":", 1)
            repository = self.repositories[repo]
            before = self._requirement_fingerprints(repository, observed[repo])
            after = self._requirement_fingerprints(repository, state.revisions[repo])
            if requirement not in after or before.get(requirement) != after[requirement]:
                return qualified
        return None

    def _requirement_fingerprints(
        self, repository: Repository, revision: str
    ) -> dict[str, tuple[str, str]]:
        manifest = self._manifest_at_revision(repository, revision)
        return {
            r["id"]: (r["spec_id"], r["text_sha256"]) for r in manifest["requirements"]
        }

    def _stale_input(self, raw_paths, observed) -> str | None:
        """First input with committed, staged or untracked changes since observation."""
        environment = dict(self.environment, GIT_LITERAL_PATHSPECS="1")
        for raw in raw_paths:
            repository, commit, relative = self._input_revision(raw, observed)
            for args in (
                ("diff", "--name-only", "-z", commit, "--", relative),
                ("diff", "--cached", "--name-only", "-z", commit, "--", relative),
                ("ls-files", "--others", "--exclude-standard", "-z", "--", relative),
            ):
                if _run(
                    ["git", *args], cwd=repository.path, env=environment, text=False
                ).stdout:
                    return raw
        return None

    def _commit_exists_and_is_reachable(
        self, repository: Repository, sha: str, pinned_revision: str
    ) -> None:
        """SHA must exist and be an ancestor of (or equal to) PINNED_REVISION."""
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
        # merge-base exits 1 for "not an ancestor" and >1 for real errors.
        if reachable.returncode == 1:
            raise CheckError(
                f"external_upstream commit {repository.name}@{sha} is not reachable "
                f"from pinned revision {pinned_revision}"
            )
        if reachable.returncode != 0:
            raise CheckError(
                f"could not compare revisions in {repository.name}: {sha} and {pinned_revision}"
            )

    # --- Child knowledge manifests -----------------------------------------

    def _manifest_at_revision(self, repository: Repository, revision: str) -> dict:
        """Validated knowledge-export manifest of a child at REVISION (cached)."""
        key = (repository.name, revision)
        if key in self._manifest_cache:
            return self._manifest_cache[key]
        if repository.name == ROOT_REPOSITORY:
            raise CheckError("root evidence is not a child knowledge manifest")
        manifest = self._export_manifest(repository, revision)
        documents = self._validate_manifest_documents(manifest["documents"])
        self._validate_manifest_requirements(
            manifest["requirements"], documents, repository.name
        )
        self._validate_manifest_references(
            manifest["external_upstream"], documents, repository.name
        )
        self._manifest_cache[key] = manifest
        return manifest

    def _export_manifest(self, repository: Repository, revision: str) -> dict:
        """Run `make knowledge-export REF=...` read-only and check its envelope."""
        # The export must neither need nor cause working-tree changes.
        before = _git_snapshot(repository.path)
        if before[1]:
            raise CheckError(
                f"knowledge-export requires a clean repository: {repository.name}"
            )
        result = _run(
            [
                "make",
                "--no-print-directory",
                "-s",
                "knowledge-export",
                f"REF={revision}",
            ],
            cwd=repository.path,
            env=self._child_environment(repository.name),
            check=False,
        )
        if _git_snapshot(repository.path) != before:
            raise CheckError(f"knowledge-export modified {repository.name}")
        if result.returncode:
            raise CheckError(
                f"knowledge-export failed for {repository.name}@{revision}: {result.stderr.strip()}"
            )

        # schema_version 1 envelope for exactly this repository and revision.
        try:
            manifest = json.loads(result.stdout, object_pairs_hook=unique_json_object)
        except (ValueError, TypeError) as exc:
            raise CheckError(
                f"invalid knowledge-export JSON from {repository.name}"
            ) from exc
        if (
            not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or manifest.get("repository") != repository.name
            or manifest.get("revision") != revision
        ):
            raise CheckError(
                f"knowledge-export schema/repository/revision mismatch: {repository.name}"
            )
        if not all(
            isinstance(manifest.get(field), list)
            for field in ("documents", "requirements", "external_upstream")
        ):
            raise CheckError(
                f"knowledge-export requires document, requirement and reference arrays: {repository.name}"
            )
        return manifest

    @staticmethod
    def _validate_manifest_documents(documents: list) -> dict[str, dict]:
        """Unique formal ids with canonical layer, path and lifecycle status."""
        by_id: dict[str, dict] = {}
        for doc in documents:
            if not isinstance(doc, dict):
                raise CheckError("invalid exported document")
            identity = doc.get("id", "")
            if (
                not isinstance(identity, str)
                or not FORMAL_ID_RE.fullmatch(identity)
                or identity in by_id
            ):
                raise CheckError(f"duplicate or invalid exported document: {identity}")
            layer = KNOWLEDGE_LAYERS.get(identity.split("-", 1)[0])
            if (
                doc.get("layer") != layer
                or doc.get("path") != f"docs/knowledge/{layer}/{identity}.md"
                or not isinstance(doc.get("status"), str)
                or doc["status"] not in LAYER_STATUSES[layer]
            ):
                raise CheckError(f"noncanonical exported document: {identity}")
            by_id[identity] = doc
        return by_id

    @staticmethod
    def _validate_manifest_requirements(
        requirements: list, documents: Mapping[str, dict], repository_name: str
    ) -> None:
        """Each clause belongs to one approved SPEC and matches its fingerprint."""
        seen = set()
        for requirement in requirements:
            if not isinstance(requirement, dict):
                raise CheckError("invalid exported requirement")
            identity = requirement.get("id", "")
            if (
                not isinstance(identity, str)
                or not REQUIREMENT_ID_RES[repository_name].fullmatch(identity)
                or identity in seen
            ):
                raise CheckError(
                    f"duplicate or invalid exported requirement: {identity}"
                )
            spec_id = requirement.get("spec_id")
            owner = documents.get(spec_id) if isinstance(spec_id, str) else None
            if (
                not owner
                or owner["layer"] != "spec"
                or owner["status"] != "approved"
                or requirement.get("path") != owner["path"]
                or not isinstance(requirement.get("text_sha256"), str)
                or not TEXT_SHA256_RE.fullmatch(requirement["text_sha256"])
            ):
                raise CheckError(
                    f"requirement has no unique approved SPEC definition: {identity}"
                )
            # The fingerprint must be recomputable from the exported definition.
            definition = requirement.get("definition")
            if (
                not isinstance(definition, str)
                or not definition.strip()
                or hashlib.sha256(definition.encode()).hexdigest()
                != requirement["text_sha256"]
            ):
                raise CheckError(
                    f"exported requirement fingerprint mismatch: {identity}"
                )
            seen.add(identity)

    @staticmethod
    def _validate_manifest_references(
        references: list, documents: Mapping[str, dict], repository_name: str
    ) -> None:
        """Exported references come from a formal document and parse cleanly."""
        for reference in references:
            if (
                not isinstance(reference, dict)
                or not isinstance(reference.get("source_id"), str)
                or reference["source_id"] not in documents
            ):
                raise CheckError("exported external reference has no formal source")
            raw = f"{reference.get('repository')}@{reference.get('revision')}:{reference.get('target_id')}"
            parse_external_reference(raw, source=repository_name)

    def _target_ids_at_revision(
        self, repository: Repository, revision: str
    ) -> dict[str, list[str]]:
        """Formal ids (and requirement ids) at REVISION -> paths defining them."""
        key = (repository.name, revision)
        if key in self._target_id_cache:
            return self._target_id_cache[key]
        if repository.name != ROOT_REPOSITORY:
            manifest = self._manifest_at_revision(repository, revision)
            targets = {
                record["id"]: [record["path"]]
                for record in manifest["documents"] + manifest["requirements"]
            }
        else:
            targets = self._root_evidence_ids_at_revision(revision)
        self._target_id_cache[key] = targets
        return targets

    def _root_evidence_ids_at_revision(self, revision: str) -> dict[str, list[str]]:
        """Canonical root EVD pages committed at REVISION, read from Git blobs."""
        targets: dict[str, list[str]] = {}
        paths = _run(
            [
                "git",
                "ls-tree",
                "-r",
                "--name-only",
                "-z",
                revision,
                "--",
                ROOT_EVIDENCE_PATH.as_posix(),
            ],
            cwd=self.root,
            text=False,
        ).stdout.split(b"\0")
        for raw in paths:
            if not raw:
                continue
            path = PurePosixPath(os.fsdecode(raw))
            if (
                path.parent != ROOT_EVIDENCE_PATH
                or path.name == "README.md"
                or path.suffix != ".md"
            ):
                continue
            text = _run(["git", "show", f"{revision}:{path}"], cwd=self.root).stdout
            identity = parse_document_id(text, source=str(path))
            if (
                identity
                and identity.startswith("EVD-")
                and FORMAL_ID_RE.fullmatch(identity)
                and path.stem == identity
            ):
                targets.setdefault(identity, []).append(path.as_posix())
        return targets

    def _reference_limits(
        self, source_repository: Repository, path: str, text: str, state: WorkspaceState
    ) -> Mapping[str, str]:
        """Newest revisions a page may cite: its observation for root EVD pages."""
        if (
            source_repository.name != ROOT_REPOSITORY
            or PurePosixPath(path).name == "README.md"
        ):
            return state.revisions
        observed = self._required_sha(text, "observed_commit", source=path)
        return {
            ROOT_REPOSITORY: observed,
            BACKEND_REPOSITORY: self._gitlink_at_revision(observed, BACKEND_REPOSITORY),
            FRONTEND_REPOSITORY: self._gitlink_at_revision(
                observed, FRONTEND_REPOSITORY
            ),
        }

    def validate_external_upstream(self, state: WorkspaceState) -> int:
        """Resolve every external_upstream reference; returns how many."""
        self._validate_root_evidence_location()
        self._validate_root_evidence_schema(state)

        # Collect (source, reference, newest allowed revisions): child references
        # come from their exports, root ones from the evidence pages.
        pending = []
        for name in (BACKEND_REPOSITORY, FRONTEND_REPOSITORY):
            manifest = self._manifest_at_revision(
                state.repositories[name], state.revisions[name]
            )
            for ref in manifest["external_upstream"]:
                pending.append(
                    (
                        f"{name}:{ref['source_id']}",
                        ExternalReference(
                            ref["repository"], ref["revision"], ref["target_id"]
                        ),
                        state.revisions,
                    )
                )
        for path, text in self._working_documents(state.repositories[ROOT_REPOSITORY]):
            limits = self._reference_limits(
                state.repositories[ROOT_REPOSITORY], path, text, state
            )
            pending.extend(
                (path, ref, limits)
                for ref in parse_external_references(text, source=path)
            )

        # Each reference: reachable commit and exactly one matching target.
        for source, reference, limits in pending:
            target = state.repositories[reference.repository]
            self._commit_exists_and_is_reachable(
                target, reference.sha, limits[reference.repository]
            )
            matches = self._target_ids_at_revision(target, reference.sha).get(
                reference.target_id, []
            )
            if len(matches) != 1:
                raise CheckError(
                    f"{source}: external_upstream target must resolve to exactly one formal document or approved-SPEC requirement; found {len(matches)} matches for {reference.repository}@{reference.sha}:{reference.target_id}"
                )
        return len(pending)

    # --- Gates ----------------------------------------------------------------

    def _child_environment(self, repository: str | None = None) -> dict[str, str]:
        """Environment for child make targets.

        The root's KNOWLEDGE_PYTHON belongs to the root venv; a child only gets
        one through BACKEND_/FRONTEND_KNOWLEDGE_PYTHON.
        """
        environment = dict(self.environment)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment.pop("KNOWLEDGE_PYTHON", None)
        key = (
            "BACKEND_KNOWLEDGE_PYTHON"
            if repository == BACKEND_REPOSITORY
            else "FRONTEND_KNOWLEDGE_PYTHON"
        )
        if repository and environment.get(key):
            environment["KNOWLEDGE_PYTHON"] = environment[key]
        return environment

    def knowledge_check(self) -> None:
        """`just knowledge-check`: root references, then both child gates."""
        state = self.inspect_workspace()
        reference_count = self.validate_external_upstream(state)
        run_read_only_command(
            self.repositories[BACKEND_REPOSITORY].path,
            ["make", "engineering-lint"],
            label="backend knowledge gate",
            env=self._child_environment(BACKEND_REPOSITORY),
        )
        run_read_only_command(
            self.repositories[FRONTEND_REPOSITORY].path,
            ["make", "knowledge-check"],
            label="frontend knowledge gate",
            env=self._child_environment(FRONTEND_REPOSITORY),
        )
        print(
            "knowledge-check passed: gitlinks aligned; child gates passed; "
            f"{reference_count} external_upstream reference(s) validated"
        )

    # --- Contract checks ----------------------------------------------------

    def _python_supports_grpc_tools(self, executable: Path) -> bool:
        """EXECUTABLE can import every backend generation dependency."""
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return False
        result = _run(
            [str(executable), "-c", "import grpc_tools.protoc; import yaml; import openapi_spec_validator"],
            cwd=self.root,
            check=False,
        )
        return result.returncode == 0

    def _configured_path(self, raw: str) -> Path:
        """User-supplied path: ~ expanded, relative to the workspace root.

        absolute() rather than resolve(): a virtualenv python is a symlink
        whose location (not its target) selects the environment.
        """
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.absolute()

    def _generation_python(self) -> Path:
        """Python able to run backend `make generate`.

        Explicit settings win and must work; otherwise try PATH's python3 and
        the usual backend virtualenv locations.
        """
        explicit_python = self.environment.get("BACKEND_GENERATE_PYTHON", "").strip()
        explicit_bin = self.environment.get("GENERATE_PYTHON_BIN_DIR", "").strip()
        if explicit_python:
            candidate = self._configured_path(explicit_python)
            if self._python_supports_grpc_tools(candidate):
                return candidate
            raise CheckError(
                "BACKEND_GENERATE_PYTHON is not an executable Python with "
                f"grpc_tools.protoc: {candidate}"
            )
        if explicit_bin:
            candidate = self._configured_path(explicit_bin) / "python3"
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
        """Regenerate in a throwaway clone at the pinned commit; any diff is drift."""
        backend = self.repositories[BACKEND_REPOSITORY]
        generation_python = self._generation_python()
        with tempfile.TemporaryDirectory(
            prefix="xbh-backend-generate-",
            dir=self.temporary_parent,
        ) as temporary_directory:
            # Clone without hardlinks so generation can never touch the checkout.
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
            # The Makefile calls plain `python3`; put the chosen one first on PATH.
            shim = self._python_shim(temporary_root / "python-bin", generation_python)
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
            # Generated files are committed, so a clean clone means no drift.
            drift = _git_status(clone)
            if drift:
                rendered = (
                    drift.replace(b"\x00", b"\n").decode(errors="replace").strip()
                )
                raise CheckError(
                    "backend OpenAPI/.proto generated-code drift detected after make generate:\n"
                    + rendered
                )

    @staticmethod
    def _python_shim(directory: Path, python: Path) -> Path:
        """DIRECTORY containing a `python3` wrapper that execs PYTHON."""
        directory.mkdir(mode=0o700)
        wrapper = directory / "python3"
        wrapper.write_text(
            "#!/bin/sh\nexec " + shlex.quote(str(python)) + ' "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        return directory

    def check_frontend_sdk(self, pinned_backend_revision: str) -> None:
        """Frontend SDK regenerates cleanly from the pinned backend OpenAPI."""
        frontend = self.repositories[FRONTEND_REPOSITORY]
        backend = self.repositories[BACKEND_REPOSITORY]
        # Use the OpenAPI blob at the pinned commit, not the backend working tree.
        backend_api_relative = PurePosixPath("app/gateway/openapi.yaml")
        backend_api_blob = _run(
            ["git", "show", f"{pinned_backend_revision}:{backend_api_relative}"],
            cwd=backend.path,
            text=False,
        )
        with tempfile.TemporaryDirectory(
            prefix="xbh-backend-api-", dir=self.temporary_parent
        ) as temporary_directory:
            backend_api = Path(temporary_directory) / "openapi.yaml"
            backend_api.write_bytes(backend_api_blob.stdout)
            environment = self._child_environment()
            environment["BACKEND_API"] = str(backend_api)
            run_read_only_command(
                frontend.path,
                ["make", "sdk-check", f"BACKEND_API={backend_api}"],
                label="frontend generated SDK drift",
                env=environment,
            )

        differences = self._sdk_copy_differences(frontend.path)
        if differences:
            raise CheckError(
                "frontend generated SDK copies differ:\n" + "\n".join(differences)
            )

    @staticmethod
    def _sdk_copy_differences(frontend: Path) -> list[str]:
        """Missing or diverging files between vendor/sdk_source and lib/sdk."""
        differences: list[str] = []
        for relative in GENERATED_SDK_FILES:
            vendor = frontend / "vendor" / "sdk_source" / relative
            application = frontend / "lib" / "sdk" / relative
            if not vendor.is_file():
                differences.append(f"missing {vendor.relative_to(frontend)}")
            if not application.is_file():
                differences.append(f"missing {application.relative_to(frontend)}")
            if (
                vendor.is_file()
                and application.is_file()
                and vendor.read_bytes() != application.read_bytes()
            ):
                differences.append(
                    f"{vendor.relative_to(frontend)} != {application.relative_to(frontend)}"
                )
        return differences

    def contract_check(self) -> None:
        """`just contract-check`: backend generation, then the frontend SDK."""
        state = self.inspect_workspace()
        self.check_backend_generation(state.revisions[BACKEND_REPOSITORY])
        self.check_frontend_sdk(state.revisions[BACKEND_REPOSITORY])
        print(
            "contract-check passed: gitlinks aligned; backend generation is current; "
            "frontend SDK is current and both generated copies are byte-identical"
        )


# --- CLI ----------------------------------------------------------------------


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
