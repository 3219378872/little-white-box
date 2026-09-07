from __future__ import annotations

import contextlib
import io
import json
import hashlib

import yaml
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


def fixture_manifest(repository, definitions, references=()):
    suffix = "backend" if repository == BACKEND else "frontend"
    documents = [
        dict(
            id=prefix + "-" + suffix,
            layer=layer,
            path=f"docs/knowledge/{layer}/{prefix}-{suffix}.md",
            status=status,
        )
        for prefix, layer, status in [
            ("INT", "intent", "approved"),
            ("SPEC", "spec", "approved"),
            ("IMP", "implementation", "active"),
        ]
    ]
    requirements = [
        dict(
            id=identity,
            spec_id="SPEC-" + suffix,
            path=f"docs/knowledge/spec/SPEC-{suffix}.md",
            definition=definition,
            text_sha256=hashlib.sha256(definition.encode()).hexdigest(),
        )
        for identity, definition in definitions.items()
    ]
    return dict(
        schema_version=1,
        repository=repository,
        revision="",
        documents=documents,
        requirements=requirements,
        external_upstream=list(references),
    )


EXPORTER = """import json, subprocess, sys
revision = subprocess.check_output(['git', 'rev-parse', '--verify', sys.argv[1] + '^{commit}'], text=True).strip()
manifest = json.loads(subprocess.check_output(['git', 'show', revision + ':knowledge-manifest.json']))
manifest['revision'] = revision
print(json.dumps(manifest))
"""


class WorkspaceFixture:
    def __init__(self, parent: Path):
        self.root = parent / "workspace"
        self.root.mkdir()
        self.backend = self.root / BACKEND
        self.frontend = self.root / FRONTEND
        export_recipe = (
            "\nknowledge-export:\n\t@"
            + shlex.quote(sys.executable)
            + ' export_fixture.py "$(REF)"\n'
        )
        self.backend_revision = initialize_repository(
            self.backend,
            {
                "knowledge-manifest.json": json.dumps(
                    fixture_manifest(BACKEND, {"CORE-001": "Community requirement"})
                ),
                "export_fixture.py": EXPORTER,
                "app/gateway/gateway.api": "syntax = 'v1'\n",
                "generated.txt": "current\n",
                "Makefile": "generate:\n\t@:\nengineering-lint:\n\t@:\n"
                + export_recipe,
            },
        )
        self.frontend_revision = initialize_repository(
            self.frontend,
            {
                "knowledge-manifest.json": json.dumps(
                    fixture_manifest(
                        FRONTEND,
                        {"FX-001": "Client requirement", "FX-002": "Other requirement"},
                        [
                            dict(
                                source_id="IMP-frontend",
                                repository=BACKEND,
                                revision=self.backend_revision,
                                target_id="INT-backend",
                            )
                        ],
                    )
                ),
                "export_fixture.py": EXPORTER,
                "vendor/sdk_source/api/gateway.dart": "api\n",
                "vendor/sdk_source/data/gateway.dart": "data\n",
                "lib/sdk/api/gateway.dart": "api\n",
                "lib/sdk/data/gateway.dart": "data\n",
                "Makefile": "sdk-check:\n\t@:\nknowledge-check:\n\t@:\n"
                + export_recipe,
            },
        )
        initialize_repository(self.root, {"README.md": "fixture\n"})
        self.pin_submodules()

    def pin_submodules(self):
        for name, revision in [
            (BACKEND, self.backend_revision),
            (FRONTEND, self.frontend_revision),
        ]:
            git(
                self.root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{revision},{name}",
            )
        git(self.root, "commit", "--quiet", "-m", "pin child repositories")
        return git(self.root, "rev-parse", "HEAD")

    def checker(self, **kwargs):
        return workspace_checks.WorkspaceChecker(self.root, **kwargs)

    def update_manifest(self, repository, change):
        path = self.root / repository
        manifest = json.loads((path / "knowledge-manifest.json").read_text())
        change(manifest)
        revision = commit_files(path, {"knowledge-manifest.json": json.dumps(manifest)})
        if repository == BACKEND:
            self.backend_revision = revision
        else:
            self.frontend_revision = revision
        self.pin_submodules()
        return revision

    def write_integration_evidence(self, **changes):
        meta = dict(
            id="EVD-integration",
            status="active",
            result="passed",
            updated_at="2026-09-07",
            observed_commit=git(self.root, "rev-parse", "HEAD"),
            commands=["just knowledge-check"],
            scope=["static"],
            coverage=[
                dict(
                    requirements=[BACKEND + ":CORE-001"],
                    paths=[BACKEND + "/generated.txt"],
                ),
                dict(
                    requirements=[FRONTEND + ":FX-001"], paths=[FRONTEND + "/lib/sdk"]
                ),
            ],
        )
        meta.update(changes)
        path = self.root / "deploy/dev/e2e/evidence/EVD-integration.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            + yaml.safe_dump(meta, sort_keys=False)
            + "---\n\n# Integration evidence\n"
        )
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


class ManifestValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = WorkspaceFixture(Path(self.temp.name))

    def export(self, revision=None):
        checker = self.fixture.checker()
        return checker._manifest_at_revision(
            checker.repositories[BACKEND], revision or self.fixture.backend_revision
        )

    def validate(self):
        checker = self.fixture.checker()
        return checker.validate_external_upstream(checker.inspect_workspace())

    def test_resolves_formal_document_and_approved_requirement(self):
        self.assertEqual(self.validate(), 1)
        self.fixture.update_manifest(
            FRONTEND,
            lambda data: data["external_upstream"][0].update(target_id="CORE-001"),
        )
        self.assertEqual(self.validate(), 1)

    def test_historical_export_ignores_newer_manifest(self):
        original_revision = self.fixture.backend_revision
        before = self.export(original_revision)
        self.fixture.update_manifest(BACKEND, lambda data: data["documents"].pop())
        self.assertEqual(self.export(original_revision), before)
        self.assertNotEqual(self.export()["documents"], before["documents"])

    def test_root_uses_child_export_without_reading_child_markdown(self):
        self.fixture.backend_revision = commit_files(
            self.fixture.backend,
            {"docs/knowledge/spec/SPEC-bad.md": "deliberately not parsed by root\n"},
        )
        self.fixture.pin_submodules()
        self.assertEqual(self.validate(), 1)

    def test_rejects_schema_types_documents_requirements_and_references(self):
        cases = [
            lambda data: data.update(schema_version=True),
            lambda data: data.update(schema_version=2),
            lambda data: data.update(repository=FRONTEND),
            lambda data: data.update(documents={}),
            lambda data: data["documents"].append(data["documents"][0]),
            lambda data: data["documents"][0].update(
                path="docs/knowledge/intent/nested/INT-backend.md"
            ),
            lambda data: data["documents"][0].update(status="imaginary"),
            lambda data: data["documents"][1].update(status="draft"),
            lambda data: data["requirements"].append(data["requirements"][0]),
            lambda data: data["requirements"][0].update(spec_id=[]),
            lambda data: data["requirements"][0].update(path="../escape"),
            lambda data: data["requirements"][0].update(definition="tampered"),
            lambda data: data["requirements"][0].update(text_sha256="x"),
            lambda data: data["external_upstream"].append(
                dict(
                    source_id=[],
                    repository=FRONTEND,
                    revision="a" * 40,
                    target_id="FX-001",
                )
            ),
            lambda data: data["external_upstream"].append(
                dict(
                    source_id="IMP-backend",
                    repository=FRONTEND,
                    revision="a" * 40,
                    target_id="CORE-001",
                )
            ),
        ]
        original = json.loads(
            (self.fixture.backend / "knowledge-manifest.json").read_text()
        )
        for change in cases:
            with self.subTest(change=cases.index(change)):
                self.fixture.update_manifest(
                    BACKEND,
                    lambda data: (
                        data.clear(),
                        data.update(json.loads(json.dumps(original))),
                        change(data),
                    ),
                )
                with self.assertRaises(workspace_checks.CheckError):
                    self.export()

    def test_rejects_missing_target_and_wrong_repository_grammar(self):
        for target in ["SPEC-missing", "FX-999"]:
            with self.subTest(target=target):
                self.fixture.update_manifest(
                    FRONTEND,
                    lambda data: data["external_upstream"][0].update(target_id=target),
                )
                with self.assertRaisesRegex(
                    workspace_checks.CheckError, "found 0 matches"
                ):
                    self.validate()

    def test_rejects_unreachable_reference(self):
        tree = git(self.fixture.backend, "rev-parse", "HEAD^{tree}")
        unreachable = git(
            self.fixture.backend, "commit-tree", tree, "-m", "unreachable"
        )
        self.fixture.update_manifest(
            FRONTEND,
            lambda data: data["external_upstream"][0].update(revision=unreachable),
        )
        with self.assertRaisesRegex(workspace_checks.CheckError, "not reachable"):
            self.validate()

    def test_rejects_unavailable_reference_commit(self):
        self.fixture.update_manifest(
            FRONTEND,
            lambda data: data["external_upstream"][0].update(revision="f" * 40),
        )
        with self.assertRaisesRegex(workspace_checks.CheckError, "unavailable"):
            self.validate()

    def test_rejects_invalid_json_duplicate_keys_and_wrong_revision(self):
        for output in [
            "not json",
            '{"schema_version": 1, "schema_version": 1}',
            json.dumps(dict(schema_version=1, repository=BACKEND, revision="f" * 40)),
        ]:
            with self.subTest(output=output):
                commit_files(
                    self.fixture.backend,
                    {"export_fixture.py": "print(" + repr(output) + ")\n"},
                )
                with self.assertRaises(workspace_checks.CheckError):
                    self.export()

    def test_rejects_export_mutation_and_dirty_checkout(self):
        commit_files(
            self.fixture.backend,
            {
                "export_fixture.py": "from pathlib import Path\nPath('generated.txt').write_text('mutated')\nprint('{}')\n"
            },
        )
        with self.assertRaisesRegex(workspace_checks.CheckError, "modified"):
            self.export()
        with self.assertRaisesRegex(workspace_checks.CheckError, "clean repository"):
            self.export()

    def test_export_failure_is_actionable(self):
        commit_files(
            self.fixture.backend,
            {
                "export_fixture.py": 'import sys\nsys.exit("missing dependencies; run make knowledge-setup")\n'
            },
        )
        with self.assertRaisesRegex(
            workspace_checks.CheckError, "make knowledge-setup"
        ):
            self.export()

    def test_root_python_does_not_leak_into_child_tools(self):
        checker = self.fixture.checker(
            environment={
                "KNOWLEDGE_PYTHON": "/root/python",
                "BACKEND_KNOWLEDGE_PYTHON": "/backend/python",
            }
        )
        self.assertEqual(
            checker._child_environment(BACKEND)["KNOWLEDGE_PYTHON"], "/backend/python"
        )
        self.assertNotIn("KNOWLEDGE_PYTHON", checker._child_environment(FRONTEND))


class RootEvidenceValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = WorkspaceFixture(Path(self.temp.name))

    def validate(self):
        checker = self.fixture.checker()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            checker.validate_external_upstream(checker.inspect_workspace())
        return output.getvalue()

    def test_accepts_groups_bound_to_observed_gitlinks(self):
        self.fixture.write_integration_evidence()
        self.assertEqual(self.validate().count("passed at unchanged inputs"), 2)

    def test_rejects_derived_fields_invalid_lists_dates_and_paths(self):
        for changes in [
            dict(backend_commit=self.fixture.backend_revision),
            dict(frontend_commit=self.fixture.frontend_revision),
            dict(upstream=[]),
            dict(commands=[]),
            dict(commands=[" "]),
            dict(commands=["test", "test"]),
            dict(scope=["imaginary"]),
            dict(updated_at="2026-2-3"),
            dict(updated_at="2026-02-30"),
            dict(status="draft"),
            dict(result="aligned"),
            dict(observed_commit="HEAD"),
            dict(coverage=[]),
            dict(covers=["FX-001"]),
            dict(artifacts=["/tmp/result"]),
        ]:
            with self.subTest(changes=changes):
                self.fixture.write_integration_evidence(**changes)
                with self.assertRaises(workspace_checks.CheckError):
                    self.validate()

    def test_rejects_unknown_or_duplicate_requirements_and_nonexistent_inputs(self):
        for group in [
            dict(requirements=["FX-001"], paths=["README.md"]),
            dict(requirements=[FRONTEND + ":CORE-001"], paths=["README.md"]),
            dict(requirements=[FRONTEND + ":FX-999"], paths=["README.md"]),
            dict(requirements=[FRONTEND + ":FX-001"] * 2, paths=["README.md"]),
            dict(requirements=[FRONTEND + ":FX-001"], paths=["../escape"]),
            dict(requirements=[FRONTEND + ":FX-001"], paths=["missing"]),
            dict(requirements=[FRONTEND + ":FX-001"], paths=[]),
            dict(
                requirements=[FRONTEND + ":FX-001"],
                paths=["README.md"],
                extra="invalid",
            ),
        ]:
            with self.subTest(group=group):
                self.fixture.write_integration_evidence(coverage=[group])
                with self.assertRaises(workspace_checks.CheckError):
                    self.validate()

    def test_requires_both_children(self):
        self.fixture.write_integration_evidence(
            coverage=[dict(requirements=[FRONTEND + ":FX-001"], paths=[FRONTEND])]
        )
        with self.assertRaisesRegex(workspace_checks.CheckError, "both children"):
            self.validate()

    def test_partial_unknown_inputs_are_explicit_not_current_proof(self):
        self.fixture.write_integration_evidence(
            result="partial",
            coverage=[
                dict(
                    requirements=[FRONTEND + ":FX-001", BACKEND + ":CORE-001"], paths=[]
                )
            ],
        )
        self.assertIn("historical inputs unknown; not current proof", self.validate())

    def test_whole_child_path_is_valid_and_scoped(self):
        self.fixture.write_integration_evidence(
            coverage=[
                dict(
                    requirements=[FRONTEND + ":FX-001", BACKEND + ":CORE-001"],
                    paths=[FRONTEND, BACKEND],
                )
            ]
        )
        self.assertIn("passed at unchanged inputs", self.validate())

    def test_child_change_invalidates_only_consuming_group(self):
        self.fixture.write_integration_evidence()
        self.fixture.backend_revision = commit_files(
            self.fixture.backend, {"generated.txt": "new\n"}
        )
        self.fixture.pin_submodules()
        output = self.validate()
        self.assertIn("stale input " + BACKEND, output)
        self.assertEqual(output.count("passed at unchanged inputs"), 1)

    def test_shared_input_change_invalidates_every_consuming_group(self):
        self.fixture.write_integration_evidence(
            coverage=[
                dict(requirements=[repo + ":" + requirement], paths=["README.md"])
                for repo, requirement in [(BACKEND, "CORE-001"), (FRONTEND, "FX-001")]
            ]
        )
        (self.fixture.root / "README.md").write_text("changed\n")
        self.assertEqual(self.validate().count("stale input README.md"), 2)

    def test_requirement_change_invalidates_only_its_group(self):
        self.fixture.write_integration_evidence()

        def change(data):
            data["requirements"][0].update(
                definition="Changed", text_sha256=hashlib.sha256(b"Changed").hexdigest()
            )

        self.fixture.update_manifest(FRONTEND, change)
        output = self.validate()
        self.assertIn("stale requirement " + FRONTEND + ":FX-001", output)
        self.assertEqual(output.count("passed at unchanged inputs"), 1)

    def test_unrelated_document_change_preserves_all_groups(self):
        self.fixture.write_integration_evidence()
        self.fixture.frontend_revision = commit_files(
            self.fixture.frontend, {"NOTES.md": "unrelated\n"}
        )
        self.fixture.pin_submodules()
        self.assertEqual(self.validate().count("passed at unchanged inputs"), 2)

    def test_external_reference_cannot_exceed_observed_gitlinks(self):
        observed = git(self.fixture.root, "rev-parse", "HEAD")
        self.fixture.backend_revision = commit_files(
            self.fixture.backend, {"NOTES.md": "later\n"}
        )
        self.fixture.pin_submodules()
        self.fixture.write_integration_evidence(
            observed_commit=observed,
            external_upstream=[
                f"{BACKEND}@{self.fixture.backend_revision}:INT-backend"
            ],
        )
        with self.assertRaisesRegex(workspace_checks.CheckError, "not reachable"):
            self.validate()

    def test_rejects_root_evidence_outside_owned_directory_and_nested_pages(self):
        path = self.fixture.write_integration_evidence()
        text = path.read_text()
        for relative in [
            "other/EVD-integration.md",
            "deploy/dev/e2e/evidence/nested/EVD-integration.md",
        ]:
            with self.subTest(relative=relative):
                target = self.fixture.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
                with self.assertRaises(workspace_checks.CheckError):
                    self.validate()
                target.unlink()

    def test_rejects_duplicate_yaml_keys(self):
        path = self.fixture.write_integration_evidence()
        path.write_text(
            path.read_text().replace(
                "status: active", "status: active\nstatus: superseded"
            )
        )
        with self.assertRaisesRegex(workspace_checks.CheckError, "duplicate"):
            self.validate()

    def test_durable_artifacts_must_exist(self):
        self.fixture.write_integration_evidence(artifacts=["missing.txt"])
        with self.assertRaisesRegex(
            workspace_checks.CheckError, "missing durable artifact"
        ):
            self.validate()
        self.fixture.write_integration_evidence(artifacts=["README.md"])
        self.validate()


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
KNOWLEDGE_PYTHON=python3
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
