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
from schema_validation import generated_schema, precise_validator, root_class, semantic_errors


class DeploymentDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / 'model/compose-application.linkml.yaml'
        cls.schema = generated_schema(cls.path)
        cls.validator = precise_validator(cls.schema)
        cls.example = parse_data((ROOT / 'model/examples/application/valid/ApplicationDescription-001.yaml').read_text(), '.yaml')

    def check(self, data):
        return list(self.validator.iter_errors(data)), semantic_errors(data, self.schema)

    def test_imported_roots_and_specialization(self):
        view = SchemaView(str(self.path))
        self.assertNotIn('ApplicationDescription', view.schema.classes)
        self.assertTrue(view.get_class('ApplicationDescription').tree_root)
        self.assertEqual(view.induced_slot('components', 'ComposeDeploymentProfile').range, 'ComposeComponent')
        self.assertEqual(view.induced_slot('properties', 'ComposeComponent').range, 'ComposeComponentProperties')
        self.assertEqual(view.get_uri(view.get_class('ApplicationDescription'), expand=True), 'https://specification.margo.org/ApplicationDescription')
        profile = SchemaView(str(ROOT / 'model/compose-profile.linkml.yaml'))
        self.assertNotIn('Cpu', profile.schema.classes)
        self.assertIsNotNone(profile.get_class('Cpu'))
        self.assertEqual(profile.get_class('ComposeOtherCharacteristic').is_a, 'OtherCharacteristic')

    def test_mixed_repeat_and_unknown(self):
        data = copy.deepcopy(self.example)
        unknown = {'key': 'vendor.example/deployment/custom', 'id': 'custom', 'novel': 5,
                   'components': [{'name': 'c', 'novel': True, 'properties': {'nonOci': {'nested': 1}}}]}
        helm = copy.deepcopy(unknown)
        helm['key'] = 'margo.org/deployment/helm'
        data['deploymentProfiles'].extend([helm, copy.deepcopy(data['deploymentProfiles'][0]), unknown])
        errors, (semantic, notes) = self.check(data)
        self.assertFalse(errors)
        self.assertFalse(semantic)
        self.assertTrue(any('validated generically only' in n for n in notes))

    def test_compose_dispatch_and_extensions(self):
        data = copy.deepcopy(self.example)
        props = data['deploymentProfiles'][0]['components'][0]['properties']
        del props['repository']
        self.assertTrue(self.check(data)[0])
        props['repository'] = 'oci://BAD host/foo'
        self.assertTrue(any('OCI repository' in e for e in self.check(data)[1][0]))
        unknown = {'key': 'vendor.example/custom', 'id': 'x', 'x_runtime': 1,
                   'x-vendor-field': True, 'components': [{'name': 'c', 'x_runtime': 1,
                   'x-vendor-field': True, 'properties': {'anything': 1}}], 'x-acme-extensions': {}}
        data['deploymentProfiles'] = [unknown]
        self.assertFalse(self.check(data)[0])
        for key, value in [('x_placeholder_extensions', {}), ('x-BAD-extensions', {}), ('x-acme-extensions', 1)]:
            data['deploymentProfiles'][0] = dict(unknown, **{key: value})
            self.assertTrue(self.check(data)[0], key)

    def test_unknown_oci_shaped_properties_do_not_get_compose_checks(self):
        data = copy.deepcopy(self.example)
        data['deploymentProfiles'] = [{'key': 'vendor.example/unknown', 'id': 'custom',
            'components': [{'name': 'c', 'properties': {
                'repository': 'oci://BAD host/foo', 'revision': '1.0.0'}}]}]
        errors, (semantic, notes) = self.check(data)
        self.assertFalse(errors)
        self.assertFalse(semantic)
        self.assertTrue(any('validated generically only' in n for n in notes))

    def test_direct_generic_targets_do_not_apply_specialization(self):
        component = {'name': 'c', 'properties': {'repository': 'oci://BAD host/foo',
                                                'revision': '1.0.0', 'nonOci': {'extra': 1}}}
        profile = {'key': 'vendor.example/unknown', 'id': 'x', 'components': [component]}
        for target, value in [('Component', component), ('DeploymentProfile', profile)]:
            schema = generated_schema(self.path, target)
            self.assertTrue(precise_validator(schema).is_valid(value), target)
            self.assertFalse(semantic_errors(value, schema)[0], target)
            if target == 'Component':
                self.assertIn('description', schema['properties']['properties'])

    def test_unknown_parameter_target_is_open(self):
        data = copy.deepcopy(self.example)
        data['deploymentProfiles'] = [{'key': 'vendor.example/unknown', 'id': 'custom',
                                       'components': [{'name': 'c', 'properties': {}}]}]
        data['parameters'] = {'custom': {'targets': [{'pointer': '/field', 'components': ['c'],
                                                        'vendorTargetField': {'nested': True}}]}}
        errors, (semantic, notes) = self.check(data)
        self.assertFalse(errors)
        self.assertFalse(semantic)
        self.assertTrue(any('validated generically only' in n for n in notes))

    def test_duplicate_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.yaml'
            path.write_text(self.path.read_text() + '''\n  AnotherProfile:
    is_a: DeploymentProfile
    slot_usage:
      key:
        equals_string: margo.org/deployment/compose
''')
            with self.assertRaisesRegex(ValueError, 'Duplicate deployment profile key'):
                generated_schema(path)

    def test_bundle_inherits_root_nonnull_policy_and_allows_override(self):
        view = SchemaView(str(self.path))
        root = view.get_class('ApplicationDescription')
        shared = next(s.source_file for s in view.schema_map.values() if s.id == root.from_schema)
        self.assertTrue(shared.endswith('.yaml'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'other.yaml').write_text(f'''id: https://example.org/other
name: OtherDeployment
imports: [{shared[:-5]}]
classes:
  OtherDeploymentProfile:
    is_a: DeploymentProfile
    slot_usage:
      key:
        equals_string: vendor.example/deployment/other
''')
            bundle = path / 'bundle.yaml'
            text = f'''id: https://example.org/bundle
name: Bundle
prefixes:
  margo-common: {view.schema.prefixes['margo-common'].prefix_reference}
imports: [{str(self.path)[:-5]}, {str(path / "other")}]
'''
            bundle.write_text(text)
            bundle_view = SchemaView(str(bundle))
            self.assertIn('margo-common:application-description.linkml', bundle_view.imports_closure())
            data = copy.deepcopy(self.example)
            data['metadata']['description'] = None
            schema = generated_schema(bundle)
            self.assertFalse(precise_validator(schema).is_valid(data))
            bundle.write_text(text + "annotations:\n  margo_optional_typed_nonnull: 'false'\n")
            self.assertTrue(precise_validator(generated_schema(bundle)).is_valid(data))
            bundle.write_text(text + "annotations:\n  margo_optional_typed_nonnull: invalid\n")
            with self.assertRaisesRegex(ValueError, 'Invalid margo_optional_typed_nonnull'):
                generated_schema(bundle)

    def test_imported_direct_target_inherits_own_schema_nonnull_policy(self):
        view = SchemaView(str(self.path))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.yaml'
            path.write_text(f'''id: https://example.org/select-bundle
name: SelectBundle
prefixes:
  margo-common: {view.schema.prefixes['margo-common'].prefix_reference}
imports: [{str(self.path)[:-5]}]
''')
            schema = generated_schema(path, 'SelectValidationSchema')
            valid = {'name': 'selection', 'dataType': 'string', 'options': ['a']}
            self.assertTrue(precise_validator(schema).is_valid(valid))
            self.assertFalse(precise_validator(schema).is_valid(dict(valid, allowEmpty=None)))

    def test_open_other_characteristic_inherited_by_subclass(self):
        profile = ROOT / 'model/compose-profile.linkml.yaml'
        schema = generated_schema(profile, 'ComposeOtherCharacteristic')
        value = {'key': 'vendor.example/feature', 'properties': {'novel': 1}, 'vendorField': True}
        self.assertTrue(precise_validator(schema).is_valid(value))
        self.assertTrue(schema['additionalProperties'])

    def test_duplicate_schema_ids_deduplicate_and_conflicting_policies_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = path / 'first.yaml'
            second = path / 'second.yaml'
            first.write_text('''id: https://example.org/shared-id
name: FirstSchema
annotations: {margo_optional_typed_nonnull: "true"}
classes:
  First:
    tree_root: true
''')
            second.write_text('''id: https://example.org/shared-id
name: SecondSchema
annotations: {margo_optional_typed_nonnull: "true"}
classes:
  Second: {}
''')
            bundle = path / 'bundle.yaml'
            bundle.write_text('''id: https://example.org/duplicate-id-bundle
name: DuplicateIdBundle
imports: [first, second]
''')
            self.assertIn('First', generated_schema(bundle)['$defs'])
            second.write_text(second.read_text().replace('"true"', '"false"'))
            with self.assertRaisesRegex(ValueError, 'Conflicting margo_optional_typed_nonnull'):
                generated_schema(bundle)

    def test_inherited_structured_extra_slots_and_extension_patterns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inherited.yaml'
            path.write_text('''id: https://example.org/inherited-extras
name: InheritedExtras
imports: [linkml:types]
classes:
  Parent:
    annotations:
      margo_extensions_pattern: '^x-[a-z][a-z0-9-]*-extensions(?![\\s\\S])'
    extra_slots:
      allowed: true
      range_expression:
        range: string
  Child:
    is_a: Parent
    tree_root: true
''')
            schema = generated_schema(path)
            validator = precise_validator(schema)
            self.assertTrue(validator.is_valid({'x_runtime': 'ok', 'x-acme-extensions': {}}))
            self.assertFalse(validator.is_valid({'other': 3}))
            self.assertFalse(validator.is_valid({'x-BAD-extensions': {}}))

    def test_application_subclass_enforces_dispatch_at_top_and_definition(self):
        view = SchemaView(str(self.path))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'subclass.yaml'
            path.write_text(f'''id: https://example.org/app-subclass
name: ApplicationSubclass
prefixes:
  margo-common: {view.schema.prefixes['margo-common'].prefix_reference}
imports: [{str(self.path)[:-5]}]
classes:
  SpecializedApplication:
    is_a: ApplicationDescription
    tree_root: true
''')
            valid = copy.deepcopy(self.example)
            invalid = copy.deepcopy(valid)
            del invalid['deploymentProfiles'][0]['components'][0]['properties']['repository']
            self.assertEqual(root_class(SchemaView(str(path))), 'SpecializedApplication')
            for target in (None, 'SpecializedApplication'):
                schema = generated_schema(path, target)
                self.assertTrue(precise_validator(schema).is_valid(valid))
                self.assertFalse(precise_validator(schema).is_valid(invalid))
                nullable = copy.deepcopy(valid)
                nullable['metadata']['description'] = None
                self.assertFalse(precise_validator(schema).is_valid(nullable))
                definition = {'$defs': schema['$defs'], '$ref': '#/$defs/SpecializedApplication'}
                self.assertFalse(precise_validator(definition).is_valid(invalid))

    def test_root_selection_rejects_ambiguous_local_or_imported_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'shared.yaml').write_text('''id: https://example.org/shared-roots
name: SharedRoots
classes:
  First:
    tree_root: true
  Second:
    tree_root: true
''')
            bundle = path / 'bundle.yaml'
            bundle.write_text('''id: https://example.org/root-bundle
name: RootBundle
imports: [shared]
''')
            with self.assertRaisesRegex(ValueError, 'Expected exactly one tree_root'):
                root_class(SchemaView(str(bundle)))
            bundle.write_text(bundle.read_text() + '''classes:
  Local:
    tree_root: true
  Another:
    tree_root: true
''')
            with self.assertRaisesRegex(ValueError, 'Expected exactly one tree_root'):
                root_class(SchemaView(str(bundle)))

    def test_rendered_pages_match_checked_in(self):
        for name, output in [('application', 'applications/application-description.md'),
                             ('profile', 'margo-management-interface/compose-profile-characteristics.md')]:
            rendered = render_page(name, ROOT, ROOT / f'model/compose-{name}.linkml.yaml')
            self.assertEqual(rendered, (ROOT / 'specification' / output).read_text())
