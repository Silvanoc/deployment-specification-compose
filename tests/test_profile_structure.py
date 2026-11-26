import shutil
from copy import deepcopy
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import yaml
from linkml_runtime.utils.schemaview import SchemaView

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from common import parse_data
from profile_structure import profile_display, profile_sections, profile_target
from schema_validation import generated_schema

SUBSET = 'DeploymentTechnologyCharacteristics'
DESCRIPTION = 'Characteristics indicating support for a deployment technology.'


class ProfileStructureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo[1]'
        (self.root / 'model').mkdir(parents=True)
        self.examples = self.root / 'model/examples/profile/valid'
        shutil.copytree(ROOT / 'model/examples/profile/valid', self.examples)
        source = ROOT / 'model/compose-profile.linkml.yaml'
        view = SchemaView(str(source))
        source_id = next(i for i in view.imports_closure() if i.endswith('workload-target-characteristics.linkml'))
        location = view.schema_map[source_id].source_file
        if urlsplit(location).scheme in ('http', 'https'):
            with urlopen(location, timeout=30) as response:
                shared = yaml.safe_load(response.read())
        else:
            shared = yaml.safe_load(Path(location).read_text())
        self.model = yaml.safe_load(source.read_text())
        for schema in (shared, self.model):
            for cls in schema.get('classes', {}).values():
                for key in ('margo_profile_example', 'margo_profile_kind', 'margo_profile_characteristic'):
                    cls.get('annotations', {}).pop(key, None)
                if cls.get('annotations') == {}:
                    cls.pop('annotations')
        self.model['imports'] = ['shared' if item == source_id else item for item in self.model['imports']]
        self.model['subsets'] = {SUBSET: {'description': DESCRIPTION}}
        self.model['classes']['ComposeDeploymentCharacteristic']['in_subset'] = [SUBSET]
        self.shared = shared
        self.save()

    def save(self):
        (self.root / 'model/shared.yaml').write_text(yaml.safe_dump(self.shared, sort_keys=False))
        (self.root / 'model/profile.yaml').write_text(yaml.safe_dump(self.model, sort_keys=False))

    def sections(self):
        self.save()
        return profile_sections(SchemaView(str(self.root / 'model/profile.yaml')))

    def add_root(self, name):
        self.model['classes']['ComposeProfile']['slot_usage']['characteristics']['any_of'].append({'range': name})

    def require(self, name):
        self.model['classes']['ComposeProfile']['all_of'].append({'slot_conditions': {'characteristics': {'has_member': {'range': name}}}})

    def test_real_model_six_sections_and_annotation_independence(self):
        sections = self.sections()
        self.assertEqual([(x.target, x.display, x.kind, x.rank) for x in sections], [
            ('ComposeDeploymentCharacteristic', 'ComposeDeploymentCharacteristic', 'deployment', 10),
            ('CpuCharacteristic', 'Cpu', 'characteristic', 20),
            ('MemoryCharacteristic', 'MemoryProperties', 'characteristic', 30),
            ('StorageCharacteristic', 'StorageProperties', 'characteristic', 40),
            ('PeripheralsCharacteristic', 'Peripheral', 'characteristic', 50),
            ('InterfacesCharacteristic', 'Interface', 'characteristic', 60),
        ])
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertIsNotNone(view.get_subset(SUBSET))
        self.assertFalse(any(k in c.annotations for c in view.all_classes().values() for k in ('margo_profile_example', 'margo_profile_kind', 'margo_profile_characteristic')))
        self.assertEqual([view.get_class(s.display).title for s in sections],
                         ['Compose Deployment Technology Support', 'CPUs', 'Memory', 'Storage', 'Peripherals', 'Interfaces'])
        self.assertEqual([view.induced_slot('key', s.target).equals_string for s in sections],
                         ['margo.org/deployment/compose', 'margo.org/resource/cpu', 'margo.org/resource/memory',
                          'margo.org/resource/storage', 'margo.org/peripherals', 'margo.org/interfaces'])
        for section in sections:
            self.assertTrue(view.get_class(section.display).title)
            self.assertEqual(view.get_class(section.display).rank, section.rank)
            self.assertTrue(view.induced_slot('key', section.target).equals_string)
        self.assertNotIn('ComposeOtherCharacteristic', [s.target for s in sections])
        self.assertIsNone(profile_display(view, 'ComposeOtherCharacteristic'))
        self.assertEqual(profile_display(view, 'CpuCharacteristic'), 'Cpu')
        with self.assertRaisesRegex(ValueError, 'Unknown profile characteristic class'):
            profile_display(view, 'MissingClass')

    def test_novel_fixture_not_added_to_profile_union(self):
        self.model['classes']['Novel'] = {'title': 'Novel', 'rank': 25, 'attributes': {'key': {'range': 'string', 'equals_string': 'example/novel'}}}
        (self.examples / 'Novel-custom.json').write_text('{"key":"example/novel"}\n')
        sections = self.sections()
        self.assertFalse(any(x.target == 'Novel' for x in sections))
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertNotIn('Novel', [x.range for x in view.induced_slot('characteristics', 'ComposeProfile').any_of])

    def test_mixin_slots_and_subset_inheritance(self):
        classes = self.model['classes']
        classes['SupportMixin'] = {'mixin': True, 'in_subset': [SUBSET], 'attributes': {
            'key': {'range': 'string', 'equals_string': 'example/mixin'},
            'properties': {'range': 'MixinProperties', 'inlined': True}}}
        classes['MixinTarget'] = {'mixins': ['SupportMixin']}
        classes['MixinProperties'] = {'title': 'Mixin Properties', 'rank': 25}
        self.add_root('MixinTarget')
        classes['RequiredMixin'] = {'mixin': True, 'all_of': [{'slot_conditions': {'characteristics': {'has_member': {'range': 'MixinTarget'}}}}]}
        classes['ComposeProfile']['mixins'] = ['RequiredMixin']
        section = next(x for x in self.sections() if x.target == 'MixinTarget')
        self.assertEqual((section.display, section.kind, section.rank), ('MixinProperties', 'deployment', 25))

    def test_cycle_and_ambiguous_display(self):
        classes = self.model['classes']
        classes['CyclicTarget'] = {'attributes': {'key': {'range': 'string', 'equals_string': 'example/cycle'}, 'properties': {'range': 'CycleA', 'inlined': True}}}
        classes['CycleA'] = {'attributes': {'next': {'range': 'CycleB', 'inlined': True}}}
        classes['CycleB'] = {'title': 'Cycle', 'rank': 25, 'attributes': {'next': {'range': 'CycleA', 'inlined': True}}}
        self.add_root('CyclicTarget')
        classes['ComposeProfile']['all_of'].append({'slot_conditions': {'characteristics': {'has_member': {'range': 'CyclicTarget'}}}})
        (self.examples / 'CyclicTarget-example.json').write_text('{"key":"example/cycle"}\n')
        self.assertEqual(next(x for x in self.sections() if x.target == 'CyclicTarget').display, 'CycleB')
        classes['CycleA'].update(title='Another', rank=26)
        with self.assertRaisesRegex(ValueError, 'Ambiguous display'):
            self.sections()
        classes['CycleA'].pop('title')
        classes['CycleA'].pop('rank')
        classes['CycleB'].pop('title')
        classes['CycleB'].pop('rank')
        with self.assertRaisesRegex(ValueError, 'Missing display'):
            self.sections()

    def test_subset_definitions(self):
        self.shared['subsets'] = {SUBSET: {'description': DESCRIPTION}}
        self.assertEqual(len(self.sections()), 6)
        self.shared['subsets'][SUBSET]['description'] = 'Different meaning.'
        with self.assertRaisesRegex(ValueError, 'Conflicting profile subset'):
            self.sections()
        self.shared.pop('subsets')
        self.model.pop('subsets')
        with self.assertRaisesRegex(ValueError, 'Missing profile subset'):
            self.sections()

    def test_subset_and_removed_annotations_do_not_change_instance_schema(self):
        after_model, after_shared = deepcopy(self.model), deepcopy(self.shared)
        before_model, before_shared = deepcopy(after_model), deepcopy(after_shared)
        before_model.pop('subsets')
        before_model['classes']['ComposeDeploymentCharacteristic'].pop('in_subset')
        before_model['classes']['ComposeDeploymentCharacteristic'].setdefault('annotations', {}).update({
            'margo_profile_kind': 'deployment', 'margo_profile_characteristic': 'ComposeDeploymentCharacteristic'})
        before_shared['classes']['Cpu'].setdefault('annotations', {}).update({
            'margo_profile_kind': 'characteristic', 'margo_profile_characteristic': 'CpuCharacteristic'})
        self.model, self.shared = before_model, before_shared
        self.save()
        schema = self.root / 'model/profile.yaml'
        before = generated_schema(schema)
        self.model, self.shared = after_model, after_shared
        self.save()
        self.assertEqual(before, generated_schema(schema))

    def test_exactly_one_of_and_implicit_inlining(self):
        classes = self.model['classes']
        classes['UnionTarget'] = {'attributes': {
            'key': {'range': 'string', 'equals_string': 'example/union'},
            'properties': {'range': 'UnionContainer', 'inlined': True}}}
        classes['UnionContainer'] = {'attributes': {
            'choice': {'exactly_one_of': [{'range': 'UnionDisplay'}, {'range': 'string'}]}}}
        classes['UnionDisplay'] = {'title': 'Union display', 'rank': 25}
        usage = classes['ComposeProfile']['slot_usage']['characteristics']
        usage['exactly_one_of'] = [{'range': 'UnionTarget'}]
        self.require('UnionTarget')
        section = next(x for x in self.sections() if x.target == 'UnionTarget')
        self.assertEqual((section.display, section.rank), ('UnionDisplay', 25))

    def test_duplicate_rank_fails(self):
        self.model['classes']['Novel'] = {'title': 'Novel', 'rank': 20, 'attributes': {'key': {'range': 'string', 'equals_string': 'example/novel'}}}
        self.add_root('Novel')
        self.require('Novel')
        with self.assertRaisesRegex(ValueError, 'Duplicate profile display or rank'):
            self.sections()

    def test_duplicate_required_display_fails(self):
        self.model['classes']['Alias'] = {'attributes': {
            'key': {'range': 'string', 'equals_string': 'vendor/alias'},
            'properties': {'range': 'CpuProperties', 'inlined': True}}}
        self.add_root('Alias')
        self.require('Alias')
        with self.assertRaisesRegex(ValueError, 'Duplicate profile display or rank'):
            self.sections()

    def test_invalid_schema_fails_closed(self):
        self.add_root('UnknownTarget')
        self.require('UnknownTarget')
        with self.assertRaisesRegex(ValueError, 'Missing has_member class'):
            self.sections()
        self.model['classes']['ComposeProfile']['all_of'].pop()
        self.model['classes']['ComposeProfile']['tree_root'] = False
        with self.assertRaisesRegex(ValueError, 'tree_root'):
            self.sections()

    def test_target_existing_examples_and_unknown(self):
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        for path in self.examples.glob('*.json'):
            target = path.name.split('-')[0]
            data = parse_data(path.read_text(), path.suffix)
            self.assertEqual(profile_target(view, data), target)
        self.assertEqual(profile_target(view, {'characteristics': []}), 'ComposeProfile')
        for data in (None, {}, {'key': 1}):
            with self.assertRaises(ValueError):
                profile_target(view, data)
        for key in ('unknown/vendor', 'margo.org/resource/cpu\n'):
            with self.assertRaisesRegex(ValueError, 'matches 0'):
                profile_target(view, {'key': key})

    def test_target_subclass_and_mixin_exact_key(self):
        classes = self.model['classes']
        classes['KeyMixin'] = {'mixin': True, 'attributes': {'key': {'range': 'string', 'equals_string': 'example/mixin-key'}}}
        classes['MixinDerived'] = {'mixins': ['KeyMixin']}
        self.add_root('MixinDerived')
        self.require('MixinDerived')
        classes['DerivedDeployment'] = {'is_a': 'ComposeDeploymentCharacteristic', 'slot_usage': {
            'key': {'equals_string': 'example/derived'}}}
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertEqual(profile_target(view, {'key': 'example/mixin-key'}), 'MixinDerived')
        with self.assertRaisesRegex(ValueError, 'matches 0'):
            profile_target(view, {'key': 'example/derived'})

    def test_target_required_pattern_and_optional_fallback(self):
        classes = self.model['classes']
        classes['SpecificPattern'] = {'title': 'Specific', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'pattern': 'vendor/specific/.*'}}}
        classes['OpenPattern'] = {'attributes': {'key': {'range': 'string', 'pattern': 'vendor/open/.*'}}}
        self.add_root('SpecificPattern')
        self.add_root('OpenPattern')
        self.require('SpecificPattern')
        (self.examples / 'OpenPattern-one.yaml').write_text('key: vendor/open/one\nvalue: 1\n')
        (self.examples / 'OpenPattern-two.json').write_text('{"key":"vendor/open/two","value":true}\n')
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertEqual(profile_target(view, {'key': 'vendor/specific/a'}), 'SpecificPattern')
        with self.assertRaisesRegex(ValueError, 'matches 0'):
            profile_target(view, {'key': 'vendor/open/one', 'value': 1})

    def test_target_overlaps_are_ambiguous(self):
        self.model['classes']['PatternTarget'] = {'title': 'Pattern', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'pattern': 'margo.org/resource/cpu'}}}
        self.add_root('PatternTarget')
        self.require('PatternTarget')
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        cpu = self.examples / 'CpuCharacteristic-001.json'
        data = parse_data(cpu.read_text(), cpu.suffix)
        with self.assertRaisesRegex(ValueError, 'matches 2'):
            profile_target(view, data)

    def test_optional_fixture_does_not_affect_target(self):
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        (self.examples / 'Unknown-test.json').write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'matches 0'):
            profile_target(view, {'key': 'unknown'})

    def test_required_pattern_needs_display_and_example(self):
        classes = self.model['classes']
        classes['RequiredPattern'] = {'attributes': {'key': {'range': 'string', 'pattern': 'vendor/.*'}}}
        self.add_root('RequiredPattern')
        classes['ComposeProfile']['all_of'].append({'slot_conditions': {
            'characteristics': {'has_member': {'range': 'RequiredPattern'}}}})
        with self.assertRaisesRegex(ValueError, 'Missing display.*RequiredPattern'):
            self.sections()
        classes['RequiredPattern'].update(title='Required pattern', rank=25)
        self.assertIn('RequiredPattern', [s.target for s in self.sections()])
        classes['ComposeProfile']['all_of'][-1]['slot_conditions']['characteristics']['has_member']['pattern'] = 'x'
        with self.assertRaisesRegex(ValueError, 'Unsupported has_member range'):
            self.sections()

    def test_direct_slot_membership_fails_closed_for_documentation(self):
        slot = self.model['classes']['ComposeProfile']['slot_usage']['characteristics']
        slot['has_member'] = {'range_expression': {'slot_conditions': {'key': {'equals_string': 'vendor/required'}}}}
        with self.assertRaisesRegex(ValueError, 'Slot-level has_member .* unsupported for profile documentation'):
            self.sections()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        with self.assertRaisesRegex(ValueError, 'Slot-level has_member .* unsupported for profile documentation'):
            profile_target(view, {'key': 'margo.org/resource/cpu'})

    def test_optional_exact_union_member_without_display_is_not_rendered(self):
        self.model['classes']['OptionalCharacteristic'] = {'attributes': {
            'key': {'range': 'string', 'equals_string': 'vendor/optional'}}}
        self.add_root('OptionalCharacteristic')
        self.assertNotIn('OptionalCharacteristic', [s.target for s in self.sections()])
        (self.examples / 'OptionalCharacteristic-test.json').write_text('{"key":"vendor/optional"}\n')
        (self.examples / 'ComposeOtherCharacteristic-test.json').write_text('{"key":"vendor/other", "properties":{}}\n')
        targets = [s.target for s in self.sections()]
        self.assertNotIn('OptionalCharacteristic', targets)
        self.assertNotIn('ComposeOtherCharacteristic', targets)


if __name__ == '__main__':
    unittest.main()
