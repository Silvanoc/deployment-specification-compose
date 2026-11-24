import json
import subprocess
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from linkml_runtime.utils.schemaview import SchemaView


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "model/compose-registry.linkml.yaml"
CLASS = "ComposeComponentAnnotations"
VERSION = "org.margo.component.version"


class RegistryPatternsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = SchemaView(str(SCHEMA))
        result = subprocess.run(
            ["linkml", "generate", "json-schema", str(SCHEMA)],
            check=True, capture_output=True, text=True, cwd=ROOT,
        )
        cls.properties = json.loads(result.stdout)["$defs"][CLASS]["properties"]

    def test_version_absolute_end_and_length(self):
        validator = Draft202012Validator(self.properties[VERSION])
        for value in ("1.0.0", "1.2.3-rc.1+build.7", "1.0.0+" + "a" * 122):
            with self.subTest(value=value):
                self.assertTrue(validator.is_valid(value))
                for suffix in ("\n", "\r\n"):
                    self.assertFalse(validator.is_valid(value + suffix))
        self.assertFalse(validator.is_valid("1.0.0+" + "a" * 123))
        self.assertEqual(self.view.induced_slot(VERSION, CLASS).pattern,
                         self.properties[VERSION]["pattern"])

    def test_optional_annotations_disallow_null_metadata(self):
        for suffix in ("title", "authors", "description", "version"):
            name = "org.opencontainers.image." + suffix
            with self.subTest(name=name):
                slot = self.view.induced_slot(name, CLASS)
                self.assertEqual(slot.annotations["margo_disallow_null"].value, "true")
                self.assertFalse(slot.required)
                self.assertEqual(slot.range, "string")
                self.assertTrue(Draft202012Validator(self.properties[name]).is_valid(""))


if __name__ == "__main__":
    unittest.main()
