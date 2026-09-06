from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace_checks  # noqa: E402


BACKEND = workspace_checks.BACKEND_REPOSITORY
FRONTEND = workspace_checks.FRONTEND_REPOSITORY
ROOT_REPOSITORY = workspace_checks.ROOT_REPOSITORY


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout.strip()


def initialize_repository(repository: Path, files: dict[str, str]) -> str:
    repository.mkdir(parents=True, exist_ok=True)
    git(repository, "init", "--quiet")
    git(repository, "config", "user.name", "Workspace Check Test")
    git(repository, "config", "user.email", "workspace-check@example.invalid")
    for relative, content in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repository, "add", "--", *files)
    git(repository, "commit", "--quiet", "-m", "fixture")
    return git(repository, "rev-parse", "HEAD")


def commit_files(
    repository: Path, files: dict[str, str], message: str = "update"
) -> str:
    for relative, content in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", message)
    return git(repository, "rev-parse", "HEAD")


def document(document_id: str, external: str | None = None) -> str:
    lines = ["---", f"id: {document_id}"]
    if external is not None:
        lines.extend(("external_upstream:", f"  - {external}"))
    lines.extend(("---", "", f"# {document_id}", ""))
    return "\n".join(lines)


def spec_document(
    document_id: str,
    requirements: tuple[str, ...],
    *,
    status: str = "approved",
) -> str:
    lines = ["---", f"id: {document_id}", f"status: {status}", "---", ""]
    lines.extend(
        f"- `{requirement}`：fixture requirement" for requirement in requirements
    )
    lines.append("")
    return "\n".join(lines)


class WorkspaceFixture:
    def __init__(
        self,
        parent: Path,
        *,
        frontend_document: str | None = None,
        duplicate_backend_id: bool = False,
    ) -> None:
        self.root = parent / "workspace"
        self.root.mkdir()
        self.backend = self.root / BACKEND
        self.frontend = self.root / FRONTEND

        backend_files = {
            "docs/knowledge/intent/INT-backend.md": document("INT-backend"),
            "docs/knowledge/spec/SPEC-backend.md": spec_document(
                "SPEC-backend", ("CORE-001",)
            ),
            "app/gateway/gateway.api": "syntax = 'v1'\n",
            "generated.txt": "current\n",
            "Makefile": "generate:\n\t@:\n",
        }
        if duplicate_backend_id:
            backend_files["docs/knowledge/intent/INT-backend-copy.md"] = document(
                "INT-backend"
            )
        self.backend_revision = initialize_repository(self.backend, backend_files)
        if frontend_document is None:
            frontend_document = document(
                "IMP-frontend",
                f"{BACKEND}@{self.backend_revision}:INT-backend",
            )
        frontend_files = {
            "docs/knowledge/implementation/IMP-frontend.md": frontend_document,
            "vendor/sdk_source/api/gateway.dart": "api\n",
            "vendor/sdk_source/data/gateway.dart": "data\n",
            "lib/sdk/api/gateway.dart": "api\n",
            "lib/sdk/data/gateway.dart": "data\n",
            "Makefile": "sdk-check:\n\t@:\nknowledge-check:\n\t@:\n",
        }
        self.frontend_revision = initialize_repository(self.frontend, frontend_files)

        initialize_repository(self.root, {"README.md": "fixture\n"})
        self.pin_submodules()

    def pin_submodules(self) -> str:
        git(
            self.root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.backend_revision},{BACKEND}",
        )
        git(
            self.root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.frontend_revision},{FRONTEND}",
        )
        git(self.root, "commit", "--quiet", "-m", "pin child repositories")
        return git(self.root, "rev-parse", "HEAD")

    def checker(self, **kwargs) -> workspace_checks.WorkspaceChecker:
        return workspace_checks.WorkspaceChecker(self.root, **kwargs)

    def reference_frontend_requirement(
        self, specification: str, requirement: str
    ) -> None:
        self.frontend_revision = commit_files(
            self.frontend,
            {"docs/knowledge/spec/SPEC-frontend.md": specification},
        )
        self.backend_revision = commit_files(
            self.backend,
            {
                "docs/knowledge/implementation/IMP-cross.md": document(
                    "IMP-cross",
                    f"{FRONTEND}@{self.frontend_revision}:{requirement}",
                )
            },
        )
        self.pin_submodules()

    def write_integration_evidence(
        self,
        *,
        status: str = "active",
        result: str = "passed",
        observed_commit: str | None = None,
        backend_commit: str | None = None,
        frontend_commit: str | None = None,
        include_commands: bool = True,
        include_covers: bool = True,
        scopes: tuple[str, ...] = ("e2e",),
        artifacts: tuple[str, ...] = (
            "deploy/dev/e2e/evidence/integration-report.txt",
        ),
    ) -> Path:
        observed_commit = observed_commit or git(self.root, "rev-parse", "HEAD")
        backend_commit = backend_commit or self.backend_revision
        frontend_commit = frontend_commit or self.frontend_revision
        lines = [
            "---",
            "id: EVD-integration",
            f"status: {status}",
            f"result: {result}",
            "updated_at: 2026-09-06",
            f"observed_commit: {observed_commit}",
            f"backend_commit: {backend_commit}",
            f"frontend_commit: {frontend_commit}",
        ]
        if include_commands:
            lines.extend(("commands:", "  - just e2e"))
        if include_covers:
            lines.extend(("covers:", "  - CORE-001"))
        lines.append("scope:")
        lines.extend(f"  - {scope}" for scope in scopes)
        lines.extend(
            (
                "external_upstream:",
                f"  - {BACKEND}@{self.backend_revision}:CORE-001",
                f"  - {FRONTEND}@{self.frontend_revision}:IMP-frontend",
            )
        )
        if artifacts:
            lines.append("artifacts:")
            lines.extend(f"  - {artifact}" for artifact in artifacts)
        lines.extend(("---", "", "# Integration evidence", ""))
        path = self.root / "deploy/dev/e2e/evidence/EVD-integration.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


class ExternalReferenceParserTest(unittest.TestCase):
    def test_parses_locked_reference_shape(self):
        sha = "a" * 40
        references = workspace_checks.parse_external_references(
            document("IMP-client", f"{BACKEND}@{sha}:SPEC-community-core"),
            source="client.md",
        )
        self.assertEqual(
            references,
            [workspace_checks.ExternalReference(BACKEND, sha, "SPEC-community-core")],
        )

    def test_parses_requirement_target(self):
        sha = "b" * 40
        references = workspace_checks.parse_external_references(
            document("IMP-client", f"{BACKEND}@{sha}:AGENT-A10"),
            source="client.md",
        )
        self.assertEqual(references[0].target_id, "AGENT-A10")

    def test_requirement_target_uses_the_target_repository_grammar(self):
        sha = "c" * 40
        references = workspace_checks.parse_external_references(
            document("IMP-client", f"{FRONTEND}@{sha}:FX-001"),
            source="client.md",
        )
        self.assertEqual(references[0].target_id, "FX-001")

        with self.assertRaisesRegex(
            workspace_checks.CheckError, "invalid requirement target"
        ):
            workspace_checks.parse_external_references(
                document("IMP-client", f"{FRONTEND}@{sha}:CORE-001"),
                source="client.md",
            )

    def test_rejects_scalar_unknown_repo_short_sha_and_nonformal_id(self):
        invalid_documents = (
            "---\nid: IMP-client\nexternal_upstream: value\n---\n",
            "---\nid: IMP-client\nexternal_upstream:\n  - other@"
            + "a" * 40
            + ":SPEC-core\n---\n",
            f"---\nid: IMP-client\nexternal_upstream:\n  - {BACKEND}@abc:SPEC-core\n---\n",
            f"---\nid: IMP-client\nexternal_upstream:\n  - {BACKEND}@"
            + "a" * 40
            + ":CLAUSE-1\n---\n",
        )
        for text in invalid_documents:
            with self.subTest(text=text):
                with self.assertRaises(workspace_checks.CheckError):
                    workspace_checks.parse_external_references(
                        text, source="invalid.md"
                    )


class RequirementDefinitionTest(unittest.TestCase):
    def test_accepts_bullet_and_supported_table_headers(self):
        text = (
            "---\n"
            "id: SPEC-example\n"
            "metadata:\n"
            "  - `CORE-999`: not a body requirement\n"
            "---\n\n"
            "* `CORE-001`: bullet requirement\n\n"
            "| ID | Meaning |\n"
            "| --- | --- |\n"
            "| `REL-054-01` | ID table requirement |\n\n"
            "| 条款 | Meaning |\n"
            "| :--- | ---: |\n"
            "| CORE-002 | localized table requirement |\n\n"
            "Requirement | Meaning\n"
            "--- | ---\n"
            "CORE-003 | table without outer pipes\n"
        )

        self.assertEqual(
            workspace_checks.requirement_definitions(text, repository=BACKEND),
            ["CORE-001", "REL-054-01", "CORE-002", "CORE-003"],
        )

    def test_multiline_code_span_hides_comment_and_requirement_shapes(self):
        text = (
            "- Context ``starts a two-tick code span\n"
            "  <!-- remains literal inside the code span\n"
            "  prose with `HID-001` inside the code example\n"
            "  | Requirement | Meaning |\n"
            "  | --- | --- |\n"
            "  | HID-002 | code example, not a requirement |\n"
            "  a longer ``` run does not close the span\n"
            "  ``- `HID-003`: closing-line suffix is not a bullet\n"
            "- `CORE-777`: visible requirement\n"
        )

        self.assertEqual(
            workspace_checks.requirement_definitions(text, repository=BACKEND),
            ["CORE-777"],
        )

    def test_unclosed_code_span_stops_at_any_new_list_item(self):
        cases = (
            (
                "Top-level paragraph ``has no close\n"
                "- `CORE-707`: visible list requirement\n",
                "CORE-707",
            ),
            (
                "- Parent item ``has no close\n"
                "  - `CORE-708`: visible nested requirement\n",
                "CORE-708",
            ),
        )
        for text, visible in cases:
            with self.subTest(visible=visible):
                self.assertEqual(
                    workspace_checks.requirement_definitions(text, repository=BACKEND),
                    [visible],
                )

    def test_unclosed_code_span_stops_at_a_sibling_list_item(self):
        for marker in ("-", "*", "+", "1."):
            with self.subTest(marker=marker):
                text = (
                    f"{marker} Context ``has no close in this item\n"
                    "- `CORE-706`: visible sibling requirement\n"
                    "  A later `` run belongs to the sibling.\n"
                )

                self.assertEqual(
                    workspace_checks.requirement_definitions(text, repository=BACKEND),
                    ["CORE-706"],
                )

    def test_multiline_code_spans_require_exact_delimiter_lengths(self):
        cases = (
            ("`", "Runs `` and ``` do not close it", "CORE-711", "HID-011"),
            ("``", "Runs ` and ``` do not close it", "CORE-712", "HID-012"),
            (
                "```",
                "Runs `, ``, and ```` do not close it",
                "CORE-713",
                "HID-013",
            ),
        )

        for delimiter, mismatches, visible, hidden in cases:
            with self.subTest(delimiter=delimiter):
                text = (
                    f"The code span starts here: {delimiter}example\n"
                    f"{mismatches}\n"
                    "| Requirement | Meaning |\n"
                    "| --- | --- |\n"
                    f"| {hidden} | code example, not a requirement |\n"
                    f"the exact run closes it here: {delimiter}\n"
                    f"- `{visible}`: visible requirement\n"
                )
                self.assertEqual(
                    workspace_checks.requirement_definitions(text, repository=BACKEND),
                    [visible],
                )

    def test_unclosed_code_span_stops_at_markdown_block_boundaries(self):
        cases = {
            "blank": (
                "Unclosed `` span\n\n- `CORE-701`: visible\nLater `` run\n",
                "CORE-701",
            ),
            "atx heading": (
                (
                    "Unclosed `` span\n"
                    "# New section\n"
                    "- `CORE-702`: visible\n"
                    "Later `` run\n"
                ),
                "CORE-702",
            ),
            "setext heading": (
                (
                    "Unclosed `` span\n"
                    "New section\n"
                    "---\n"
                    "- `CORE-703`: visible\n"
                    "Later `` run\n"
                ),
                "CORE-703",
            ),
            "fence": (
                (
                    "Unclosed `` span\n"
                    "```markdown\n"
                    "``\n"
                    "- `HID-004`: fenced example\n"
                    "```\n"
                    "- `CORE-704`: visible\n"
                ),
                "CORE-704",
            ),
        }

        for name, (text, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    workspace_checks.requirement_definitions(text, repository=BACKEND),
                    [expected],
                )

    def test_invalid_backtick_fence_info_cannot_hide_following_requirement(self):
        text = "```text`invalid\n- `CORE-705`: visible requirement\n```\n"

        self.assertEqual(
            workspace_checks.requirement_definitions(text, repository=BACKEND),
            ["CORE-705"],
        )

    def test_list_container_fences_hide_all_content(self):
        cases = (
            ("-", "~~~", "markdown", "  ", "CORE-721"),
            ("*", "```", "markdown", "  ", "CORE-722"),
            ("+", "````", "markdown", "  ", "CORE-723"),
            ("1.", "~~~", "text`allowed", "   ", "CORE-724"),
            ("- 1.", "```", "markdown", "     ", "CORE-726"),
        )
        for list_marker, fence, info, indentation, visible in cases:
            with self.subTest(list_marker=list_marker, fence=fence):
                shorter_fence = fence[:-1]
                text = (
                    f"{list_marker} {fence}{info}\n"
                    f"{indentation}<!-- literal comment opener\n"
                    f"{indentation}{shorter_fence}\n"
                    f"{indentation}text with an inline-looking ``` run\n"
                    f"{indentation}- `HID-021`: fenced requirement example\n"
                    f"{indentation}{fence}\n"
                    f"- `{visible}`: visible requirement\n"
                )
                self.assertEqual(
                    workspace_checks.requirement_definitions(text, repository=BACKEND),
                    [visible],
                )

    def test_unclosed_list_container_fence_stops_at_sibling_item(self):
        text = (
            "- ~~~markdown\n"
            "  - `HID-022`: fenced requirement example\n"
            "- `CORE-725`: visible sibling requirement\n"
        )

        self.assertEqual(
            workspace_checks.requirement_definitions(text, repository=BACKEND),
            ["CORE-725"],
        )

    def test_continuation_line_list_fence_stops_at_sibling_item(self):
        cases = (
            ("-", "  ", "CORE-727"),
            ("1.", "   ", "CORE-728"),
        )
        for marker, indentation, visible in cases:
            with self.subTest(marker=marker):
                text = (
                    f"{marker} Context before the fence\n"
                    f"{indentation}~~~markdown\n"
                    f"{indentation}- `HID-023`: fenced requirement example\n"
                    f"{marker} Sibling item\n"
                    f"{indentation}- `{visible}`: visible requirement\n"
                )

                self.assertEqual(
                    workspace_checks.requirement_definitions(text, repository=BACKEND),
                    [visible],
                )

    def test_bare_list_marker_provides_continuation_fence_context(self):
        cases = (
            ("-", "  ", "CORE-729"),
            ("1.", "   ", "CORE-730"),
        )
        for marker, indentation, visible in cases:
            with self.subTest(marker=marker):
                text = (
                    f"{marker}\n"
                    f"{indentation}```text\n"
                    f"{indentation}- `HID-024`: fenced requirement example\n"
                    f"{marker} Sibling item\n"
                    f"{indentation}- `{visible}`: visible requirement\n"
                )

                self.assertEqual(
                    workspace_checks.requirement_definitions(text, repository=BACKEND),
                    [visible],
                )

    def test_nested_continuation_fence_keeps_comment_marker_literal(self):
        text = (
            "- Parent item\n"
            "  - Child item\n"
            "    ~~~markdown\n"
            "    <!-- literal comment opener\n"
            "    - `HID-025`: fenced requirement example\n"
            "    ~~~\n"
            "- `CORE-731`: visible requirement\n"
        )

        self.assertEqual(
            workspace_checks.requirement_definitions(text, repository=BACKEND),
            ["CORE-731"],
        )

    def test_repository_specific_requirement_id_grammars(self):
        backend = (
            "- `CORE-001`: core\n"
            "- `CORE-A07`: acceptance\n"
            "- `REL-054-01`: reliability\n"
            "- `AGENT-A10`: agent\n"
        )
        frontend = (
            "- `FX-001`: functional\n"
            "- `FQ-999`: quality\n"
            "- `CORE-001`: foreign namespace\n"
            "- `FX-A01`: malformed frontend ID\n"
            "- `FX-001-01`: malformed frontend ID\n"
        )

        self.assertEqual(
            workspace_checks.requirement_definitions(backend, repository=BACKEND),
            ["CORE-001", "CORE-A07", "REL-054-01", "AGENT-A10"],
        )
        self.assertEqual(
            workspace_checks.requirement_definitions(frontend, repository=FRONTEND),
            ["FX-001", "FQ-999"],
        )

    def test_rejects_malformed_requirement_definition_shapes(self):
        cases = {
            "empty bullet": "- `FX-777`:\n",
            "missing colon": "- `FX-777` Definition\n",
            "single column": ("| Requirement |\n| --- |\n| FX-777 |\n"),
            "empty header": (
                "| Requirement | |\n| --- | --- |\n| FX-777 | Definition |\n"
            ),
            "unpaired header tick": (
                "| `Requirement | Definition |\n"
                "| --- | --- |\n"
                "| FX-777 | Definition |\n"
            ),
            "unpaired cell tick": (
                "| Requirement | Definition |\n"
                "| --- | --- |\n"
                "| `FX-777 | Definition |\n"
            ),
            "double cell ticks": (
                "| Requirement | Definition |\n"
                "| --- | --- |\n"
                "| ``FX-777`` | Definition |\n"
            ),
            "empty definition": (
                "| Requirement | Definition |\n| --- | --- |\n| FX-777 | |\n"
            ),
            "even escaped pipe": (
                "| Requirement | Definition |\n"
                "| --- | --- |\n"
                r"| FX-777 | split \\| extra |"
                "\n"
            ),
        }
        for repository in (FRONTEND, BACKEND):
            for name, text in cases.items():
                with self.subTest(repository=repository, name=name):
                    self.assertEqual(
                        workspace_checks.requirement_definitions(
                            text, repository=repository
                        ),
                        [],
                    )

    def test_accepts_paired_ticks_and_odd_escaped_pipe(self):
        text = (
            "| `Requirement` | Definition |\n"
            "| --- | --- |\n"
            r"| `FX-777` | escaped \| pipe |"
            "\n"
        )

        self.assertEqual(
            workspace_checks.requirement_definitions(text, repository=FRONTEND),
            ["FX-777"],
        )


class WorkspaceInspectionTest(unittest.TestCase):
    def test_accepts_matching_gitlinks_and_child_heads(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            state = fixture.checker().inspect_workspace()

            self.assertEqual(state.revisions[BACKEND], fixture.backend_revision)
            self.assertEqual(state.revisions[FRONTEND], fixture.frontend_revision)

    def test_rejects_child_head_ahead_of_gitlink(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            commit_files(fixture.backend, {"generated.txt": "new\n"})

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "submodule HEAD mismatch"
            ):
                fixture.checker().inspect_workspace()

    def test_rejects_missing_child_checkout(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.frontend.rename(fixture.root / "frontend-missing")

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "missing or uninitialized"
            ):
                fixture.checker().inspect_workspace()


class ExternalReferenceValidationTest(unittest.TestCase):
    def test_resolves_one_formal_document_at_reachable_commit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            checker = fixture.checker()
            state = checker.inspect_workspace()

            self.assertEqual(checker.validate_external_upstream(state), 1)

    def test_resolves_requirement_defined_once_by_approved_spec(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(
                Path(temporary_directory), frontend_document=document("IMP-frontend")
            )
            fixture.frontend_revision = commit_files(
                fixture.frontend,
                {
                    "docs/knowledge/implementation/IMP-frontend.md": document(
                        "IMP-frontend",
                        f"{BACKEND}@{fixture.backend_revision}:CORE-001",
                    )
                },
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 1
            )

    def test_resolves_valid_frontend_bullet_and_table_requirements(self):
        cases = {
            "bullet": ("FX-777", "- `FX-777`: Defined behavior.\n"),
            "table": (
                "FQ-777",
                "| `Requirement` | Definition |\n"
                "| --- | --- |\n"
                "| `FQ-777` | Defined quality. |\n",
            ),
        }
        for name, (requirement, body) in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(Path(temporary_directory))
                    fixture.reference_frontend_requirement(
                        "---\nid: SPEC-frontend\nstatus: approved\n---\n\n" + body,
                        requirement,
                    )
                    checker = fixture.checker()

                    self.assertEqual(
                        checker.validate_external_upstream(checker.inspect_workspace()),
                        2,
                    )

    def test_rejects_frontend_pseudo_requirement_definitions(self):
        cases = {
            "empty bullet": "- `FX-777`:\n",
            "unpaired header": (
                "| `Requirement | Definition |\n"
                "| --- | --- |\n"
                "| FX-777 | Defined behavior. |\n"
            ),
            "unpaired cell": (
                "| Requirement | Definition |\n"
                "| --- | --- |\n"
                "| `FX-777 | Defined behavior. |\n"
            ),
            "empty definition": (
                "| Requirement | Definition |\n| --- | --- |\n| FX-777 | |\n"
            ),
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(Path(temporary_directory))
                    fixture.reference_frontend_requirement(
                        "---\nid: SPEC-frontend\nstatus: approved\n---\n\n" + body,
                        "FX-777",
                    )
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError,
                        "approved-SPEC requirement",
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_resolves_requirement_defined_in_approved_spec_table(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(
                Path(temporary_directory), frontend_document=document("IMP-frontend")
            )
            fixture.backend_revision = commit_files(
                fixture.backend,
                {
                    "docs/knowledge/spec/SPEC-backend.md": (
                        "---\n"
                        "id: SPEC-backend\n"
                        "status: approved\n"
                        "---\n\n"
                        "| Requirement | Meaning |\n"
                        "| --- | --- |\n"
                        "| `REL-054-01` | fixture requirement |\n"
                    )
                },
            )
            fixture.frontend_revision = commit_files(
                fixture.frontend,
                {
                    "docs/knowledge/implementation/IMP-frontend.md": document(
                        "IMP-frontend",
                        f"{BACKEND}@{fixture.backend_revision}:REL-054-01",
                    )
                },
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 1
            )

    def test_rejects_missing_and_duplicate_approved_spec_requirements(self):
        cases = {
            "missing": spec_document("SPEC-backend", ("CORE-002",)),
            "duplicate": spec_document("SPEC-backend", ("CORE-777", "CORE-777")),
            "draft": spec_document("SPEC-backend", ("CORE-777",), status="draft"),
            "empty bullet": (
                "---\nid: SPEC-backend\nstatus: approved\n---\n\n- `CORE-777`:\n"
            ),
            "empty table definition": (
                "---\n"
                "id: SPEC-backend\n"
                "status: approved\n"
                "---\n\n"
                "| Requirement | Definition |\n"
                "| --- | --- |\n"
                "| CORE-777 | |\n"
            ),
        }
        for name, specification in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(
                        Path(temporary_directory),
                        frontend_document=document("IMP-frontend"),
                    )
                    fixture.backend_revision = commit_files(
                        fixture.backend,
                        {"docs/knowledge/spec/SPEC-backend.md": specification},
                    )
                    fixture.frontend_revision = commit_files(
                        fixture.frontend,
                        {
                            "docs/knowledge/implementation/IMP-frontend.md": document(
                                "IMP-frontend",
                                f"{BACKEND}@{fixture.backend_revision}:CORE-777",
                            )
                        },
                    )
                    fixture.pin_submodules()
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError,
                        "approved-SPEC requirement",
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_revision_blob_preserves_malformed_frontmatter_prefix(self):
        valid = (
            "---\n"
            "id: SPEC-malformed\n"
            "status: approved\n"
            "---\n\n"
            "- `CORE-777`: must not become visible after git show\n"
        )
        cases = {
            "leading blank": "\n" + valid,
            "indented delimiter": "  " + valid,
        }
        for name, specification in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(
                        Path(temporary_directory),
                        frontend_document=document("IMP-frontend"),
                    )
                    fixture.backend_revision = commit_files(
                        fixture.backend,
                        {"docs/knowledge/spec/SPEC-malformed.md": specification},
                    )
                    fixture.frontend_revision = commit_files(
                        fixture.frontend,
                        {
                            "docs/knowledge/implementation/IMP-frontend.md": document(
                                "IMP-frontend",
                                f"{BACKEND}@{fixture.backend_revision}:CORE-777",
                            )
                        },
                    )
                    fixture.pin_submodules()
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError,
                        "approved-SPEC requirement",
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_ignores_requirements_hidden_in_comments_and_fenced_examples(self):
        hidden_bodies = (
            "<!--\n- `HID-001`: hidden requirement\n-->\n",
            "<!--\n- `HID-001`: unclosed hidden requirement\n",
            "```markdown\n- `HID-001`: example requirement\n```\n",
            ("```markdown\n```not-a-closing-fence\n- `HID-001`: still hidden\n"),
        )
        for body in hidden_bodies:
            with self.subTest(body=body):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(
                        Path(temporary_directory),
                        frontend_document=document("IMP-frontend"),
                    )
                    fixture.backend_revision = commit_files(
                        fixture.backend,
                        {
                            "docs/knowledge/spec/SPEC-backend.md": (
                                "---\n"
                                "id: SPEC-backend\n"
                                "status: approved\n"
                                "---\n\n" + body
                            )
                        },
                    )
                    fixture.frontend_revision = commit_files(
                        fixture.frontend,
                        {
                            "docs/knowledge/implementation/IMP-frontend.md": document(
                                "IMP-frontend",
                                f"{BACKEND}@{fixture.backend_revision}:HID-001",
                            )
                        },
                    )
                    fixture.pin_submodules()
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError,
                        "approved-SPEC requirement",
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_fenced_comment_literal_does_not_hide_visible_requirement(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(
                Path(temporary_directory), frontend_document=document("IMP-frontend")
            )
            fixture.backend_revision = commit_files(
                fixture.backend,
                {
                    "docs/knowledge/spec/SPEC-backend.md": (
                        "---\n"
                        "id: SPEC-backend\n"
                        "status: approved\n"
                        "---\n\n"
                        "```markdown\n"
                        "<!-- literal comment opener in an example\n"
                        "```\n"
                        "- `CORE-777`: visible requirement\n"
                        "<!-- -->\n"
                    )
                },
            )
            fixture.frontend_revision = commit_files(
                fixture.frontend,
                {
                    "docs/knowledge/implementation/IMP-frontend.md": document(
                        "IMP-frontend",
                        f"{BACKEND}@{fixture.backend_revision}:CORE-777",
                    )
                },
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 1
            )

    def test_inline_code_comment_literal_does_not_hide_visible_requirement(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(
                Path(temporary_directory), frontend_document=document("IMP-frontend")
            )
            fixture.backend_revision = commit_files(
                fixture.backend,
                {
                    "docs/knowledge/spec/SPEC-backend.md": (
                        "---\n"
                        "id: SPEC-backend\n"
                        "status: approved\n"
                        "---\n\n"
                        "The literal `<!--` starts an HTML comment.\n"
                        "- `CORE-777`: visible requirement\n"
                    )
                },
            )
            fixture.frontend_revision = commit_files(
                fixture.frontend,
                {
                    "docs/knowledge/implementation/IMP-frontend.md": document(
                        "IMP-frontend",
                        f"{BACKEND}@{fixture.backend_revision}:CORE-777",
                    )
                },
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 1
            )

    def test_rejects_non_definition_requirement_shapes(self):
        bodies = {
            "frontmatter": (
                "---\n"
                "id: SPEC-backend\n"
                "status: approved\n"
                "examples:\n"
                "  - `CORE-777`: metadata only\n"
                "---\n"
            ),
            "orphan table row": (
                "---\n"
                "id: SPEC-backend\n"
                "status: approved\n"
                "---\n\n"
                "| `CORE-777` | not in a table |\n"
            ),
            "unrecognized table header": (
                "---\n"
                "id: SPEC-backend\n"
                "status: approved\n"
                "---\n\n"
                "| Example | Meaning |\n"
                "| --- | --- |\n"
                "| `CORE-777` | illustrative only |\n"
            ),
            "indented code": (
                "---\n"
                "id: SPEC-backend\n"
                "status: approved\n"
                "---\n\n"
                "    - `CORE-777`: indented code example\n\n"
                "    | ID | Meaning |\n"
                "    | --- | --- |\n"
                "    | CORE-777 | indented table example |\n"
            ),
        }
        for name, specification in bodies.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(
                        Path(temporary_directory),
                        frontend_document=document("IMP-frontend"),
                    )
                    fixture.backend_revision = commit_files(
                        fixture.backend,
                        {"docs/knowledge/spec/SPEC-backend.md": specification},
                    )
                    fixture.frontend_revision = commit_files(
                        fixture.frontend,
                        {
                            "docs/knowledge/implementation/IMP-frontend.md": document(
                                "IMP-frontend",
                                f"{BACKEND}@{fixture.backend_revision}:CORE-777",
                            )
                        },
                    )
                    fixture.pin_submodules()
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError,
                        "approved-SPEC requirement",
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_missing_target_id(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(
                Path(temporary_directory), frontend_document=document("IMP-frontend")
            )
            fixture.frontend_revision = commit_files(
                fixture.frontend,
                {
                    "docs/knowledge/implementation/IMP-frontend.md": document(
                        "IMP-frontend",
                        f"{BACKEND}@{fixture.backend_revision}:SPEC-missing",
                    )
                },
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            with self.assertRaisesRegex(
                workspace_checks.CheckError,
                "exactly one formal document",
            ):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_noncanonical_formal_document_targets(self):
        cases = (
            ("docs/knowledge/intent/README.md", "INT-readme"),
            ("docs/knowledge/intent/nested/INT-nested.md", "INT-nested"),
            (
                "docs/knowledge/implementation/evidence/EVD-legacy.md",
                "EVD-legacy",
            ),
            (
                "docs/knowledge/implementation/x/evidence/EVD-nested.md",
                "EVD-nested",
            ),
        )
        for path, target_id in cases:
            with self.subTest(path=path):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(
                        Path(temporary_directory),
                        frontend_document=document("IMP-frontend"),
                    )
                    fixture.backend_revision = commit_files(
                        fixture.backend, {path: document(target_id)}
                    )
                    fixture.frontend_revision = commit_files(
                        fixture.frontend,
                        {
                            "docs/knowledge/implementation/IMP-frontend.md": document(
                                "IMP-frontend",
                                f"{BACKEND}@{fixture.backend_revision}:{target_id}",
                            )
                        },
                    )
                    fixture.pin_submodules()
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError, "exactly one formal document"
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_ignores_legacy_evidence_as_an_external_reference_source(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.backend_revision = commit_files(
                fixture.backend,
                {
                    "docs/knowledge/implementation/evidence/EVD-legacy.md": (
                        "---\n"
                        "id: EVD-legacy\n"
                        "external_upstream:\n"
                        "  - not-a-formal-reference\n"
                        "---\n"
                    )
                },
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 1
            )

    def test_rejects_reference_not_reachable_from_gitlink(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            pinned_backend = fixture.backend_revision
            future = commit_files(
                fixture.backend,
                {"docs/knowledge/spec/SPEC-future.md": document("SPEC-future")},
                "future target",
            )
            git(fixture.backend, "checkout", "--quiet", "--detach", pinned_backend)
            fixture.frontend_revision = commit_files(
                fixture.frontend,
                {
                    "docs/knowledge/implementation/IMP-frontend.md": document(
                        "IMP-frontend", f"{BACKEND}@{future}:SPEC-future"
                    )
                },
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "not reachable"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_root_evidence_outside_owned_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            commit_files(
                fixture.root,
                {"EVD-wrong.md": document("EVD-wrong")},
                "misplaced evidence",
            )
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "must live under"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_quoted_non_ascii_newline_evidence_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            path = '证据"\nEVD-outside.md'
            commit_files(
                fixture.root,
                {
                    path: document(
                        "EVD-outside",
                        f"{BACKEND}@{fixture.backend_revision}:SPEC-backend",
                    )
                },
                "misplaced evidence with special path",
            )
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "must live under"):
                checker.validate_external_upstream(checker.inspect_workspace())


class RootEvidenceValidationTest(unittest.TestCase):
    def test_accepts_evidence_bound_to_current_root_and_child_commits(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.write_integration_evidence()
            checker = fixture.checker()

            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 3
            )

    def test_rejects_nested_evidence_schema_bypass(self):
        for directory in ("nested", "archive", "proposals", "templates"):
            with self.subTest(directory=directory):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(Path(temporary_directory))
                    nested = (
                        fixture.root
                        / "deploy/dev/e2e/evidence"
                        / directory
                        / "EVD-bypass.md"
                    )
                    nested.parent.mkdir(parents=True)
                    nested.write_text("---\nid: EVD-bypass\n---\n", encoding="utf-8")
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError, "must be direct children"
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_missing_required_field(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.write_integration_evidence(include_commands=False)
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "commands"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_malformed_controlled_yaml_frontmatter_line(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            evidence = fixture.write_integration_evidence()
            original = evidence.read_text(encoding="utf-8")
            evidence.write_text(
                original.replace(
                    "id: EVD-integration",
                    "id: EVD-integration\nextension_note: allowed",
                ),
                encoding="utf-8",
            )
            checker = fixture.checker()
            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 3
            )

            evidence.write_text(
                original.replace(
                    "id: EVD-integration",
                    "id: EVD-integration\nthis is not yaml",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(workspace_checks.CheckError, "controlled YAML"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_blank_command_item(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            evidence = fixture.write_integration_evidence()
            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace("  - just e2e", "  - ''"),
                encoding="utf-8",
            )
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "non-blank"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_duplicate_evidence_list_items(self):
        replacements = {
            "commands": ("  - just e2e", "  - just e2e\n  - just e2e"),
            "covers": ("  - CORE-001\nscope:", "  - CORE-001\n  - CORE-001\nscope:"),
            "scope": (
                "  - e2e\nexternal_upstream:",
                "  - e2e\n  - e2e\nexternal_upstream:",
            ),
        }
        for key, (old, new) in replacements.items():
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(Path(temporary_directory))
                    evidence = fixture.write_integration_evidence()
                    evidence.write_text(
                        evidence.read_text(encoding="utf-8").replace(old, new),
                        encoding="utf-8",
                    )
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError, "duplicate item"
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_missing_covers_and_invalid_scope(self):
        cases = (
            ({"include_covers": False}, "covers"),
            ({"scopes": ("local-ish",)}, "invalid evidence scope"),
        )
        for arguments, diagnostic in cases:
            with self.subTest(arguments=arguments):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(Path(temporary_directory))
                    fixture.write_integration_evidence(**arguments)
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError, diagnostic
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_cover_without_exact_external_requirement_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            evidence = fixture.write_integration_evidence()
            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace(
                    "  - CORE-001\nscope:", "  - FAKE-999\nscope:"
                ),
                encoding="utf-8",
            )
            checker = fixture.checker()

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "exact external_upstream target"
            ):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_noncanonical_evidence_date(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            evidence = fixture.write_integration_evidence()
            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace(
                    "updated_at: 2026-09-06", "updated_at: 20260906"
                ),
                encoding="utf-8",
            )
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "YYYY-MM-DD"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_invalid_status(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.write_integration_evidence(status="verified")
            checker = fixture.checker()

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "status must be active or superseded"
            ):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_commits_that_do_not_match_observed_gitlinks(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.write_integration_evidence(backend_commit=fixture.frontend_revision)
            checker = fixture.checker()

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "must match the gitlinks"
            ):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_active_evidence_must_follow_current_gitlinks(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            evidence = fixture.write_integration_evidence()
            fixture.backend_revision = commit_files(
                fixture.backend, {"generated.txt": "new revision\n"}
            )
            fixture.pin_submodules()
            checker = fixture.checker()

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "active evidence must match"
            ):
                checker.validate_external_upstream(checker.inspect_workspace())

            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace(
                    "status: active", "status: superseded"
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 3
            )

    def test_superseded_evidence_cannot_reference_after_its_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            observed_root = git(fixture.root, "rev-parse", "HEAD")
            observed_backend = fixture.backend_revision
            evidence = fixture.write_integration_evidence(
                status="superseded",
                observed_commit=observed_root,
                backend_commit=observed_backend,
            )
            relative_evidence = evidence.relative_to(fixture.root).as_posix()
            commit_files(
                fixture.root,
                {relative_evidence: evidence.read_text(encoding="utf-8")},
                "record superseded evidence",
            )

            fixture.backend_revision = commit_files(
                fixture.backend,
                {
                    "docs/knowledge/spec/SPEC-new.md": spec_document(
                        "SPEC-new", ("NEW-999",)
                    )
                },
                "add later requirement",
            )
            fixture.pin_submodules()
            evidence.write_text(
                evidence.read_text(encoding="utf-8")
                .replace("  - CORE-001\nscope:", "  - NEW-999\nscope:")
                .replace(
                    f"{BACKEND}@{observed_backend}:CORE-001",
                    f"{BACKEND}@{fixture.backend_revision}:NEW-999",
                ),
                encoding="utf-8",
            )
            checker = fixture.checker()

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "not reachable from pinned revision"
            ):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_active_evidence_allows_only_evidence_changes_after_observation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            evidence = fixture.write_integration_evidence()
            relative_evidence = evidence.relative_to(fixture.root).as_posix()
            commit_files(
                fixture.root,
                {relative_evidence: evidence.read_text(encoding="utf-8")},
                "record evidence",
            )
            checker = fixture.checker()

            self.assertEqual(
                checker.validate_external_upstream(checker.inspect_workspace()), 3
            )

            commit_files(
                fixture.root,
                {"deploy/dev/e2e/test_changed.py": "changed = True\n"},
                "change integration asset",
            )
            with self.assertRaisesRegex(workspace_checks.CheckError, "is stale"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_active_evidence_rejects_reverted_non_evidence_commit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            evidence = fixture.write_integration_evidence()
            relative_evidence = evidence.relative_to(fixture.root).as_posix()
            commit_files(
                fixture.root,
                {relative_evidence: evidence.read_text(encoding="utf-8")},
                "record evidence",
            )
            commit_files(
                fixture.root,
                {"README.md": "temporary non-evidence change\n"},
                "change root asset",
            )
            commit_files(
                fixture.root,
                {"README.md": "fixture\n"},
                "revert root asset",
            )
            checker = fixture.checker()

            self.assertEqual(git(fixture.root, "diff", "HEAD~2", "HEAD"), "")
            with self.assertRaisesRegex(workspace_checks.CheckError, "is stale"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_active_evidence_rejects_dirty_root_assets(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.write_integration_evidence()
            (fixture.root / "justfile").write_text("dirty\n", encoding="utf-8")
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "changes outside"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_active_passed_evidence_rejects_only_temporary_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            fixture.write_integration_evidence(artifacts=("/tmp/report.json",))
            checker = fixture.checker()

            with self.assertRaisesRegex(workspace_checks.CheckError, "only /tmp"):
                checker.validate_external_upstream(checker.inspect_workspace())

    def test_rejects_blank_and_duplicate_artifacts(self):
        cases = {
            "blank": ("",),
            "duplicate": ("report.json", "report.json"),
        }
        for name, artifacts in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    fixture = WorkspaceFixture(Path(temporary_directory))
                    fixture.write_integration_evidence(artifacts=artifacts)
                    checker = fixture.checker()

                    with self.assertRaisesRegex(
                        workspace_checks.CheckError, "artifacts"
                    ):
                        checker.validate_external_upstream(checker.inspect_workspace())


class ChildGateTest(unittest.TestCase):
    def test_knowledge_check_invokes_both_public_child_gates(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            calls: list[tuple[Path, tuple[str, ...], str]] = []

            def record(repository, command, *, label, env):
                calls.append((repository, tuple(command), label))
                self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

            with mock.patch.object(
                workspace_checks, "run_read_only_command", side_effect=record
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    fixture.checker().knowledge_check()

            self.assertEqual(
                [(command, label) for _, command, label in calls],
                [
                    (("make", "engineering-lint"), "backend knowledge gate"),
                    (("make", "knowledge-check"), "frontend knowledge gate"),
                ],
            )

    def test_read_only_wrapper_detects_repository_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repo"
            initialize_repository(repository, {"tracked.txt": "before\n"})
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError, "modified the checked-out repository"
                ):
                    workspace_checks.run_read_only_command(
                        repository,
                        ["sh", "-c", "printf after > tracked.txt"],
                        label="fixture gate",
                        env=os.environ,
                    )

    def test_read_only_wrapper_rejects_dirty_repository_before_command(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repo"
            initialize_repository(repository, {"tracked.txt": "committed\n"})
            tracked = repository / "tracked.txt"
            tracked.write_text("user change\n", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError,
                    "requires a clean checked-out repository",
                ):
                    workspace_checks.run_read_only_command(
                        repository,
                        ["sh", "-c", "printf overwritten > tracked.txt"],
                        label="fixture gate",
                        env=os.environ,
                    )

            self.assertEqual(tracked.read_text(encoding="utf-8"), "user change\n")

    def test_read_only_wrapper_detects_clean_head_change(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repo"
            original_head = initialize_repository(
                repository, {"tracked.txt": "committed\n"}
            )

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError, "modified the checked-out repository"
                ):
                    workspace_checks.run_read_only_command(
                        repository,
                        ["git", "commit", "--quiet", "--allow-empty", "-m", "mutation"],
                        label="fixture gate",
                        env=os.environ,
                    )

            self.assertNotEqual(git(repository, "rev-parse", "HEAD"), original_head)

    def test_read_only_wrapper_preserves_missing_command_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repo"
            initialize_repository(repository, {"tracked.txt": "before\n"})
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError, "missing command"
                ):
                    workspace_checks.run_read_only_command(
                        repository,
                        ["definitely-not-a-workspace-command"],
                        label="fixture gate",
                        env=os.environ,
                    )

    def test_read_only_wrapper_reports_child_gate_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repo"
            initialize_repository(repository, {"tracked.txt": "before\n"})
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError, r"fixture gate failed \(7\)"
                ):
                    workspace_checks.run_read_only_command(
                        repository,
                        ["sh", "-c", "exit 7"],
                        label="fixture gate",
                        env=os.environ,
                    )


class StackRoutingTest(unittest.TestCase):
    def test_stack_functions_route_through_root_checker(self):
        root = Path(__file__).resolve().parents[2]
        stack = root / "deploy/dev/stack.sh"
        script = f"""
ROOT={shlex.quote(str(root))}
BACKEND=/fixture/backend
FRONTEND=/fixture/frontend
source {shlex.quote(str(stack))}
python3() {{
  printf 'python3'
  printf '|%s' "$@"
  printf '\\n'
}}
knowledge_check
contract_check
"""
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "python3|-m|unittest|-v|deploy.dev.test_workspace_checks",
                f"python3|{root}/deploy/dev/workspace_checks.py|knowledge|"
                f"--root|{root}|--backend|/fixture/backend|"
                "--frontend|/fixture/frontend",
                f"python3|{root}/deploy/dev/workspace_checks.py|contract|"
                f"--root|{root}|--backend|/fixture/backend|"
                "--frontend|/fixture/frontend",
            ],
        )


class ContractCheckTest(unittest.TestCase):
    def _fake_generation_python(self, parent: Path) -> Path:
        executable = parent / "fake-python3"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def test_backend_generation_uses_temp_clone_and_cleans_it(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            fixture = WorkspaceFixture(parent)
            scratch = parent / "scratch"
            scratch.mkdir()
            environment = {
                **os.environ,
                "BACKEND_GENERATE_PYTHON": str(self._fake_generation_python(parent)),
            }
            checker = fixture.checker(environment=environment, temporary_parent=scratch)

            with contextlib.redirect_stdout(io.StringIO()):
                checker.check_backend_generation(fixture.backend_revision)

            self.assertEqual(list(scratch.iterdir()), [])
            self.assertEqual(git(fixture.backend, "status", "--porcelain"), "")

    def test_backend_generation_reports_drift_and_cleans_temp_clone(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            fixture = WorkspaceFixture(parent)
            fixture.backend_revision = commit_files(
                fixture.backend,
                {"Makefile": ("generate:\n\t@printf 'drift\\n' > generated.txt\n")},
                "drifting generator",
            )
            fixture.pin_submodules()
            scratch = parent / "scratch"
            scratch.mkdir()
            checker = fixture.checker(
                environment={
                    **os.environ,
                    "BACKEND_GENERATE_PYTHON": str(
                        self._fake_generation_python(parent)
                    ),
                },
                temporary_parent=scratch,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError, "generated-code drift"
                ):
                    checker.check_backend_generation(fixture.backend_revision)

            self.assertEqual(list(scratch.iterdir()), [])
            self.assertEqual(git(fixture.backend, "status", "--porcelain"), "")

    def test_missing_generation_python_has_actionable_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            checker = fixture.checker(
                environment={
                    "PATH": os.environ.get("PATH", ""),
                    "BACKEND_GENERATE_PYTHON": "/does/not/exist/python3",
                }
            )

            with self.assertRaisesRegex(
                workspace_checks.CheckError, "BACKEND_GENERATE_PYTHON"
            ):
                checker.check_backend_generation(fixture.backend_revision)

    def test_missing_generation_dependency_names_backend_requirements_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            checker = fixture.checker(environment={"PATH": "/does/not/exist"})

            with mock.patch.object(
                checker, "_python_supports_grpc_tools", return_value=False
            ):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError,
                    rf"{BACKEND}/scripts/requirements-generate\.txt",
                ):
                    checker._generation_python()

    def test_generation_python_preserves_virtualenv_symlink(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            fixture = WorkspaceFixture(parent)
            executable = self._fake_generation_python(parent)
            virtualenv_python = parent / "venv" / "bin" / "python3"
            virtualenv_python.parent.mkdir(parents=True)
            virtualenv_python.symlink_to(executable)
            checker = fixture.checker(
                environment={
                    **os.environ,
                    "BACKEND_GENERATE_PYTHON": str(virtualenv_python),
                }
            )

            with mock.patch.object(
                checker, "_python_supports_grpc_tools", return_value=True
            ) as supports_grpc_tools:
                selected = checker._generation_python()

            self.assertEqual(selected, virtualenv_python.absolute())
            supports_grpc_tools.assert_called_once_with(virtualenv_python.absolute())

    def test_generation_python_preserves_virtualenv_symlink_from_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            fixture = WorkspaceFixture(parent)
            executable = self._fake_generation_python(parent)
            virtualenv_bin = parent / "venv" / "bin"
            virtualenv_bin.mkdir(parents=True)
            virtualenv_python = virtualenv_bin / "python3"
            virtualenv_python.symlink_to(executable)
            checker = fixture.checker(
                environment={
                    "PATH": os.pathsep.join(
                        (str(virtualenv_bin), os.environ.get("PATH", os.defpath))
                    )
                }
            )

            with mock.patch.object(
                checker, "_python_supports_grpc_tools", return_value=True
            ) as supports_grpc_tools:
                selected = checker._generation_python()

            self.assertEqual(selected, virtualenv_python.absolute())
            supports_grpc_tools.assert_called_once_with(virtualenv_python.absolute())

    def test_frontend_sdk_uses_explicit_api_and_compares_both_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = WorkspaceFixture(Path(temporary_directory))
            calls = []
            mutable_api = fixture.backend / "app/gateway/gateway.api"
            mutable_api.write_text("dirty checkout API\n", encoding="utf-8")

            def record(repository, command, *, label, env):
                exported_api = Path(env["BACKEND_API"])
                calls.append(
                    (
                        repository,
                        command,
                        label,
                        env,
                        exported_api.read_bytes(),
                        exported_api,
                    )
                )

            with mock.patch.object(
                workspace_checks, "run_read_only_command", side_effect=record
            ):
                fixture.checker().check_frontend_sdk(fixture.backend_revision)

            self.assertEqual(
                calls[0][1],
                [
                    "make",
                    "sdk-check",
                    f"BACKEND_API={calls[0][5]}",
                ],
            )
            self.assertEqual(calls[0][3]["BACKEND_API"], str(calls[0][5]))
            self.assertEqual(calls[0][4], b"syntax = 'v1'\n")
            self.assertNotEqual(calls[0][5], mutable_api)
            self.assertFalse(calls[0][5].exists())

            (fixture.frontend / "lib/sdk/data/gateway.dart").write_text(
                "different\n", encoding="utf-8"
            )
            with mock.patch.object(workspace_checks, "run_read_only_command"):
                with self.assertRaisesRegex(
                    workspace_checks.CheckError, "SDK copies differ"
                ):
                    fixture.checker().check_frontend_sdk(fixture.backend_revision)


if __name__ == "__main__":
    unittest.main()
