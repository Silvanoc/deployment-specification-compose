"""Native LinkML validation for profile examples, with model-specific gaps."""
from linkml.generators.jsonschemagen import JsonSchemaGenerator
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml.validator.report import Severity
from linkml_runtime.dumpers import json_dumper
from linkml_runtime.utils.schemaview import SchemaView
import jsonschema

from common import semantic_equal
from schema_validation import (annotation, disallow_null, precise_validator,
                               reject_unverified_numeric_constraints, required_memberships)


class _Context:
    def __init__(self, base, validator):
        self.base, self.validator = base, validator

    def __getattr__(self, name):
        return getattr(self.base, name)

    def json_schema_validator(self, **kwargs):
        return self.validator


def _unique_by(validator, key, instance, schema):
    if not isinstance(instance, list):
        return
    strings, other = set(), []
    for item in instance:
        if not isinstance(item, dict) or key not in item:
            continue
        value = item[key]
        if isinstance(value, str):
            duplicate = value in strings
            strings.add(value)
        else:
            duplicate = any(semantic_equal(value, previous) for previous in other)
            other.append(value)
        if duplicate:
            yield jsonschema.ValidationError(f'duplicate {key}: {value!r}')


class ProfileStructure(JsonschemaValidationPlugin):
    """Use a fresh generator per target; adapt only profile metadata gaps."""
    def __init__(self, view):
        super().__init__(closed=True)
        self.view = view
        self.cache = {}
        self.schemas = {}
        reject_unverified_numeric_constraints(view)

    def _schema(self, target):
        if target in self.schemas:
            return self.schemas[target]
        if not self.view.get_class(target):
            raise ValueError(f"Unknown profile target class: {target}")
        # Match the existing schema-level policy of the target or its owners.
        nonnull = annotation(self.view.schema, 'margo_optional_typed_nonnull')
        if nonnull is None:
            for name in self.view.class_ancestors(target):
                owner = self.view.get_class(name).from_schema
                schemas = [s for s in self.view.schema_map.values() if s.id == owner]
                if not schemas:
                    raise ValueError(f"Cannot identify schema defining class {name}")
                policies = {annotation(s, 'margo_optional_typed_nonnull') for s in schemas} - {None}
                if len(policies) > 1:
                    raise ValueError(f"Conflicting nonnull policies for {owner}")
                if policies:
                    nonnull = next(iter(policies))
                    break
        if nonnull not in (None, 'true', 'false'):
            raise ValueError('Invalid margo_optional_typed_nonnull annotation')
        schema = JsonSchemaGenerator(self.view.schema, top_class=target, preserve_names=True,
            include_range_class_descendants=True, not_closed=False,
            include_null=nonnull != 'true').generate()
        defs = schema.get('$defs')
        if not isinstance(defs, dict) or target not in defs:
            raise ValueError(f"Missing generated profile definition: {target}")
        for name, cls in self.view.all_classes().items():
            if name not in defs:
                if name in self.view.all_classes(imports=False):
                    raise ValueError(f"Missing generated profile definition: {name}")
                continue
            ancestors = self.view.class_ancestors(name)
            patterns = {annotation(self.view.get_class(p), 'margo_extensions_pattern') for p in ancestors} - {None}
            if len(patterns) > 1:
                raise ValueError(f"Conflicting extension patterns: {name}")
            extra = next((self.view.get_class(p).extra_slots for p in ancestors
                          if self.view.get_class(p).extra_slots is not None), None)
            memberships = required_memberships(self.view, name, defs)
            for node in [defs[name]] + ([schema] if name == target else []):
                props = node.get('properties', {})
                if not isinstance(props, dict):
                    raise ValueError(f"Missing generated properties: {name}")
                for slot_name, member_class in memberships:
                    prop = props.get(slot_name)
                    if not isinstance(prop, dict) or not isinstance(prop.get('items'), dict):
                        raise ValueError(f'Unsupported has_member array: {name}.{slot_name}')
                    if prop.get('type') in (['array', 'null'], ['null', 'array']):
                        prop['type'] = 'array'
                    elif prop.get('type') != 'array':
                        raise ValueError(f'Unsupported has_member type: {name}.{slot_name}')
                    required = node.setdefault('required', [])
                    if slot_name not in required:
                        required.append(slot_name)
                    refs = [{'$ref': f'#/$defs/{child}'} for child in self.view.class_descendants(member_class)]
                    member_schema = refs[0] if len(refs) == 1 else {'anyOf': refs}
                    member_slots = {s.name: s for s in self.view.class_induced_slots(member_class)}
                    key_slot = member_slots.get('key')
                    if key_slot and key_slot.equals_string is not None:
                        member_schema = {'allOf': [member_schema, {'properties': {'key': {'const': key_slot.equals_string}}, 'required': ['key']}]}
                    prop.setdefault('allOf', []).append({'contains': member_schema})
                if patterns:
                    pattern = next(iter(patterns))
                    node.setdefault('patternProperties', {})[pattern] = {'type': 'object'}
                    props.pop('x_placeholder_extensions', None)
                    if 'required' in node:
                        node['required'] = [p for p in node['required'] if p != 'x_placeholder_extensions']
                    if extra and extra.allowed:
                        node['additionalProperties'] = True
                        node.setdefault('propertyNames', {}).setdefault('allOf', []).append({
                            'if': {'pattern': r'^x[-_].*[-_]extensions(?![\s\S])'},
                            'then': {'pattern': pattern}})
                    else:
                        node['additionalProperties'] = False
                if extra is not None:
                    if extra.allowed and extra.range_expression and extra.range_expression.range:
                        if extra.range_expression.range != 'string':
                            raise ValueError(f"Unsupported extra_slots range: {name}")
                        node['additionalProperties'] = {'type': 'string'}
                    else:
                        node['additionalProperties'] = bool(extra.allowed)
                for slot in self.view.class_induced_slots(name):
                    if slot.has_member is not None:
                        direct = props.get(slot.name)
                        member = json_dumper.to_dict(slot.has_member)
                        if ('range' in member or not slot.multivalued or not isinstance(direct, dict)
                                or 'contains' not in direct):
                            raise ValueError(f'Unsupported generated slot has_member: {name}.{slot.name}')
                    unique = annotation(slot, 'margo_unique_by')
                    if unique is None:
                        for parent in ancestors:
                            if parent == name:
                                continue
                            inherited = next((s for s in self.view.class_induced_slots(parent)
                                              if s.name == slot.name), None)
                            if inherited is not None:
                                unique = annotation(inherited, 'margo_unique_by')
                                if unique is not None:
                                    break
                    if unique is not None:
                        if not isinstance(unique, str) or not unique or not slot.multivalued or slot.name not in props:
                            raise ValueError(f'Invalid unique-by metadata: {name}.{slot.name}')
                        props[slot.name]['x-profile-unique-by'] = unique
                    null = annotation(slot, 'margo_disallow_null')
                    if null is not None:
                        if null != 'true' or slot.name not in props:
                            raise ValueError(f"Invalid nonnull metadata: {name}.{slot.name}")
                        disallow_null(props[slot.name], f'{name}.{slot.name}')
        self.schemas[target] = schema
        return schema

    def process(self, instance, context):
        target = context.target_class
        if target not in self.cache:
            precise = precise_validator(self._schema(target))
            extended = jsonschema.validators.extend(type(precise), {'x-profile-unique-by': _unique_by})
            self.cache[target] = extended(precise.schema, format_checker=precise.format_checker)
        yield from super().process(instance, _Context(context, self.cache[target]))


class ProfileValidator:
    def __init__(self, schema):
        self.schema = str(schema)
        self.view = SchemaView(self.schema)
        self.view.all_classes()
        structure = ProfileStructure(self.view)
        self.validator = Validator(self.view.schema, validation_plugins=[structure])

    def validate(self, schema, target, data):
        if not self.view.get_class(target):
            raise ValueError(f'Unknown profile target class: {target}')
        report = self.validator.validate(data, target)
        return [r.message for r in report.results if r.severity in (Severity.ERROR, Severity.FATAL)], []
