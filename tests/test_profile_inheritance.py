import copy
import sys
import tempfile
import unittest
from pathlib import Path

from linkml_runtime.utils.schemaview import SchemaView

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from common import parse_data
from generate import render_page
from schema_validation import annotation, generated_schema, precise_validator, semantic_errors


class ProfileInheritanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / 'model/compose-profile.linkml.yaml'
        cls.view = SchemaView(str(cls.path))
        cls.schema = generated_schema(cls.path)
        cls.validator = precise_validator(cls.schema)
        fixture = ROOT / 'model/examples/profile/valid/ComposeProfile-001.json'
        cls.profile = parse_data(fixture.read_text(), '.json')

    def test_inherited_profile_and_characteristics(self):
        self.assertEqual(self.view.get_class('ComposeProfile').is_a, 'WorkloadTargetProfile')
        self.assertNotIn('WorkloadTargetProfile', self.view.schema.classes)
        parent = self.view.induced_slot('characteristics', 'WorkloadTargetProfile')
        child = self.view.induced_slot('characteristics', 'ComposeProfile')
        self.assertEqual(annotation(parent, 'margo_unique_by'), 'key')
        self.assertIsNone(annotation(child, 'margo_unique_by'))
        self.assertTrue(child.required and child.multivalued and child.inlined and child.inlined_as_list)
        self.assertEqual([x.range for x in child.any_of], ['ComposeDeploymentCharacteristic',
            'CpuCharacteristic', 'MemoryCharacteristic', 'StorageCharacteristic',
            'PeripheralsCharacteristic', 'InterfacesCharacteristic', 'ComposeOtherCharacteristic'])
        self.assertEqual(self.schema['properties']['characteristics']['x-margo-unique-by'], 'key')

    def test_required_unique_and_unknown(self):
        self.assertTrue(self.validator.is_valid(self.profile))
        self.assertFalse(semantic_errors(self.profile, self.schema)[0])
        for index in range(len(self.profile['characteristics'])):
            missing = copy.deepcopy(self.profile)
            missing['characteristics'].pop(index)
            self.assertFalse(self.validator.is_valid(missing), index)
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
            text = self.path.read_text().replace('margo_required_characteristics:',
                'margo_unique_by: vendorKey\n          margo_required_characteristics:', 1)
            path.write_text(text)
            schema = generated_schema(path)
            prop = schema['properties']['characteristics']
            self.assertEqual(prop['x-margo-unique-by'], 'vendorKey')
            self.assertIn('vendorKey', prop['allOf'][0]['contains']['properties'])
            path.write_text(text.replace('margo_unique_by: vendorKey', "margo_unique_by: ''"))
            with self.assertRaisesRegex(ValueError, 'Invalid required characteristics metadata'):
                generated_schema(path)

    def test_markdown_byte_parity(self):
        actual = render_page('profile', ROOT, self.path).encode('utf-8')
        expected = (ROOT / 'specification/margo-management-interface/compose-profile-characteristics.md').read_bytes()
        self.assertEqual(actual, expected)
