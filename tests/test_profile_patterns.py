import json
import re
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
import yaml

from jsonschema import Draft202012Validator
from linkml_runtime.utils.schemaview import SchemaView


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check_examples
from common import parse_data
from profile_structure import profile_sections
SCHEMA = ROOT / "model/compose-profile.linkml.yaml"
KNOWN = {
    "ComposeDeploymentCharacteristic": "margo.org/deployment/compose",
    "CpuCharacteristic": "margo.org/resource/cpu",
    "MemoryCharacteristic": "margo.org/resource/memory",
    "StorageCharacteristic": "margo.org/resource/storage",
    "PeripheralsCharacteristic": "margo.org/peripherals",
    "InterfacesCharacteristic": "margo.org/interfaces",
}


class ProfilePatternsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = SchemaView(str(SCHEMA))
        result = subprocess.run(
            ["linkml", "generate", "json-schema", str(SCHEMA)],
            check=True, capture_output=True, text=True, cwd=ROOT,
        )
        cls.definitions = json.loads(result.stdout)["$defs"]

    def key_validator(self, name):
        return Draft202012Validator(self.definitions[name]["properties"]["key"])

    def test_known_keys_use_exact_const_without_pattern(self):
        for cls, key in KNOWN.items():
            with self.subTest(cls=cls):
                validator = self.key_validator(cls)
                self.assertTrue(validator.is_valid(key))
                slot = self.view.induced_slot("key", cls)
                self.assertEqual(slot.equals_string, key)
                self.assertIsNone(slot.pattern)
                self.assertEqual(self.definitions[cls]["properties"]["key"]["const"], key)
                self.assertNotIn("pattern", self.definitions[cls]["properties"]["key"])
                for suffix in ("\n", "\r\n", "/other"):
                    self.assertFalse(validator.is_valid(key + suffix))

    def test_profile_target_uses_exact_equality(self):
        for cls, key in KNOWN.items():
            self.assertEqual(check_examples.profile_target({'key': key}, SCHEMA), cls)
            for suffix in ('\n', '\r\n', '/other'):
                with self.assertRaisesRegex(ValueError, 'matches 0'):
                    check_examples.profile_target({'key': key + suffix}, SCHEMA)

    def test_profile_target_pattern_only_both_and_ambiguous_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / 'model', root / 'model')
            schema = root / 'model/compose-profile.linkml.yaml'
            model = yaml.safe_load(schema.read_text())
            classes = model['classes']
            classes['One'] = {'title': 'One', 'rank': 70, 'attributes': {'key': {'range': 'string', 'pattern': 'foo.*'}}}
            classes['Two'] = {'title': 'Two', 'rank': 80, 'attributes': {'key': {'range': 'string', 'equals_string': 'bar', 'pattern': 'bar'}}}
            classes['ComposeProfile']['slot_usage']['characteristics']['any_of'].extend([{'range': 'One'}, {'range': 'Two'}])
            classes['ComposeProfile']['all_of'].extend([{'slot_conditions': {'characteristics': {'has_member': {'range': name}}}} for name in ('One', 'Two')])
            def save():
                schema.write_text(yaml.safe_dump(model, sort_keys=False))
            save()
            self.assertEqual(check_examples.profile_target({'key': 'foo42'}, schema), 'One')
            self.assertEqual(check_examples.profile_target({'key': 'bar'}, schema), 'Two')
            with self.assertRaisesRegex(ValueError, 'matches 0'):
                check_examples.profile_target({'key': 'bar\n'}, schema)
            classes['Two']['attributes']['key']['pattern'] = 'no-match'
            save()
            with self.assertRaisesRegex(ValueError, 'matches 0'):
                check_examples.profile_target({'key': 'bar'}, schema)
            classes['Two']['attributes']['key'] = {'range': 'string', 'equals_string': 'foo42'}
            save()
            with self.assertRaisesRegex(ValueError, 'matches 2'):
                check_examples.profile_target({'key': 'foo42'}, schema)
            classes['One']['attributes']['key'] = {'range': 'string'}
            classes['Two']['attributes']['key'] = {'range': 'string'}
            save()
            with self.assertRaisesRegex(ValueError, 'Missing key constraint'):
                profile_sections(SchemaView(str(schema)))

    def test_unknown_key_excludes_only_exact_known_keys(self):
        validator = self.key_validator("ComposeOtherCharacteristic")
        for key in KNOWN.values():
            self.assertFalse(validator.is_valid(key), key)
            for suffix in ("\n", "\r\n", "/other"):
                self.assertTrue(validator.is_valid(key + suffix), repr(key + suffix))
        for key in ("", "\n", "\r\n", "custom", "custom\nkey"):
            self.assertTrue(validator.is_valid(key), repr(key))

    def test_extension_metadata_pattern_has_absolute_end(self):
        pattern = self.view.get_class("WorkloadTargetProfile").annotations["margo_extensions_pattern"].value
        for value in ("x-a-extensions", "x-vendor-name-extensions"):
            self.assertIsNotNone(re.search(pattern, value))
            for suffix in ("\n", "\r\n"):
                self.assertIsNone(re.search(pattern, value + suffix))

    def test_optional_typed_slots_disallow_null_metadata(self):
        for cls, name, range_ in (
            ("Cpu", "architecture", "CpuArchitecture"),
            ("Peripheral", "manufacturer", "string"),
            ("Peripheral", "model", "string"),
        ):
            with self.subTest(cls=cls, name=name):
                slot = self.view.induced_slot(name, cls)
                self.assertEqual(slot.annotations["margo_disallow_null"].value, "true")
                self.assertFalse(slot.required)
                self.assertEqual(slot.range, range_)

    def test_profile_presentation_metadata(self):
        self.assert_profile_presentation_metadata(self.view)

    def assert_profile_presentation_metadata(self, view):
        expected = [
            ("ComposeDeploymentCharacteristic", "Compose Deployment Technology Support", 10, "deployment", "ComposeDeploymentCharacteristic", "The following required characteristics indicate a workload target is capable of deploying Compose workloads."),
            ("Cpu", "CPUs", 20, "characteristic", "CpuCharacteristic", None),
            ("MemoryProperties", "Memory", 30, "characteristic", "MemoryCharacteristic", None),
            ("StorageProperties", "Storage", 40, "characteristic", "StorageCharacteristic", None),
            ("Peripheral", "Peripherals", 50, "characteristic", "PeripheralsCharacteristic", "Indicates the peripherals available on the workload target."),
            ("Interface", "Interfaces", 60, "characteristic", "InterfacesCharacteristic", "Indicates the interfaces available on the workload target."),
        ]
        sections = profile_sections(view)
        self.assertEqual([s.display for s in sections], [entry[0] for entry in expected])
        for section, (name, title, rank, kind, root, description) in zip(sections, expected):
            with self.subTest(name=name):
                cls = view.get_class(section.display)
                self.assertEqual((cls.title, cls.rank, cls.description), (title, rank, description))
                self.assertEqual((section.kind, section.target, section.rank), (kind, root, rank))
                if kind == "deployment":
                    properties = view.get_class(view.induced_slot('properties', root).range)
                    self.assertEqual(properties.title, 'Compose Deployment Technology')
                    self.assertEqual(properties.description, 'The properties for the Compose deployment technology will be defined once the deployment technology specifications are separated from the core specification.')
                key = view.induced_slot("key", section.target).equals_string
                self.assertEqual(key, KNOWN[root])
                fixtures = [p for p in (ROOT / "model/examples/profile/valid").iterdir()
                            if re.fullmatch(re.escape(root) + r"-[A-Za-z0-9][A-Za-z0-9_-]*\.(?:json|yaml|yml)", p.name)]
                self.assertTrue(fixtures, f"Missing valid fixtures for {root}")
                for fixture in fixtures:
                    self.assertEqual(parse_data(fixture.read_text(), fixture.suffix)["key"], key)

    def test_profile_presentation_does_not_require_obsolete_annotations(self):
        view = SchemaView(str(SCHEMA))
        for cls in view.all_classes().values():
            for name in ("margo_profile_example", "margo_profile_kind", "margo_profile_characteristic"):
                cls.annotations.pop(name, None)
        self.assert_profile_presentation_metadata(view)

    def test_required_characteristics_match_presentation(self):
        expressions = self.view.get_class('ComposeProfile').all_of
        required_classes = [e.slot_conditions['characteristics'].has_member.range for e in expressions]
        required = [self.view.induced_slot('key', name).equals_string for name in required_classes]
        displayed = [self.view.induced_slot('key', section.target).equals_string
                     for section in profile_sections(self.view)]
        self.assertEqual(len(required), 6)
        self.assertEqual(len(displayed), 6)
        self.assertEqual(set(required), set(displayed))


if __name__ == "__main__":
    unittest.main()
