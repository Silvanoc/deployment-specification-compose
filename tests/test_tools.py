import json
from decimal import Decimal
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_examples
import common
import generate
import jsonschema
from jinja2 import Environment, nodes
import yaml
from schema_validation import precise_validator


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ("model", "specification"):
            shutil.copytree(common.ROOT / directory, self.root / directory)
        # Until the application schema lane lands its fixture renames, adapt
        # only this isolated test project to the intended fragment class.
        for kind in ("valid", "invalid"):
            folder = self.root / "model/examples/application" / kind
            if not any(folder.glob("ComponentListFragment-*")):
                for old in folder.glob("DeploymentProfile-*"):
                    old.rename(old.with_name(old.name.replace("DeploymentProfile-", "ComponentListFragment-", 1)))

    def test_fixture_discovery(self):
        with patch.object(check_examples, "validate") as validator:
            check_examples.check(self.root)
        calls = validator.call_args_list
        self.assertTrue(any(c.args[1] == "ApplicationDescription" and not c.kwargs["negative"] for c in calls))
        self.assertTrue(any(c.args[1] == "ComponentListFragment" and c.kwargs["negative"] for c in calls))
        self.assertTrue(any(c.args[1] == "ComposeProfile" and not c.kwargs["negative"] for c in calls))
        self.assertTrue(any(c.args[1] == "ComposeComponentAnnotations" and c.kwargs["negative"] for c in calls))

    def test_drift_and_missing_negative(self):
        doc = self.root / "specification/margo-management-interface/compose-profile-characteristics.md"
        original = doc.read_text()
        doc.write_text(original.replace('"available": 64', '"available": 65'))
        with patch.object(check_examples, "validate"), self.assertRaisesRegex(ValueError, "drift"):
            check_examples.check(self.root)
        doc.write_text(original)
        (self.root / "model/examples/profile/invalid/MemoryCharacteristic-001.json").unlink()
        with patch.object(check_examples, "validate"), self.assertRaisesRegex(ValueError, "Both positive and negative"):
            check_examples.check(self.root)

    def test_negative_runtime_error_is_not_validation(self):
        engine = unittest.mock.Mock()
        engine.validate.side_effect = RuntimeError("schema crashed")
        fixture = self.root / "model/examples/application/invalid/ApplicationDescription-001.yaml"
        with self.assertRaisesRegex(RuntimeError, "schema crashed"):
            check_examples.validate("schema", "Item", fixture, negative=True, engine=engine)

    def test_equal_fixture_for_wrong_class_is_rejected(self):
        for kind in ("valid", "invalid"):
            folder = self.root / "model/examples/profile" / kind
            original = folder / "MemoryCharacteristic-001.json"
            original.rename(folder / "StorageCharacteristic-999.json")
        with patch.object(check_examples, "validate"), self.assertRaisesRegex(ValueError, "drift.*MemoryCharacteristic"):
            check_examples.check(self.root)

    def test_semantic_json_comparison(self):
        self.assertTrue(common.semantic_equal({'a': 1, 'b': [2, {'x': 3.0}]}, {'b': [2.0, {'x': 3}], 'a': 1.0}))
        for left, right in ((True, 1), (False, 0), ({'x': None}, {}), ({'x': []}, {}), ({'x': {}}, {}), ([1, 2], [2, 1]), ('1', 1), (None, False)):
            with self.subTest(left=left, right=right):
                self.assertFalse(common.semantic_equal(left, right))

    def test_templates_contain_only_formatting_literals(self):
        templates = sorted((common.ROOT / "tools/templates").rglob("*.jinja2"))
        self.assertTrue(templates, "No Jinja templates found")
        environment = Environment()
        for path in templates:
            tree = environment.parse(path.read_text(encoding="utf-8"))
            for literal in tree.find_all(nodes.TemplateData):
                with self.subTest(template=path, line=literal.lineno):
                    self.assertIsNotNone(re.fullmatch(r"[\s|`-]*", literal.data), f"Authored content in {path}:{literal.lineno}")

    def test_document_numeric_normalization_matches_fixture(self):
        doc = self.root / "specification/margo-management-interface/compose-profile-characteristics.md"
        doc.write_text(doc.read_text().replace('"available": 64', '"available": 64.0'))
        with patch.object(check_examples, "validate"):
            check_examples.check(self.root)

    def test_unknown_profile_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "matches 0"):
            check_examples.profile_target({"key": "not-a-characteristic"}, self.root / "model/compose-profile.linkml.yaml")

    def test_malformed_and_absent_fixture(self):
        with self.assertRaises(ValueError):
            common.markdown_example("```yaml\nname: hello\n", ".yaml", 0)
        shutil.rmtree(self.root / "model/examples/application/valid")
        with self.assertRaisesRegex(ValueError, "fixture directory"):
            check_examples.check(self.root)

    def test_oras_annotations(self):
        text = '```bash\noras push repo \\\n --annotation "key=one two" --annotation other=value\n```\n'
        self.assertEqual(common.oras_annotations(text, 0), {"key": "one two", "other": "value"})
        for bad in ('--annotation key', '--annotation key=x --annotation key=y', '--annotation', '--annotation-bad key=x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                common.oras_annotations(f'```bash\noras push repo {bad}\n```\n', 0)

    def test_oras_empty_annotation_value(self):
        text = '```bash\noras push repo --annotation optional=\n```\n'
        self.assertEqual(common.oras_annotations(text, 0), {"optional": ""})
        with self.assertRaises(ValueError):
            common.oras_annotations('```bash\noras push repo --annotation =value\n```\n', 0)

    def test_oras_drift(self):
        doc = self.root / "specification/applications/application-registry.md"
        doc.write_text(doc.read_text().replace('org.opencontainers.image.title=myapp', 'org.opencontainers.image.title=different'))
        with patch.object(check_examples, "validate"), self.assertRaisesRegex(ValueError, "drift"):
            check_examples.check(self.root)

    def test_documented_registry_context(self):
        document = (self.root / "specification/applications/application-registry.md").read_text()
        fence = common.markdown_fences(document, {"bash"})[0]
        self.assertEqual(check_examples.registry_context(fence, document), ("1.0.0", "1.0.0"))
        reference = "registry.example.com/org/myapp"
        for bad_fence, bad_doc, message in (
            (fence.replace(reference + ":1.0.0", reference), document, "repository and tag"),
            (fence, document.replace('      revision: "1.0.0"', ''), "repository and revision"),
            (fence, document.replace("oci://" + reference, "oci://registry.example.com/other/myapp"), "repository mismatch"),
            (fence, document.replace("oci://" + reference, "oci://"), "repository and revision"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                check_examples.registry_context(bad_fence, bad_doc)
        hostport = reference.replace("registry.example.com", "registry.example.com:5000")
        self.assertEqual(check_examples.registry_context(fence.replace(reference, hostport), document.replace("oci://" + reference, "oci://" + hostport)), ("1.0.0", "1.0.0"))
        with self.assertRaisesRegex(ValueError, "repository and tag"):
            check_examples.registry_context(fence.replace(reference + ":1.0.0", hostport), document.replace("oci://" + reference, "oci://" + hostport))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("model", "specification", "content"):
            shutil.copytree(common.ROOT / name, self.root / name)
        shutil.copytree(common.ROOT / "tools/templates", self.root / "tools/templates")
        shutil.copy2(common.ROOT / "README.md", self.root / "README.md")
        shutil.copy2(common.ROOT / "COMPATIBILITY-ISSUES.md", self.root / "COMPATIBILITY-ISSUES.md")

    def test_report_symlink_rejected(self):
        report = self.root / "COMPATIBILITY-ISSUES.md"
        report.unlink()
        report.symlink_to(self.root / "README.md")
        with self.assertRaisesRegex(ValueError, "COMPATIBILITY-ISSUES"):
            generate.generate(self.root)

    def test_generation_fidelity_roots_and_model_change(self):
        generate.generate(self.root, check=True)
        self.assertEqual((self.root / "build/site/COMPATIBILITY-ISSUES.md").read_bytes(), (self.root / "COMPATIBILITY-ISSUES.md").read_bytes())
        schemas = self.root / "build/artifacts/json-schemas"
        for name, fixture in (("application", "ApplicationDescription-001.yaml"), ("profile", "ComposeProfile-001.json"), ("registry", "ComposeComponentAnnotations-001.json")):
            text = (schemas / f"{name}.schema.json").read_text()
            schema = json.loads(text)
            validator = jsonschema.validators.validator_for(schema)(schema)
            precise = precise_validator(json.loads(text, parse_float=Decimal))
            for kind, valid in (("valid", True), ("invalid", False)):
                path = self.root / "model/examples" / name / kind / fixture
                fixture_text = path.read_text()
                data = common.parse_data(fixture_text, path.suffix)
                self.assertEqual(precise.is_valid(data), valid, f"{name} {kind} precise")
                # Stock JSON Schema checks interoperability for these small
                # fixtures, not the custom x-margo semantic constraints.
                standard = json.loads(fixture_text) if path.suffix == ".json" else yaml.safe_load(fixture_text)
                self.assertEqual(validator.is_valid(standard), valid, f"{name} {kind} standard")
        profile = self.root / "model/compose-profile.linkml.yaml"
        profile.write_text(profile.read_text().replace("Defines how many cores the CPU has available.", "Changed core description."))
        with self.assertRaisesRegex(ValueError, "differs"):
            generate.generate(self.root, check=True)
        output = self.root / "build/site/specification/margo-management-interface/compose-profile-characteristics.md"
        self.assertIn("Changed core description.", output.read_text())

    def test_fragment_change_changes_output(self):
        fragment = self.root / "content/profile.md"
        text = fragment.read_text()
        self.assertIn("# Compose Profile Characteristics", text)
        fragment.write_text(text.replace("# Compose Profile Characteristics", "# Changed Profile Title"))
        with self.assertRaisesRegex(ValueError, "differs"):
            generate.generate(self.root, check=True)
        page = self.root / "build/site/specification/margo-management-interface/compose-profile-characteristics.md"
        self.assertIn("# Changed Profile Title", page.read_text())

    def test_cleanup_and_symlink_safety(self):
        generate.generate(self.root)
        unrelated = self.root / "build/artifacts/unrelated.txt"
        unrelated.write_text("keep")
        stale = self.root / "build/site/specification/stale.md"
        stale.write_text("stale")
        (self.root / "build/artifacts/json-schemas/stale.json").write_text("stale")
        generate.generate(self.root, check=True)
        self.assertFalse(stale.exists())
        self.assertFalse((self.root / "build/artifacts/json-schemas/stale.json").exists())
        self.assertEqual(unrelated.read_text(), "keep")
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep")
        (self.root / "build/site/specification/link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            generate.generate(self.root)
        self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
