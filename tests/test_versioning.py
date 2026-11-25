"""Check the distinct SemVer annotation and OCI tag representations."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VersionEncodingTests(unittest.TestCase):
    def validate(self, schema, target, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return subprocess.run(
                ["linkml", "validate", "--schema", str(ROOT / "model" / schema),
                 "--target-class", target, str(path)],
                capture_output=True, text=True, check=False,
            )

    def assert_invalid(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertIn("[ERROR] [", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_build_metadata_uses_distinct_version_and_tag(self):
        version = "1.2.3+build.1"
        tag = "1.2.3_build.1"
        self.assertEqual(version.replace("+", "_"), tag)
        annotation = {"org.margo.component.type": "compose",
                      "org.margo.component.version": version}
        component = {"repository": "oci://registry.example.com/example", "revision": tag}
        self.assertEqual(self.validate("compose-registry.linkml.yaml", "ComposeComponentAnnotations", annotation).returncode, 0)
        self.assertEqual(self.validate("compose-application.linkml.yaml", "ComposeComponentProperties", component).returncode, 0)

    def test_version_and_tag_reject_wrong_separator_and_long_tag(self):
        annotation = {"org.margo.component.type": "compose",
                      "org.margo.component.version": "1.2.3_build.1"}
        self.assert_invalid(self.validate("compose-registry.linkml.yaml", "ComposeComponentAnnotations", annotation))
        for revision in ("1.2.3+build.1", "1.2.3_" + "a" * 123):
            with self.subTest(revision=revision):
                component = {"repository": "oci://registry.example.com/example", "revision": revision}
                self.assert_invalid(self.validate("compose-application.linkml.yaml", "ComposeComponentProperties", component))

    def test_oci_tag_length_boundary(self):
        version = "1.2.3+" + "a" * 122
        tag = "1.2.3_" + "a" * 122
        self.assertEqual(len(tag), 128)
        annotation = {"org.margo.component.type": "compose",
                      "org.margo.component.version": version}
        component = {"repository": "oci://registry.example.com/example", "revision": tag}
        self.assertEqual(self.validate("compose-registry.linkml.yaml", "ComposeComponentAnnotations", annotation).returncode, 0)
        self.assertEqual(self.validate("compose-application.linkml.yaml", "ComposeComponentProperties", component).returncode, 0)


if __name__ == "__main__":
    unittest.main()
