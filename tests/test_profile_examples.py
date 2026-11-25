import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from common import ROOT, parse_data
from profile_examples import ProfileValidator
from schema_validation import ValidationEngine, annotation
import check_examples


class ProfileExamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ROOT / 'model/compose-profile.linkml.yaml'
        cls.native = ProfileValidator(cls.schema)
        cls.old = ValidationEngine()
        cls.profile = parse_data((ROOT / 'model/examples/profile/valid/ComposeProfile-001.json').read_text(), '.json')

    @classmethod
    def tearDownClass(cls):
        cls.old.close()

    def parity(self, target, data, valid):
        errors, notes = self.native.validate(self.schema, target, data)
        old_errors, old_notes = self.old.validate(self.schema, target, data)
        self.assertEqual((not errors, notes), (not old_errors, old_notes), (errors, old_errors))
        self.assertEqual(not errors, valid, errors)

    def test_existing_fixtures(self):
        for kind in ('valid', 'invalid'):
            for path in sorted((ROOT / 'model/examples/profile' / kind).glob('*.json')):
                with self.subTest(path=path):
                    self.parity(path.stem.split('-')[0], parse_data(path.read_text(), '.json'), kind == 'valid')

    def test_required_members_and_duplicate_keys(self):
        self.parity('ComposeProfile', self.profile, True)
        for member in self.profile['characteristics']:
            data = copy.deepcopy(self.profile)
            data['characteristics'] = [x for x in data['characteristics'] if x['key'] != member['key']]
            with self.subTest(missing=member['key']): self.parity('ComposeProfile', data, False)
        for key in ('margo.org/resource/cpu', 'vendor.example/unknown'):
            data = copy.deepcopy(self.profile)
            if key.startswith('vendor'):
                data['characteristics'].append({'key': key, 'properties': {}})
                duplicate = {'key': key, 'properties': {'different': 1}}
            else:
                duplicate = copy.deepcopy(data['characteristics'][1])
                duplicate['properties']['cpus'][0]['cores'] = 25
            data['characteristics'].append(duplicate)
            with self.subTest(duplicate=key): self.parity('ComposeProfile', data, False)
            self.assertTrue(any('duplicate' in e for e in self.native.validate(self.schema, 'ComposeProfile', data)[0]))

    def test_open_unknown_closed_known_and_extensions(self):
        unknown = {'key': 'vendor.example/unknown', 'properties': {'arbitrary': {'nested': True}}, 'vendor': 1}
        data = copy.deepcopy(self.profile); data['characteristics'].append(unknown)
        self.parity('ComposeProfile', data, True)
        data = copy.deepcopy(self.profile); data['characteristics'][1]['properties']['cpus'][0]['architecture'] = 'bad'
        self.parity('ComposeProfile', data, False)
        for key, value, valid in [('x-vendor-extensions', {}, True), ('x-vendor-extensions', 1, False),
                                  ('x-Vendor-extensions', {}, False), ('other', {}, False)]:
            data = copy.deepcopy(self.profile); data[key] = value
            with self.subTest(key=key, value=value): self.parity('ComposeProfile', data, valid)
        self.parity('ComposeDeploymentCharacteristic', {'key': 'margo.org/deployment/compose', 'properties': {}}, True)

    def test_nullable_and_target_order(self):
        for value, valid in ((None, True), ({}, False), ([], True)):
            data = copy.deepcopy(self.profile); data['characteristics'][1]['properties']['cpus'] = value
            self.parity('ComposeProfile', data, valid)
        data = copy.deepcopy(self.profile); data['characteristics'][1]['properties']['cpus'][0]['architecture'] = None
        self.parity('ComposeProfile', data, False)
        opened = {'key': 'vendor.example/unknown', 'properties': {}, 'anything': 1}
        closed = {'key': 'margo.org/deployment/compose', 'properties': {}, 'anything': 1}
        for order in ((('ComposeOtherCharacteristic', opened, True), ('ComposeDeploymentCharacteristic', closed, False)),
                      (('ComposeDeploymentCharacteristic', closed, False), ('ComposeOtherCharacteristic', opened, True))):
            validator = ProfileValidator(self.schema)
            for target, data, valid in order:
                with self.subTest(order=order, target=target):
                    self.assertEqual(not validator.validate(self.schema, target, data)[0], valid)

    def test_precise_numbers(self):
        template = '{"key":"margo.org/resource/cpu","properties":{"cpus":[{"architecture":"amd64","cores":%s}]}}'
        for number, valid in (('9' * 4301, True), ('1e1000', True), ('2.000', True), ('2.00000000000000000001', True)):
            with self.subTest(number=number[:30]):
                data = parse_data(template % number, '.json')
                self.parity('CpuCharacteristic', data, valid)

    def test_unknown_target_and_exceptions_not_invalid(self):
        with self.assertRaisesRegex(ValueError, 'Unknown profile target'):
            self.native.validate(self.schema, 'NotAClass', {})
        fixture = ROOT / 'model/examples/profile/invalid/ComposeProfile-001.json'
        with patch.object(self.native.validator, 'validate', side_effect=RuntimeError('plugin failed')):
            with self.assertRaisesRegex(RuntimeError, 'plugin failed'):
                check_examples.validate(self.schema, 'ComposeProfile', fixture, negative=True, engine=self.native)

    def test_modeled_characteristic_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            schema = Path(temp) / 'profile.yaml'
            schema.write_text(self.schema.read_text() + '''
  VendorCpuCharacteristic:
    is_a: CpuCharacteristic
    attributes:
      vendor_flag:
        range: string
        required: true
  OverrideCpuCharacteristic:
    is_a: CpuCharacteristic
    slot_usage:
      key:
        equals_string: vendor.example/cpu
  ProfileBundle:
    attributes:
      profile:
        range: ComposeProfile
        inlined: true
        required: true
  DerivedProfile:
    is_a: ComposeProfile
    slot_usage:
      characteristics:
        description: Locally overridden description.
        annotations:
          vendor_hint: example
''')
            native = ProfileValidator(schema)
            self.assertEqual(annotation(native.view.induced_slot('characteristics', 'ComposeProfile'), 'margo_unique_by'), 'key')
            self.assertIsNone(annotation(native.view.induced_slot('characteristics', 'DerivedProfile'), 'margo_unique_by'))
            cpu = copy.deepcopy(self.profile['characteristics'][1])
            cpu['vendor_flag'] = 'enabled'
            data = copy.deepcopy(self.profile); data['characteristics'][1] = cpu
            for target, instance in [('VendorCpuCharacteristic', cpu), ('ComposeProfile', data)]:
                with self.subTest(target=target):
                    native_errors, native_notes = native.validate(schema, target, instance)
                    old_errors, old_notes = self.old.validate(schema, target, instance)
                    self.assertEqual((not native_errors, native_notes), (not old_errors, old_notes), (native_errors, old_errors))
                    self.assertFalse(native_errors)
            del cpu['vendor_flag']
            self.assertTrue(native.validate(schema, 'VendorCpuCharacteristic', cpu)[0])
            overridden = copy.deepcopy(self.profile)
            overridden['characteristics'][1]['key'] = 'vendor.example/cpu'
            for target, instance in [('ComposeProfile', overridden), ('ProfileBundle', {'profile': overridden})]:
                with self.subTest(target=target, subtype='overridden key'):
                    native_errors, _ = native.validate(schema, target, instance)
                    old_errors, _ = self.old.validate(schema, target, instance)
                    self.assertTrue(native_errors)
                    self.assertTrue(old_errors)
            missing = copy.deepcopy(self.profile)
            missing['characteristics'].pop(1)
            duplicates = []
            known = copy.deepcopy(self.profile)
            extra_cpu = copy.deepcopy(known['characteristics'][1]); extra_cpu['properties']['cpus'][0]['cores'] = 25
            known['characteristics'].append(extra_cpu); duplicates.append(known)
            unknown = copy.deepcopy(self.profile)
            unknown['characteristics'].extend([{'key': 'vendor.example/unknown', 'properties': {}},
                                               {'key': 'vendor.example/unknown', 'properties': {'different': 1}}])
            duplicates.append(unknown)
            for label, instance in [('valid', self.profile), ('duplicate known', known), ('duplicate unknown', unknown)]:
                with self.subTest(derived=label):
                    native_errors, native_notes = native.validate(schema, 'DerivedProfile', instance)
                    old_errors, old_notes = self.old.validate(schema, 'DerivedProfile', instance)
                    self.assertEqual((not native_errors, native_notes), (not old_errors, old_notes), (native_errors, old_errors))
                    self.assertEqual(not native_errors, label == 'valid')
            for label, instance, valid in [('valid', self.profile, True), ('missing cpu', missing, False),
                                           ('duplicate known', duplicates[0], False), ('duplicate unknown', duplicates[1], False)]:
                with self.subTest(nested=label):
                    native_errors, native_notes = native.validate(schema, 'ProfileBundle', {'profile': instance})
                    old_errors, old_notes = self.old.validate(schema, 'ProfileBundle', {'profile': instance})
                    self.assertEqual((not native_errors, native_notes), (not old_errors, old_notes), (native_errors, old_errors))
                    self.assertEqual(not native_errors, valid, native_errors)
            arbitrary = copy.deepcopy(self.profile)
            arbitrary['characteristics'].append({'key': 'vendor.example/feature', 'properties': {'nested': {'characteristics': []}}})
            native_errors, native_notes = native.validate(schema, 'ProfileBundle', {'profile': arbitrary})
            old_errors, old_notes = self.old.validate(schema, 'ProfileBundle', {'profile': arbitrary})
            self.assertEqual((not native_errors, native_notes), (not old_errors, old_notes), (native_errors, old_errors))
            self.assertFalse(native_errors)

    def test_synthetic_integer_precision_and_numeric_constraint_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            schema = Path(temp) / 'profile.yaml'
            addition = '''
  IntegralProbe:
    attributes:
      count:
        range: integer
        required: true
'''
            schema.write_text(self.schema.read_text() + addition)
            native = ProfileValidator(schema)
            for number, valid in (('9' * 4301, True), ('1e1000', True), ('2.000', True), ('2.00000000000000000001', False)):
                with self.subTest(number=number[:30]):
                    instance = parse_data('{"count":' + number + '}', '.json')
                    result = native.validate(schema, 'IntegralProbe', instance)[0]
                    old_result, _ = self.old.validate(schema, 'IntegralProbe', instance)
                    self.assertEqual(not result, not old_result, (result, old_result))
                    self.assertEqual(not result, valid, result)
            schema.write_text(self.schema.read_text() + addition + '        minimum_value: 1\n')
            with self.assertRaisesRegex(ValueError, 'Cannot verify LinkML numeric constraint'):
                ProfileValidator(schema)
