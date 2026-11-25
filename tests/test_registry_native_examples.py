import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml.validator.report import Severity

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from common import parse_data
from registry_examples import make_registry_validator, check_registry_context


class RegistryNativeExamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ROOT / 'model/compose-registry.linkml.yaml'
        cls.examples = ROOT / 'model/examples/registry'
        cls.validator = make_registry_validator(cls.schema)
        fixture = cls.examples / 'valid/ComposeComponentAnnotations-001.json'
        cls.fixture = parse_data(fixture.read_text(), fixture.suffix)

    def test_native_validator_and_three_additive_constraints(self):
        self.assertIsInstance(self.validator, Validator)
        plain = Validator(str(self.schema), validation_plugins=[JsonschemaValidationPlugin()])
        for field, value in [('vendor.custom', 4), ('org.opencontainers.image.title', None),
                             ('org.opencontainers.image.version', '2.0.0')]:
            data = dict(self.fixture, **{field: value})
            self.assertFalse(plain.validate(data, 'ComposeComponentAnnotations').results, field)
            errors = self.validator.validate(data, 'ComposeComponentAnnotations').results
            self.assertTrue(any(r.severity == Severity.ERROR for r in errors), field)
        data = dict(self.fixture, **{'vendor.custom': 'value', 'org.opencontainers.image.title': ''})
        self.assertFalse(self.validator.validate(data, 'ComposeComponentAnnotations').results)

    def test_real_json_fixtures(self):
        for kind in ('valid', 'invalid'):
            for path in sorted((self.examples / kind).glob('*.json')):
                with self.subTest(path=path):
                    data = parse_data(path.read_text(), path.suffix)
                    target = path.stem.split('-')[0]
                    errors = self.validator.validate(data, target).results
                    self.assertEqual(bool(errors), kind == 'invalid')

    def test_large_integer_rejected_without_parser_limit(self):
        number = '9' * 4301
        text = json.dumps(self.fixture, default=str)[:-1] + ', "vendor.large": ' + number + '}'
        data = parse_data(text, '.json')
        errors = self.validator.validate(data, 'ComposeComponentAnnotations').results
        self.assertTrue(any(r.severity == Severity.ERROR and 'vendor.large' in r.message for r in errors))

    def test_yaml_and_yml_use_strict_parser_then_native_validator(self):
        for suffix in ('.yaml', '.yml'):
            with self.subTest(suffix=suffix):
                valid = parse_data('org.margo.component.type: compose\norg.margo.component.version: 1.0.0\n', suffix)
                invalid = parse_data('org.margo.component.type: helm\norg.margo.component.version: 1.0.0\n', suffix)
                self.assertFalse(self.validator.validate(valid, 'ComposeComponentAnnotations').results)
                self.assertTrue(self.validator.validate(invalid, 'ComposeComponentAnnotations').results)
                with self.assertRaisesRegex(ValueError, 'Duplicate YAML key'):
                    parse_data('x: 1\nx: 2\n', suffix)

    def test_plugin_exception_propagates(self):
        with patch.object(JsonschemaValidationPlugin, 'process', side_effect=RuntimeError('generator crashed')):
            with self.assertRaisesRegex(RuntimeError, 'generator crashed'):
                self.validator.validate(self.fixture, 'ComposeComponentAnnotations')

    def test_unknown_target_is_not_a_validation_error_result(self):
        with self.assertRaises(Exception):
            self.validator.validate(self.fixture, 'NonexistentClass')

    def test_external_version_comparison(self):
        check_registry_context(self.schema, 'ComposeComponentAnnotations', self.fixture, '1.0.0', '1.0.0')
        build = dict(self.fixture, **{'org.margo.component.version': '1.0.0+build.7'})
        check_registry_context(self.schema, 'ComposeComponentAnnotations', build, '1.0.0_build.7', '1.0.0_build.7')
        for tag, revision in [('2.0.0', '1.0.0'), ('1.0.0', '2.0.0')]:
            with self.assertRaisesRegex(ValueError, 'Registry example context invalid'):
                check_registry_context(self.schema, 'ComposeComponentAnnotations', self.fixture, tag, revision)
