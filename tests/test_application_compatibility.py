"""Focused checks for the shared application model and Compose overrides."""

import json
import copy
import subprocess
import sys
import unittest
from pathlib import Path

import jsonschema
import yaml
from linkml_runtime import SchemaView
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from schema_validation import ValidationEngine

MODEL = ROOT / "model/compose-application.linkml.yaml"
EXAMPLE = ROOT / "model/examples/application/valid/ApplicationDescription-001.yaml"


class ApplicationCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = SchemaView(str(MODEL))
        result = subprocess.run(["gen-json-schema", str(MODEL)], check=True,
                                capture_output=True, text=True)
        cls.schema = json.loads(result.stdout)

    def valid(self, class_name, value):
        schema = {"$defs": self.schema["$defs"], "$ref": f"#/$defs/{class_name}"}
        return not list(jsonschema.Draft7Validator(schema).iter_errors(value))

    def test_inherited_slots_and_extension_metadata(self):
        self.assertIn("linkml:types", self.view.schema.imports)
        self.assertEqual(self.view.schema.annotations["margo_optional_typed_nonnull"].value, "true")
        self.assertTrue(self.view.schema.classes["ApplicationDescription"].tree_root)
        for cls in ("ApplicationDescription", "DeploymentProfile", "Component"):
            self.assertEqual(self.view.schema.classes[cls].annotations["margo_extensions_pattern"].value,
                             r"^x-[a-z][a-z0-9-]*-extensions(?![\s\S])")
            self.assertNotIn("x-placeholder-extensions", {s.name for s in self.view.class_induced_slots(cls)})
        slots = {s.name: s for s in self.view.class_induced_slots("ComposeDeploymentProfile")}
        self.assertTrue(slots["key"].required)
        self.assertTrue(slots["id"].required)
        self.assertEqual(slots["components"].range, "ComposeComponent")
        self.assertIn("components", {s.name for s in self.view.class_induced_slots("ComponentListFragment")})

    def test_application_example_linkml_validator(self):
        result = subprocess.run(["linkml", "validate", "--schema", str(MODEL),
                                 "--target-class", "ApplicationDescription", str(EXAMPLE)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_application_api_version(self):
        example = yaml.safe_load(EXAMPLE.read_text())
        # The unpatched generator represents configuration schema references
        # differently from the published example; isolate the version check.
        example.pop("configuration", None)
        self.assertTrue(self.valid("ApplicationDescription", example))
        self.assertFalse(self.valid("ApplicationDescription", {**example, "apiVersion": "v2"}))

    def test_parameter_scalars_and_homogeneous_arrays(self):
        target = {"pointer": "PORT", "components": ["main"]}
        for value in ("x", 1, 1.5, True, ["x"], [1, 2], [1.5, 2.5], [True, False]):
            with self.subTest(value=value):
                self.assertTrue(self.valid("Parameter", {"name": "port", "value": value, "targets": [target]}))
        self.assertFalse(self.valid("Parameter", {"name": "port", "value": ["x", True], "targets": [target]}))

    def test_selector_boolean_and_invalid_operator(self):
        expression = {"key": "feature", "property": "/enabled", "operator": "In", "values": [True]}
        self.assertTrue(self.valid("MatchExpression", expression))
        self.assertFalse(self.valid("MatchExpression", {**expression, "operator": "Bogus"}))
        item = {"property": "/enabled", "operator": "In", "values": [False]}
        self.assertTrue(self.valid("ItemMatchExpression", item))
        self.assertFalse(self.valid("ItemMatchExpression", {**item, "values": [{}]}))

    def test_selector_rule_metadata(self):
        groups = {"membership": ["In", "NotIn"],
                  "existence": ["Exists", "DoesNotExist"],
                  "numeric": ["Gt", "GtEq", "Lt", "LtEq"]}
        for cls in ("MatchExpression", "ItemMatchExpression"):
            expected = dict(groups)
            if cls == "MatchExpression":
                expected["object_array"] = ["ContainsAll", "ContainsAny"]
            annotation = self.view.schema.classes[cls].annotations["margo_selector_rules"]
            self.assertEqual(json.loads(annotation.value), expected)

    def test_timeout_and_required_profile_fields(self):
        slots = {s.name: s for s in self.view.class_induced_slots("ComponentProperties")}
        for field in ("wait", "timeout"):
            self.assertEqual(slots[field].annotations["margo_disallow_null"].value, "true")
        self.assertEqual(slots["repository"].annotations["margo_oci_repository"].value, "true")
        properties = {"repository": "oci://registry.example.com/example", "revision": "1.0.0"}
        self.assertTrue(self.valid("ComponentProperties", properties))
        self.assertTrue(self.valid("ComponentProperties", {**properties, "timeout": "5m0s"}))
        self.assertFalse(self.valid("ComponentProperties", {**properties, "timeout": "five"}))
        component = {"name": "main", "properties": properties}
        self.assertTrue(self.valid("ComponentListFragment", {"components": [component]}))
        self.assertFalse(self.valid("DeploymentProfile", {"components": [component]}))

    def test_typed_select_options_generated_schema(self):
        metadata = self.view.get_class("SelectValidationSchema").annotations["margo_typed_options"].value
        self.assertEqual(json.loads(metadata), {"type_slot": "dataType", "options_slot": "options",
                                               "types": {"string": "string", "integer": "integer",
                                                         "double": "number", "boolean": "boolean"}})
        for data_type, options in (("string", ["a", "b"]), ("integer", [30, 60]),
                                   ("double", [1.5, 2.5]), ("boolean", [True, False]),
                                   ("array[integer]", [[1, 2], [3, 4]])):
            with self.subTest(data_type=data_type):
                self.assertTrue(self.valid("SelectValidationSchema", {"name": "choice", "dataType": data_type, "options": options}))
        self.assertFalse(self.valid("SelectValidationSchema", {"name": "choice", "dataType": "string", "options": [{}]}))

    def test_absolute_end_patterns_generated_schema(self):
        app = yaml.safe_load(EXAMPLE.read_text())
        app.pop("configuration", None)
        properties = {"repository": "oci://registry.example.com/example", "revision": "1.0.0_build.1", "timeout": "5m0s"}
        self.assertTrue(self.valid("ApplicationDescription", app))
        self.assertTrue(self.valid("ComponentProperties", properties))
        for ending in ("\n", "\r\n"):
            with self.subTest(ending=repr(ending)):
                self.assertFalse(self.valid("ApplicationDescription", {**app, "id": app["id"] + ending}))
                for field in ("revision", "timeout"):
                    self.assertFalse(self.valid("ComponentProperties", {**properties, field: properties[field] + ending}))

    def test_generator_no_include_null(self):
        result = subprocess.run(["gen-json-schema", "--no-include-null", str(MODEL)],
                                check=True, capture_output=True, text=True)
        schema = json.loads(result.stdout)

        def valid(cls, value):
            target = {"$defs": schema["$defs"], "$ref": f"#/$defs/{cls}"}
            return not list(jsonschema.Draft7Validator(target).iter_errors(value))

        cases = (
            ("Parameter", {"name": "x", "targets": [{"pointer": "X", "components": ["main"]}]}, "value", [1, 2]),
            ("Metadata", {"name": "x", "version": "1", "catalog": {"organization": [{"name": "org"}]}}, "description", "text"),
            ("ApplicationMetadata", {}, "tagline", "text"),
            ("ApplicationMetadata", {}, "tags", ["tag"]),
            ("DeploymentProfile", {"id": "x", "key": "margo.org/deployment/compose", "components": []}, "description", "text"),
            ("ComponentProperties", {"repository": "oci://registry.example.com/example", "revision": "1.0.0"}, "wait", True),
            ("ComponentProperties", {"repository": "oci://registry.example.com/example", "revision": "1.0.0"}, "timeout", "5m0s"),
        )
        for cls, base, field, value in cases:
            with self.subTest(cls=cls, field=field):
                self.assertTrue(valid(cls, base))
                self.assertTrue(valid(cls, {**base, field: value}))
                self.assertFalse(valid(cls, {**base, field: None}))


class ApplicationEngineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = ValidationEngine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.application = yaml.safe_load(EXAMPLE.read_text())

    def assert_engine_valid(self, application):
        before = copy.deepcopy(application)
        errors, _ = self.engine.validate(MODEL, "ApplicationDescription", application)
        self.assertEqual(errors, [], errors)
        self.assertEqual(application, before, "validation must preserve raw input")

    def test_mixed_helm_and_compose_profiles(self):
        helm = copy.deepcopy(self.application["deploymentProfiles"][0])
        helm["key"] = "margo.org/deployment/helm"
        helm["id"] = "example-helm"
        self.application["deploymentProfiles"].append(helm)
        self.assert_engine_valid(self.application)

    def test_object_extensions_at_all_levels(self):
        profile = self.application["deploymentProfiles"][0]
        for node in (self.application, profile, profile["components"][0]):
            node["x-acme-extensions"] = {"enabled": True, "nested": {"value": None}}
        self.assert_engine_valid(self.application)
        schema, _ = self.engine.cache[(str(MODEL.resolve()), "ApplicationDescription")]
        errors = list(jsonschema.validators.validator_for(schema)(schema).iter_errors(self.application))
        self.assertEqual(errors, [], [error.message for error in errors])

    def test_invalid_extensions_rejected(self):
        for location in ("application", "profile", "component"):
            for name, value in (("x-acme-extensions", "not an object"),
                                ("x-Acme-extensions", {}),
                                ("x_placeholder_extensions", {})):
                with self.subTest(location=location, name=name):
                    application = copy.deepcopy(self.application)
                    profile = application["deploymentProfiles"][0]
                    node = {"application": application, "profile": profile,
                            "component": profile["components"][0]}[location]
                    node[name] = value
                    errors, _ = self.engine.validate(MODEL, "ApplicationDescription", application)
                    self.assertTrue(errors, (location, name, value))

    def test_complete_application_values(self):
        profile = self.application["deploymentProfiles"][0]
        profile["placementConstraints"][0]["matchExpressions"].append(
            {"key": "feature", "property": "/enabled", "operator": "In", "values": [True]})
        self.application["parameters"]["idpName"]["value"] = ["first", "second"]
        profile["components"][0]["properties"]["timeout"] = "5m0s"
        self.assert_engine_valid(self.application)

    def test_select_option_types_in_engine(self):
        for data_type, options in (("string", ["a", "b"]), ("integer", [30, 60]),
                                   ("double", [1.5, 2.5]), ("boolean", [True, False])):
            with self.subTest(data_type=data_type):
                errors, _ = self.engine.validate(MODEL, "SelectValidationSchema",
                                                 {"name": "choice", "dataType": data_type, "options": options})
                self.assertEqual(errors, [], errors)

    def test_absolute_end_patterns_in_engine_and_patched_schema(self):
        self.assert_engine_valid(self.application)
        schema, _ = self.engine.cache[(str(MODEL.resolve()), "ApplicationDescription")]
        validator = jsonschema.validators.validator_for(schema)(schema)
        for ending in ("", "\n", "\r\n"):
            for field in ("id", "revision", "timeout", "extension"):
                with self.subTest(field=field, ending=repr(ending)):
                    application = copy.deepcopy(self.application)
                    properties = application["deploymentProfiles"][0]["components"][0]["properties"]
                    if field == "id":
                        application["id"] += ending
                    elif field == "revision":
                        properties["revision"] = "1.0.0_build.1" + ending
                    elif field == "timeout":
                        properties["timeout"] = "5m0s" + ending
                    else:
                        application["x-acme-extensions" + ending] = {}
                    errors, _ = self.engine.validate(MODEL, "ApplicationDescription", application)
                    schema_errors = list(validator.iter_errors(application))
                    self.assertEqual(bool(errors), bool(ending), errors)
                    self.assertEqual(bool(schema_errors), bool(ending), [e.message for e in schema_errors])


if __name__ == "__main__":
    unittest.main()
