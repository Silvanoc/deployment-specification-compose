import json
from decimal import Decimal
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_examples
import common
import generate
import jsonschema
from jinja2 import Environment, nodes
from linkml_runtime.dumpers import yaml_dumper
import yaml
from schema_validation import precise_validator


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ("model", "specification"):
            shutil.copytree(common.ROOT / directory, self.root / directory)

    def test_fixture_discovery(self):
        with patch.object(check_examples, "validate") as validator:
            check_examples.check(self.root)
        calls = validator.call_args_list
        self.assertTrue(any(c.args[1] == "ApplicationDescription" and not c.kwargs["negative"] for c in calls))
        self.assertTrue(any(c.args[1] == "ComposeComponent" and c.kwargs["negative"] for c in calls))
        self.assertTrue(any(c.args[1] == "ComposeProfile" and not c.kwargs["negative"] for c in calls))
        registry_calls = [c for c in calls if c.args[1] == "ComposeComponentAnnotations"]
        self.assertEqual(len(registry_calls), 4)
        self.assertEqual(sum(not c.kwargs['negative'] for c in registry_calls), 1)

    def test_registry_driver_large_integer_and_parse_failures(self):
        fixture = self.root / 'model/examples/registry/invalid/ComposeComponentAnnotations-999.json'
        valid = (self.root / 'model/examples/registry/valid/ComposeComponentAnnotations-001.json').read_text()
        fixture.write_text(valid.rstrip()[:-1] + ', "vendor.extra": ' + '9' * 4301 + '}')
        native = check_examples.make_registry_validator(self.root / 'model/compose-registry.linkml.yaml')
        with patch.object(check_examples, 'make_registry_validator', return_value=native), patch.object(
                native, 'validate', wraps=native.validate) as calls:
            check_examples.check(self.root)
        self.assertEqual(calls.call_count, 5)
        fixture.write_text('{"a": 1, "a": 2}')
        with self.assertRaises(ValueError):
            check_examples.check(self.root)
        fixture.unlink()
        (fixture.parent / 'badname.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Invalid fixture path'):
            check_examples.check(self.root)

    def test_registry_driver_target_order(self):
        schema = self.root / 'model/order-registry.yaml'
        schema.write_text('''id: https://example.org/order-registry
name: order_registry
prefixes:
  linkml: https://w3id.org/linkml/
  ex: https://example.org/
imports:
  - linkml:types
default_prefix: ex
classes:
  Open:
    extra_slots:
      allowed: true
    attributes:
      name:
        range: string
  Closed:
    extra_slots:
      allowed: false
    attributes:
      name:
        range: string
''')
        positive_open = self.root / 'Open-positive.json'
        positive_closed = self.root / 'Closed-positive.json'
        negative_open = self.root / 'Open-negative.json'
        negative_closed = self.root / 'Closed-negative.json'
        positive_open.write_text('{"name":"n","extra":"value"}')
        positive_closed.write_text('{"name":"n"}')
        negative_open.write_text('{"name":3}')
        negative_closed.write_text('{"name":"n","extra":"value"}')
        factory = check_examples.make_registry_validator
        for order in (('Open', 'Closed'), ('Closed', 'Open')):
            validators = []
            def make(path):
                validator = factory(path)
                validator.validate = unittest.mock.Mock(wraps=validator.validate)
                validators.append(validator)
                return validator
            adapter = check_examples._RegistryFixtureValidator(schema, check_examples.SchemaView(str(schema)))
            with patch.object(check_examples, 'make_registry_validator', side_effect=make):
                for target in order:
                    positive = positive_open if target == 'Open' else positive_closed
                    negative = negative_open if target == 'Open' else negative_closed
                    check_examples.validate(schema, target, positive, engine=adapter)
                    check_examples.validate(schema, target, negative, negative=True, engine=adapter)
            self.assertEqual(len(validators), 2)
            self.assertEqual([v.validate.call_count for v in validators], [2, 2])

    def test_registry_fixture_context_note(self):
        schema = self.root / 'model/compose-registry.linkml.yaml'
        adapter = check_examples._RegistryFixtureValidator(schema, check_examples.SchemaView(str(schema)))
        data = common.parse_data((self.root / 'model/examples/registry/valid/ComposeComponentAnnotations-001.json').read_text(), '.json')
        errors, notes = adapter.validate(schema, 'ComposeComponentAnnotations', data)
        self.assertFalse(errors)
        self.assertEqual(notes, ['External OCI comparison not performed for: tag, revision'])
        del data['org.margo.component.version']
        _, notes = adapter.validate(schema, 'ComposeComponentAnnotations', data)
        self.assertEqual(notes, [])

    def test_registry_negative_plugin_exception_propagates(self):
        schema = self.root / 'model/compose-registry.linkml.yaml'
        fixture = self.root / 'model/examples/registry/invalid/ComposeComponentAnnotations-001.json'
        native = check_examples.make_registry_validator(schema)
        adapter = check_examples._RegistryFixtureValidator(schema, check_examples.SchemaView(str(schema)))
        with patch.object(check_examples, 'make_registry_validator', return_value=native), patch.object(
                native, 'validate', side_effect=RuntimeError('registry plugin failed')):
            with self.assertRaisesRegex(RuntimeError, 'registry plugin failed'):
                check_examples.validate(schema, 'ComposeComponentAnnotations', fixture, negative=True, engine=adapter)

    def test_profile_fixtures_validated_once(self):
        native = check_examples.ProfileValidator(self.root / 'model/compose-profile.linkml.yaml')
        fixtures = [p for kind in ('valid', 'invalid') for p in (self.root / 'model/examples/profile' / kind).iterdir()]
        with patch.object(check_examples, 'ProfileValidator', return_value=native), patch.object(
                native, 'validate', wraps=native.validate) as calls, patch.object(
                check_examples, 'validate', wraps=check_examples.validate) as checked:
            check_examples.check(self.root)
        seen = [c.args[2] for c in checked.call_args_list if c.args[2].parent.parent.name == 'profile']
        self.assertEqual(Counter(seen), Counter(fixtures))
        self.assertEqual(calls.call_count, len(fixtures))

    def test_profile_examples_do_not_require_example_annotations(self):
        schema = self.root / 'model/compose-profile.linkml.yaml'
        view = check_examples.SchemaView(str(schema))
        view.all_classes()
        shared = yaml.safe_load(yaml_dumper.dumps(view.schema_map['margo-common:workload-target-characteristics.linkml']))
        local = yaml.safe_load(schema.read_text())
        def strip(value):
            if isinstance(value, dict):
                for key in list(value):
                    if key == 'margo_profile_example':
                        value.pop(key)
                    else:
                        strip(value[key])
            elif isinstance(value, list):
                for child in value:
                    strip(child)
        self.assertIn('Cpu', shared['classes'])
        self.assertIn('MemoryProperties', shared['classes'])
        self.assertIn('ComposeDeploymentCharacteristic', local['classes'])
        strip(shared)
        strip(local)
        shared_path = self.root / 'model/workload-target-characteristics.linkml.yaml'
        shared_path.write_text(yaml.safe_dump(shared, sort_keys=False))
        local['imports'] = ['linkml:types', str(shared_path.with_suffix(''))]
        schema.write_text(yaml.safe_dump(local, sort_keys=False))
        fixtures = [p for kind in ('valid', 'invalid') for p in (self.root / 'model/examples/profile' / kind).iterdir()]
        for source_already_absent in (False, True):
            with self.subTest(source_already_absent=source_already_absent):
                if source_already_absent:
                    # Repeat the removal on already annotation-free sources.
                    for path in (schema, shared_path):
                        source = yaml.safe_load(path.read_text())
                        strip(source)
                        path.write_text(yaml.safe_dump(source, sort_keys=False))
                isolated = check_examples.SchemaView(str(schema))
                self.assertFalse(any('margo_profile_example' in c.annotations for c in isolated.all_classes().values()))
                native = check_examples.ProfileValidator(schema)
                with patch.object(check_examples, 'ProfileValidator', return_value=native), patch.object(
                        native, 'validate', wraps=native.validate) as calls, patch.object(
                        check_examples, 'validate', wraps=check_examples.validate) as checked:
                    check_examples.check(self.root)
                seen = [c.args[2] for c in checked.call_args_list if c.args[2].parent.parent.name == 'profile']
                self.assertEqual(Counter(seen), Counter(fixtures))
                self.assertEqual(calls.call_count, len(fixtures))

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

    def test_registry_wrapper_shape_and_drift(self):
        doc = self.root / "specification/applications/application-registry.md"
        original = doc.read_text()
        yaml_fence = common.markdown_fences(original, {"yaml", "yml"})[0]
        component = {"name": "myapp", "properties": {}}
        for value in (None, [], {}, {"components": "bad"}, {"components": []},
                      {"components": [component, component]}, {"components": [component], "other": 1}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Registry fragment"):
                check_examples.registry_component(value)
        for replacement in ("components: [", "components: not-a-list", "other: value\n" + yaml_fence,
                            "other: value", "components: []", "components:\n  - {}\n  - {}"):
            with self.subTest(replacement=replacement):
                doc.write_text(original.replace(yaml_fence, replacement))
                with patch.object(check_examples, "validate"), self.assertRaises((ValueError, yaml.YAMLError)):
                    check_examples.check(self.root)
        doc.write_text(original.replace("name: myapp", "name: changed"))
        with patch.object(check_examples, "validate"), self.assertRaisesRegex(ValueError, "drift.*ComposeComponent"):
            check_examples.check(self.root)


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
        profile.write_text(profile.read_text().replace("title: Compose Deployment Technology Support", "title: Changed Deployment Support"))
        with self.assertRaisesRegex(ValueError, "differs"):
            generate.generate(self.root, check=True)
        output = self.root / "build/site/specification/margo-management-interface/compose-profile-characteristics.md"
        self.assertIn("Changed Deployment Support", output.read_text())

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
