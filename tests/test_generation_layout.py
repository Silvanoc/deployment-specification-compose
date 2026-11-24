import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        model.write_text(model.read_text().replace("classes:\n", "classes:\n  Novel:\n    title: Novel Display\n    rank: 25\n    annotations:\n      margo_profile_kind: characteristic\n      margo_profile_characteristic: Novel\n      margo_profile_example: profile/Novel-001.json\n    attributes:\n      key:\n        range: string\n        equals_string: example/novel\n      value:\n        range: string\n        description: A novel value.\n", 1))
        fixture = self.root / "model/examples/profile/valid/Novel-001.json"
        fixture.write_text('{"value": "new"}\n')
        generate.generate(self.root)
        page = (self.root / "build/site/specification" / generate.PAGES["profile"]).read_text()
        self.assertIn("### Novel Display\n\nKey: `example/novel`", page)
        self.assertLess(page.index("### CPUs"), page.index("### Novel Display"))
        self.assertLess(page.index("### Novel Display"), page.index("### Memory"))
        self.assertIn("| value | string | N | A novel value. |", page)
        self.assertIn(fixture.read_text(), page)

    def test_model_and_source_changes_appear(self):
        model = self.root / "model/compose-profile.linkml.yaml"
        text = model.read_text().replace("Defines how many cores the CPU has available.", "A changed core description.")
        text = text.replace("title: CPUs\n    rank: 20", "title: Processor\n    rank: 45")
        text = text.replace("equals_string: margo.org/resource/cpu", "equals_string: example/processor")
        text = text.replace("Indicates the peripherals available on the workload target.", "Changed peripheral description.")
        model.write_text(text)
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

    def test_standard_jinja_and_missing_context(self):
        profile = (self.root / "content/profile.md").read_text()
        registry = (self.root / "content/registry.md").read_text()
        self.assertTrue(list(Environment().parse(profile).find_all(nodes.FromImport)))
        self.assertTrue(list(Environment().parse(registry).find_all(nodes.Include)))
        source = self.root / "content/application.md"
        original = source.read_text()
        schema = self.root / "model/compose-application.linkml.yaml"
        for extra in ("{{ missing_context }}", "{% include 'model/../README.md' %}",
                      "{% include 'model/missing.jinja2' %}",
                      "{{ schemaview.induced_slot('wait', 'ComponentProperties').description }}"):
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
                classes = [cls for cls in view.all_classes().values() if "margo_profile_kind" in cls.annotations]
                refs = [cls.annotations["margo_profile_example"].value for cls in sorted(classes, key=lambda cls: cls.rank)] + refs
            self.assertEqual(len(blocks), len(refs))
            for block, ref in zip(blocks, refs):
                group, filename = ref.split("/")
                fixture = (self.root / "model/examples" / group / "valid" / filename).read_text()
                parse = json.loads if filename.endswith(".json") else yaml.safe_load
                self.assertEqual(parse(block), parse(fixture))


if __name__ == "__main__":
    unittest.main()
