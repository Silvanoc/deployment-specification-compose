import copy
import sys
import tempfile
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator

from linkml_runtime.utils.schemaview import SchemaView

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from common import parse_data
from generate import render_page
from schema_validation import ValidationEngine, annotation, generated_schema, precise_validator, semantic_errors


class ProfileInheritanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / 'model/compose-profile.linkml.yaml'
        cls.view = SchemaView(str(cls.path))
        cls.schema = generated_schema(cls.path)
        cls.validator = precise_validator(cls.schema)
        cls.engine = ValidationEngine()
        fixture = ROOT / 'model/examples/profile/valid/ComposeProfile-001.json'
        cls.profile = parse_data(fixture.read_text(), '.json')

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_inherited_profile_and_characteristics(self):
        self.assertEqual(self.view.get_class('ComposeProfile').is_a, 'WorkloadTargetProfile')
        self.assertNotIn('WorkloadTargetProfile', self.view.schema.classes)
        parent = self.view.induced_slot('characteristics', 'WorkloadTargetProfile')
        child = self.view.induced_slot('characteristics', 'ComposeProfile')
        self.assertEqual(annotation(parent, 'margo_unique_by'), 'key')
        self.assertEqual(annotation(child, 'margo_unique_by'), 'key')
        self.assertTrue(child.required and child.multivalued and child.inlined and child.inlined_as_list)
        self.assertEqual([x.range for x in child.any_of], ['ComposeDeploymentCharacteristic',
            'CpuCharacteristic', 'MemoryCharacteristic', 'StorageCharacteristic',
            'PeripheralsCharacteristic', 'InterfacesCharacteristic', 'ComposeOtherCharacteristic'])
        self.assertEqual(self.schema['properties']['characteristics']['x-margo-unique-by'], 'key')
        self.assertEqual(len(self.schema['properties']['characteristics']['allOf']), 6)
        self.assertEqual(self.schema['properties']['characteristics']['allOf'],
                         self.schema['$defs']['ComposeProfile']['properties']['characteristics']['allOf'])

    def test_generic_profile_does_not_require_specialized_members(self):
        schema = generated_schema(self.path, 'WorkloadTargetProfile')
        self.assertTrue(Draft202012Validator(schema).is_valid({'characteristics': []}))

    def test_required_unique_and_unknown(self):
        self.assertTrue(self.validator.is_valid(self.profile))
        self.assertFalse(semantic_errors(self.profile, self.schema)[0])
        for index in range(len(self.profile['characteristics'])):
            missing = copy.deepcopy(self.profile)
            missing['characteristics'].pop(index)
            self.assertFalse(self.validator.is_valid(missing), index)
            self.assertFalse(Draft202012Validator(self.schema).is_valid(missing), index)
            self.assertTrue(self.engine.validate(self.path, 'ComposeProfile', missing)[0], index)
        duplicate = copy.deepcopy(self.profile)
        duplicate['characteristics'].append(copy.deepcopy(duplicate['characteristics'][0]))
        self.assertTrue(any('duplicate key' in e for e in semantic_errors(duplicate, self.schema)[0]))
        unknown = copy.deepcopy(self.profile)
        unknown['characteristics'].append({'key': 'vendor.example/deployment/future', 'properties': {'novel': 1}})
        self.assertTrue(self.validator.is_valid(unknown))
        self.assertFalse(semantic_errors(unknown, self.schema)[0])

    def test_explicit_unique_metadata_overrides_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.yaml'
            text = self.path.read_text().replace('      characteristics:\n        # slot_usage',
                '      characteristics:\n        annotations:\n          margo_unique_by: vendorKey\n        # slot_usage', 1)
            path.write_text(text)
            schema = generated_schema(path)
            prop = schema['properties']['characteristics']
            self.assertEqual(prop['x-margo-unique-by'], 'vendorKey')
            self.assertIn('contains', prop['allOf'][0])
            path.write_text(text.replace('margo_unique_by: vendorKey', "margo_unique_by: ''"))
            with self.assertRaisesRegex(ValueError, 'Invalid unique-by metadata'):
                generated_schema(path)

    def test_descendant_members_and_inherited_expressions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.yaml'
            path.write_text(self.path.read_text() + '''\n  ExtendedCpuCharacteristic:
    is_a: CpuCharacteristic
    attributes:
      extraField:
        range: string
        required: true
  ImpostorCpuCharacteristic:
    is_a: CpuCharacteristic
    slot_usage:
      key:
        equals_string: vendor.example/cpu
  ExtendedComposeProfile:
    is_a: ComposeProfile
''')
            schema = generated_schema(path, 'ExtendedComposeProfile')
            validator = Draft202012Validator(schema)
            data = copy.deepcopy(self.profile)
            cpu = next(c for c in data['characteristics'] if c['key'] == 'margo.org/resource/cpu')
            cpu['extraField'] = 'valid'
            self.assertTrue(validator.is_valid(data))
            cpu['extraField'] = 1
            self.assertFalse(validator.is_valid(data))
            data['characteristics'].remove(cpu)
            self.assertFalse(validator.is_valid(data))
            data['characteristics'].append({'key': 'vendor.example/cpu', 'properties': {'cpus': [{'cores': 1}]}})
            self.assertFalse(validator.is_valid(data))

    def test_unsupported_membership_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.yaml'
            text = self.path.read_text().replace('range: CpuCharacteristic\n      - slot_conditions:',
                'range: CpuCharacteristic\n              equals_string: unsupported\n      - slot_conditions:', 1)
            path.write_text(text)
            with self.assertRaisesRegex(ValueError, 'Unsupported has_member range'):
                generated_schema(path)
            path.write_text(self.path.read_text().replace('    all_of:', '    any_of:', 1))
            with self.assertRaisesRegex(ValueError, 'Unsupported has_member outside class all_of'):
                generated_schema(path)

    def test_direct_slot_membership_supported_or_rejected_if_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'direct.yaml'
            text = '''id: https://example.org/direct
name: DirectMembership
imports: [linkml:types]
classes:
  Root:
    tree_root: true
    attributes:
      entries:
        multivalued: true
        range: Child
        inlined_as_list: true
        has_member:
          range_expression:
            slot_conditions:
              kind:
                equals_string: required
  Child:
    attributes:
      kind:
        range: string
'''
            path.write_text(text)
            schema = generated_schema(path)
            validator = Draft202012Validator(schema)
            self.assertTrue(validator.is_valid({'entries': [{'kind': 'required'}]}))
            self.assertFalse(validator.is_valid({'entries': [{'kind': 'other'}]}))
            path.write_text(text.replace('          range_expression:\n            slot_conditions:\n              kind:\n                equals_string: required', '          range: Child'))
            with self.assertRaisesRegex(ValueError, 'Unsupported generated slot has_member'):
                generated_schema(path)

    def test_membership_documentation_metadata_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.yaml'
            text = self.path.read_text().replace('      - slot_conditions:\n          characteristics:\n            has_member:\n              range: ComposeDeploymentCharacteristic',
                '      - description: Required deployment\n        slot_conditions:\n          characteristics:\n            description: Required slot\n            has_member:\n              description: Required member\n              range: ComposeDeploymentCharacteristic', 1)
            path.write_text(text)
            schema = generated_schema(path)
            self.assertTrue(Draft202012Validator(schema).is_valid(self.profile))

    def test_has_member_as_annotation_and_slot_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'names.yaml'
            path.write_text('''id: https://example.org/names
name: Names
imports: [linkml:types]
classes:
  Root:
    tree_root: true
    annotations:
      has_member: documentation only
    all_of:
      - slot_conditions:
          has_member:
            has_member:
              range: Child
    attributes:
      has_member:
        range: Child
        multivalued: true
        inlined_as_list: true
  Child:
    attributes:
      value:
        range: string
        required: true
''')
            schema = generated_schema(path)
            validator = Draft202012Validator(schema)
            self.assertTrue(validator.is_valid({'has_member': [{'value': 'ok'}]}))
            self.assertFalse(validator.is_valid({'has_member': []}))

    def test_markdown_byte_parity(self):
        actual = render_page('profile', ROOT, self.path).encode('utf-8')
        expected = (ROOT / 'specification/margo-management-interface/compose-profile-characteristics.md').read_bytes()
        self.assertEqual(actual, expected)
