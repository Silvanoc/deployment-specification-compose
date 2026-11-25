import json
import re
import subprocess
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from linkml_runtime.utils.schemaview import SchemaView


ROOT = Path(__file__).resolve().parents[1]
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

    def test_known_keys_have_absolute_end(self):
        for cls, key in KNOWN.items():
            with self.subTest(cls=cls):
                validator = self.key_validator(cls)
                self.assertTrue(validator.is_valid(key))
                slot = self.view.induced_slot("key", cls)
                self.assertEqual(slot.equals_string, key)
                self.assertIsNotNone(re.search(slot.pattern, slot.equals_string))
                for suffix in ("\n", "\r\n"):
                    self.assertFalse(validator.is_valid(key + suffix))
                self.assertEqual(
                    self.view.induced_slot("key", cls).pattern,
                    self.definitions[cls]["properties"]["key"]["pattern"],
                )

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
        expected = [
            ("ComposeDeploymentCharacteristic", "Compose Deployment Technology Support", 10, "deployment", "ComposeDeploymentCharacteristic", "The following required characteristics indicate a workload target is capable of deploying Compose workloads."),
            ("Cpu", "CPUs", 20, "characteristic", "CpuCharacteristic", None),
            ("MemoryProperties", "Memory", 30, "characteristic", "MemoryCharacteristic", None),
            ("StorageProperties", "Storage", 40, "characteristic", "StorageCharacteristic", None),
            ("Peripheral", "Peripherals", 50, "characteristic", "PeripheralsCharacteristic", "Indicates the peripherals available on the workload target."),
            ("Interface", "Interfaces", 60, "characteristic", "InterfacesCharacteristic", "Indicates the interfaces available on the workload target."),
        ]
        classes = [c for c in self.view.all_classes().values() if "margo_profile_kind" in c.annotations]
        classes.sort(key=lambda c: c.rank)
        self.assertEqual([c.name for c in classes], [entry[0] for entry in expected])
        for cls, (name, title, rank, kind, root, description) in zip(classes, expected):
            with self.subTest(name=name):
                self.assertEqual((cls.title, cls.rank, cls.description), (title, rank, description))
                annotations = {key: value.value for key, value in cls.annotations.items()}
                expected_annotations = {
                    "margo_profile_kind": kind,
                    "margo_profile_characteristic": root,
                    "margo_profile_example": f"profile/{root}-001.json",
                }
                if kind == "deployment":
                    expected_annotations.update({
                        "margo_profile_label": "Compose Deployment Technology",
                        "margo_profile_note": "The properties for the Compose deployment technology will be defined once the deployment technology specifications are separated from the core specification.",
                    })
                self.assertEqual(annotations, expected_annotations)
                fixture = ROOT / "model/examples/profile/valid" / f"{root}-001.json"
                key = self.view.induced_slot("key", annotations["margo_profile_characteristic"]).equals_string
                self.assertEqual(key, KNOWN[root])
                self.assertEqual(json.loads(fixture.read_text())["key"], key)

    def test_required_characteristics_match_presentation(self):
        slot = self.view.induced_slot("characteristics", "ComposeProfile")
        required = json.loads(slot.annotations["margo_required_characteristics"].value)
        displayed = [
            self.view.induced_slot(
                "key", cls.annotations["margo_profile_characteristic"].value
            ).equals_string
            for cls in self.view.all_classes().values()
            if "margo_profile_kind" in cls.annotations
        ]
        self.assertEqual(len(required), 6)
        self.assertEqual(len(displayed), 6)
        self.assertEqual(set(required), set(displayed))


if __name__ == "__main__":
    unittest.main()
