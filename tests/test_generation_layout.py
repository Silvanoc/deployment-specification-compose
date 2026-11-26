import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

import yaml
from jinja2 import Environment, TemplateError, nodes
from linkml_runtime.utils.schemaview import SchemaView

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import generate


class GenerationLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ("model", "specification", "content", "tools/templates"):
            shutil.copytree(ROOT / directory, self.root / directory)
        for name in ("README.md", "COMPATIBILITY-ISSUES.md"):
            shutil.copy2(ROOT / name, self.root / name)
        model = self.root / "model/compose-profile.linkml.yaml"
        view = SchemaView(str(model))
        source_id = next(i for i in view.imports_closure() if i.endswith('workload-target-characteristics.linkml'))
        source = view.schema_map[source_id].source_file
        if urlsplit(source).scheme in ('http', 'https'):
            with urlopen(source, timeout=30) as response:
                text = response.read().decode('utf-8')
        else:
            text = Path(source).read_text()
        text = re.sub(r'^ *margo_profile_(?:example|kind|characteristic):.*\n', '', text, flags=re.MULTILINE)
        shared = self.root / 'model/shared-characteristics.yaml'
        shared.write_text(text)
        model.write_text(model.read_text().replace(source_id, str(shared.with_suffix(''))))

    def test_exact_parity_and_no_examples_catalog(self):
        self.assertFalse(list(self.root.rglob("*-examples.json")))
        generate.generate(self.root, check=True)
        self.assertFalse((self.root / "build/site/data-model").exists())

    def test_published_page_is_linkml_index(self):
        original = generate.PageDocGenerator.serialize
        calls = []

        def marked_index(generator, directory=None):
            original(generator, directory=directory)
            calls.append(generator.page_name)
            if generator.page_name == "profile":
                index = Path(directory) / "index.md"
                index.write_bytes(index.read_bytes() + b"\nLINKML-INDEX-TEST\n")

        with patch.object(generate.PageDocGenerator, "serialize", marked_index):
            generate.generate(self.root)
        page = self.root / "build/site/specification" / generate.PAGES["profile"]
        self.assertIn(b"LINKML-INDEX-TEST", page.read_bytes())
        self.assertCountEqual(calls, generate.PAGES)

    def test_templates_render_model_not_prose(self):
        for path in (self.root / "tools/templates").rglob("*.jinja2"):
            text = path.read_text()
            tree = Environment().parse(text)
            self.assertFalse(list(tree.find_all((nodes.Include, nodes.Extends, nodes.Import, nodes.FromImport))))
            self.assertIn("schemaview.class_induced_slots(", text)
            self.assertIn("slot.description", text)
            if path.name == "class.md.jinja2":
                self.assertIn("schemaview.class_induced_slots(element.name)", text)
            for literal in tree.find_all(nodes.TemplateData):
                self.assertRegex(literal.data, r"^[\s|`-]*$")
        output = self.root / "rendered"
        subprocess.run(["linkml", "generate", "doc", "--preserve-names", "--template-directory",
                        str(self.root / "tools/templates/profile"), "--directory", str(output),
                        str(self.root / "model/compose-profile.linkml.yaml")], check=True)
        rendered = (output / "Cpu.md").read_text()
        self.assertIn("# Cpu\n\n| Field | Type | Required | Description |\n| --- |", rendered)
        self.assertIn("| cores | number | Y |", rendered)

    def test_new_class_and_example_need_no_code_change(self):
        model = self.root / "model/compose-profile.linkml.yaml"
        model.write_text(model.read_text().replace("classes:\n", "classes:\n  Novel:\n    title: Novel Display\n    rank: 25\n    attributes:\n      key:\n        range: string\n        equals_string: example/novel\n      value:\n        range: string\n        description: A novel value.\n", 1))
        data = yaml.safe_load(model.read_text())
        profile = data['classes']['ComposeProfile']
        profile['slot_usage']['characteristics']['any_of'].append({'range': 'Novel'})
        profile['all_of'].append({'slot_conditions': {'characteristics': {'has_member': {'range': 'Novel'}}}})
        model.write_text(yaml.safe_dump(data, sort_keys=False))
        fixture = self.root / "model/examples/profile/valid/Novel-other.json"
        fixture.write_text('{"key": "example/novel", "value": "new"}\n')
        generate.generate(self.root)
        page = (self.root / "build/site/specification" / generate.PAGES["profile"]).read_text()
        self.assertIn("### Novel Display\n\nKey: `example/novel`", page)
        self.assertLess(page.index("### CPUs"), page.index("### Novel Display"))
        self.assertLess(page.index("### Novel Display"), page.index("### Memory"))
        self.assertIn("| value | string | N | A novel value. |", page)
        self.assertIn(fixture.read_text(), page)
        view = SchemaView(str(model))
        self.assertIn('Novel', [x.range for x in view.induced_slot('characteristics', 'ComposeProfile').any_of])
        from profile_examples import ProfileValidator
        validator = ProfileValidator(model)
        full = json.loads((self.root / 'model/examples/profile/valid/ComposeProfile-001.json').read_text())
        self.assertTrue(validator.validate(model, 'ComposeProfile', full)[0])
        full['characteristics'].append(json.loads(fixture.read_text()))
        self.assertFalse(validator.validate(model, 'ComposeProfile', full)[0])

    def test_model_and_source_changes_appear(self):
        model = self.root / "model/compose-profile.linkml.yaml"
        view = SchemaView(str(model))
        source_id = next(i for i in view.imports_closure() if i.endswith('shared-characteristics'))
        source = view.schema_map[source_id].source_file
        if urlsplit(source).scheme in ('http', 'https'):
            with urlopen(source, timeout=30) as response:
                text = response.read().decode('utf-8')
        else:
            text = Path(source).read_text()
        text = text.replace("Defines how many cores the CPU has available, in decimal units", "A changed core description. In decimal units")
        text = text.replace("title: CPUs\n    rank: 20", "title: Processor\n    rank: 45")
        text = text.replace("equals_string: margo.org/resource/cpu", "equals_string: example/processor")
        text = text.replace("Indicates the peripherals available on the workload target.", "Changed peripheral description.")
        shared = self.root / 'model/shared-characteristics.yaml'
        shared.write_text(text)
        model.write_text(model.read_text().replace(source_id, str(shared.with_suffix(''))))
        source = self.root / "content/profile.md"
        source.write_text(source.read_text().replace("# Compose Profile Characteristics", "# Changed Profile Title"))
        generate.generate(self.root)
        page = (self.root / "build/site/specification" / generate.PAGES["profile"]).read_text()
        self.assertIn("A changed core description.", page)
        self.assertIn("# Changed Profile Title", page)
        self.assertIn("### Processor\n\nKey: `example/processor`", page)
        self.assertLess(page.index("### Storage"), page.index("### Processor"))
        self.assertIn("Changed peripheral description.", page)

    def test_fixture_change_appears(self):
        fixture = self.root / "model/examples/profile/valid/CpuCharacteristic-001.json"
        fixture.write_text(fixture.read_text().replace('"key":', '"key" :', 1))
        generate.generate(self.root)
        page = (self.root / "build/site/specification" / generate.PAGES["profile"]).read_text()
        self.assertIn(fixture.read_text(), page)

    def test_deployment_properties_range_drives_label_and_note(self):
        model = self.root / 'model/compose-profile.linkml.yaml'
        text = model.read_text().replace('range: ComposeDeploymentProperties', 'range: AlternateProperties', 1)
        text += '''\n  AlternateProperties:
    title: Alternate Label
    description: Alternate note from the selected properties class.
    extra_slots:
      allowed: true
'''
        model.write_text(text)
        generate.validate_profile_metadata(model)
        page = generate.render_page('profile', self.root, model)
        self.assertIn('### Alternate Label Properties Attributes', page)
        self.assertIn('> **Note:** Alternate note from the selected properties class.', page)
        self.assertIn('### Alternate Label Example', page)

    def test_deployment_properties_metadata_fails_closed(self):
        model = self.root / 'model/compose-profile.linkml.yaml'
        original = model.read_text()
        for before, after in (
            ('      properties:\n        range: ComposeDeploymentProperties', '      otherProperties:\n        range: ComposeDeploymentProperties'),
            ('range: ComposeDeploymentProperties', 'range: string'),
            ('range: ComposeDeploymentProperties', 'range: MissingProperties'),
            ('        range: ComposeDeploymentProperties\n', ''),
            ('        range: ComposeDeploymentProperties\n', '        any_of:\n          - range: ComposeDeploymentProperties\n'),
            ('range: ComposeDeploymentProperties', 'range: ComposeDeploymentProperties\n        multivalued: true'),
            ('title: Compose Deployment Technology\n', "title: '   '\n"),
            ('    title: Compose Deployment Technology\n', ''),
            ('description: The properties for the Compose deployment technology will be defined once the deployment technology specifications are separated from the core specification.', "description: ''"),
            ('    description: The properties for the Compose deployment technology will be defined once the deployment technology specifications are separated from the core specification.\n', ''),
        ):
            with self.subTest(after=after):
                self.assertIn(before, original)
                model.write_text(original.replace(before, after, 1))
                with self.assertRaisesRegex(ValueError, 'deployment properties'):
                    generate.validate_profile_metadata(model)

    def test_standard_jinja_and_missing_context(self):
        profile = (self.root / "content/profile.md").read_text()
        registry = (self.root / "content/registry.md").read_text()
        self.assertTrue(list(Environment().parse(profile).find_all(nodes.FromImport)))
        self.assertTrue(list(Environment().parse(registry).find_all(nodes.Include)))
        source = self.root / "content/application.md"
        original = source.read_text()
        schema = self.root / "model/compose-application.linkml.yaml"
        duplicate = "{{ schemaview.induced_slot('wait', 'ComposeComponentProperties').description }}"
        for extra in ("{{ missing_context }}", "{% include 'model/../README.md' %}",
                      "{% include 'model/missing.jinja2' %}",
                      "{{ schemaview.induced_slot('wait', 'MissingClass').description }}",
                      "{{ schemaview.induced_slot('missingSlot', 'ComposeComponentProperties').description }}",
                      duplicate + duplicate):
            source.write_text(original + "\n" + extra)
            with self.assertRaises((ValueError, TemplateError)):
                generate.render_page("application", self.root, schema)
        source.write_text(original + "\n{% include 'content/extra.md' %}\n")
        (self.root / "content/extra.md").write_text("Included text.")
        self.assertIn("Included text.", generate.render_page("application", self.root, schema))
        (self.root / "content/extra.md").unlink()
        (self.root / "content/extra.md").symlink_to(self.root / "README.md")
        with self.assertRaises(TemplateError):
            generate.render_page("application", self.root, schema)
        profile_path = self.root / "content/profile.md"
        schema = self.root / "model/compose-profile.linkml.yaml"
        for replacement in ("", "{{ profile_group('deployment') }}{{ profile_group('deployment') }}"):
            profile_path.write_text(profile.replace("{{ profile_group('deployment') }}", replacement))
            with self.assertRaises(ValueError):
                generate.render_page("profile", self.root, schema)

    def test_application_prose_without_model_references(self):
        content = (self.root / 'content/application.md').read_text()
        authored = (self.root / 'specification/applications/application-description.md').read_text()
        bullets = [line for line in authored.splitlines() if line.startswith(
            ('- The `component.properties.wait`', '- The `parameter.targets[].pointer`'))]
        self.assertEqual(len(bullets), 2)
        for line in bullets:
            self.assertIn(line, content.splitlines())
        self.assertNotIn('schemaview', content)
        schema = self.root / 'model/compose-application.linkml.yaml'
        self.assertEqual(generate.render_page('application', self.root, schema), authored)
        view = SchemaView(str(schema))
        self.assertIsNone(view.get_class('ComposeParameterTarget'))
        self.assertIsNotNone(view.get_class('ParameterTarget'))
        slot = view.induced_slot('wait', 'ComposeComponentProperties')
        self.assertEqual(slot.range, 'boolean')
        self.assertEqual(slot.annotations['margo_disallow_null'].value, 'true')

    def test_fixture_jinja_is_not_evaluated(self):
        fixture = self.root / "model/examples/application/valid/ApplicationDescription-001.yaml"
        fixture.write_text(fixture.read_text() + '# {{ missing_context }}\n')
        schema = self.root / "model/compose-application.linkml.yaml"
        result = generate.render_page("application", self.root, schema)
        self.assertIn('# {{ missing_context }}\n```', result)

    def test_example_references_fail_closed(self):
        source = self.root / "content/application.md"
        original = source.read_text()
        schema = self.root / "model/compose-application.linkml.yaml"
        ref = "application/ApplicationDescription-001.yaml"
        for bad in ("application/../bad.yaml", "unknown/ApplicationDescription-001.yaml", "application/Unknown-001.yaml", "application/ApplicationDescription-999.yaml"):
            source.write_text(original.replace(ref, bad))
            with self.assertRaises(ValueError):
                generate.render_page("application", self.root, schema)
        source.write_text(original + "\n~~~text\nunclosed\n")
        with self.assertRaises(ValueError):
            generate.render_page("application", self.root, schema)
        source.write_text(original + "\n{{ example('" + ref + "') }}\n")
        with self.assertRaises(ValueError):
            generate.render_page("application", self.root, schema)
        source.write_text(original)
        fixture = self.root / "model/examples/application/valid/ApplicationDescription-001.yaml"
        fixture.unlink()
        with self.assertRaises(ValueError):
            generate.render_page("application", self.root, schema)
        fixture.symlink_to(self.root / "README.md")
        with self.assertRaises(ValueError):
            generate.render_page("application", self.root, schema)

    def test_fixture_semantics_match_original_fences(self):
        for name, relative in generate.PAGES.items():
            doc = (self.root / "specification" / relative).read_text()
            blocks = re.findall(r"(?ms)^```(?:json|yaml)\n(.*?)^```", doc)
            refs = re.findall(r"example\('([^']+)'\)", (self.root / "content" / f"{name}.md").read_text())
            if name == "profile":
                view = SchemaView(str(self.root / "model/compose-profile.linkml.yaml"))
                sections = generate.profile_sections(view)
                runner = generate.ExampleRunner(input_directory=self.root / "model/examples/profile/valid", input_formats=["json"])
                refs = ["profile/" + Path(sorted(runner.example_source_inputs(section.target))[0]).name for section in sections] + refs
            self.assertEqual(len(blocks), len(refs))
            for block, ref in zip(blocks, refs):
                group, filename = ref.split("/")
                fixture = (self.root / "model/examples" / group / "valid" / filename).read_text()
                parse = json.loads if filename.endswith(".json") else yaml.safe_load
                expected = parse(fixture)
                if name == "registry" and filename == "ComposeComponent-001.yaml":
                    expected = {"components": [expected]}
                self.assertEqual(parse(block), expected)

    def test_profile_discovery_is_annotation_free_and_deterministic(self):
        model = self.root / 'model/compose-profile.linkml.yaml'
        view = SchemaView(str(model))
        self.assertFalse(any(key in cls.annotations for cls in view.all_classes().values()
                             for key in ('margo_profile_example', 'margo_profile_kind', 'margo_profile_characteristic')))
        directory = self.root / 'model/examples/profile/valid'
        original = directory / 'CpuCharacteristic-001.json'
        original.rename(directory / 'CpuCharacteristic-z.json')
        first = directory / 'CpuCharacteristic-a.json'
        other = (directory / 'CpuCharacteristic-z.json').read_text()
        first.write_text(other.replace('"key":', '"key" :', 1))
        actual = generate.ExampleRunner.example_source_inputs
        calls = []
        def spy(runner, class_name=None):
            calls.append((runner.input_directory, runner.input_formats, class_name))
            return actual(runner, class_name)
        with patch.object(generate.ExampleRunner, 'example_source_inputs', spy):
            page = generate.render_page('profile', self.root, model)
        self.assertIn(first.read_text(), page)
        self.assertNotIn(other, page)
        self.assertIn((directory, ['json'], 'CpuCharacteristic'), calls)

    def test_profile_discovery_with_glob_metacharacters_in_root(self):
        root = self.root / 'repo[1]'
        for directory in ('model', 'content', 'tools/templates'):
            shutil.copytree(self.root / directory, root / directory)
        model = root / 'model/compose-profile.linkml.yaml'
        model.write_text(model.read_text().replace(str(self.root / 'model/shared-characteristics'), 'shared-characteristics'))
        page = generate.render_page('profile', root, model)
        fixture = root / 'model/examples/profile/valid/CpuCharacteristic-001.json'
        self.assertIn(fixture.read_text(), page)

    def test_optional_and_subclass_fixtures_do_not_change_docs(self):
        import check_examples
        model = self.root / 'model/compose-profile.linkml.yaml'
        data = yaml.safe_load(model.read_text())
        data['classes']['DerivedCpu'] = {'is_a': 'CpuCharacteristic'}
        data['classes']['OptionalCharacteristic'] = {'title': 'Optional', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'required': True, 'equals_string': 'vendor/optional'}}}
        model.write_text(yaml.safe_dump(data, sort_keys=False))
        valid = self.root / 'model/examples/profile/valid'
        invalid = self.root / 'model/examples/profile/invalid'
        (valid / 'DerivedCpu-test.json').write_text((valid / 'CpuCharacteristic-001.json').read_text())
        (invalid / 'DerivedCpu-test.json').write_text('{}\n')
        (valid / 'OptionalCharacteristic-test.json').write_text('{"key":"vendor/optional"}\n')
        (invalid / 'OptionalCharacteristic-test.json').write_text('{}\n')
        page = generate.render_page('profile', self.root, model)
        self.assertEqual(page, (self.root / 'specification' / generate.PAGES['profile']).read_text())
        self.assertEqual(check_examples.profile_target({'key': 'margo.org/resource/cpu'}, str(model)), 'CpuCharacteristic')
        with self.assertRaisesRegex(ValueError, 'matches 0'):
            check_examples.profile_target({'key': 'vendor/optional'}, str(model))
        generate.validate_profile_metadata(str(model))
        check_examples.check(self.root)

    def test_required_pattern_is_rendered_accurately(self):
        model = self.root / 'model/compose-profile.linkml.yaml'
        data = yaml.safe_load(model.read_text())
        data['classes']['PatternCharacteristic'] = {'title': 'Pattern', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'required': True, 'pattern': 'vendor/pattern/.*'}}}
        profile = data['classes']['ComposeProfile']
        profile['slot_usage']['characteristics']['any_of'].append({'range': 'PatternCharacteristic'})
        profile['all_of'].append({'slot_conditions': {'characteristics': {'has_member': {'range': 'PatternCharacteristic'}}}})
        model.write_text(yaml.safe_dump(data, sort_keys=False))
        (self.root / 'model/examples/profile/valid/PatternCharacteristic-example.json').write_text('{"key":"vendor/pattern/test"}\n')
        page = generate.render_page('profile', self.root, model)
        self.assertIn('### Pattern\n\nKey pattern: `vendor/pattern/.*`', page)

    def test_profile_missing_invalid_only_and_symlink_fail(self):
        model = self.root / 'model/compose-profile.linkml.yaml'
        directory = self.root / 'model/examples/profile/valid'
        fixture = directory / 'CpuCharacteristic-001.json'
        fixture.unlink()
        invalid = self.root / 'model/examples/profile/invalid/CpuCharacteristic-only.json'
        invalid.write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'No valid profile JSON example for CpuCharacteristic'):
            generate.render_page('profile', self.root, model)
        fixture.symlink_to(self.root / 'README.md')
        with self.assertRaisesRegex(ValueError, 'unsafe example'):
            generate.render_page('profile', self.root, model)

    def test_profile_discovery_rejects_unsafe_directory_before_glob(self):
        model = self.root / 'model/compose-profile.linkml.yaml'
        directory = self.root / 'model/examples/profile/valid'
        moved = directory.with_name('saved-valid')
        directory.rename(moved)
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    directory.symlink_to(moved, target_is_directory=True)
                with patch.object(generate.ExampleRunner, 'example_source_inputs') as discovery:
                    with self.assertRaisesRegex(ValueError, 'unsafe profile example directory'):
                        generate.render_page('profile', self.root, model)
                    discovery.assert_not_called()
                if symlink:
                    directory.unlink()


if __name__ == "__main__":
    unittest.main()
