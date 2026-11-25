"""Native LinkML registry-example validation plus three documented gaps.

LinkML's JSON Schema plugin currently omits typed extra slots, explicit-null
annotations and cross-slot equality. These are additive checks, not a second
schema validator.
"""
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin, ValidationPlugin
from linkml.validator.report import Severity, ValidationResult
from linkml_runtime.utils.schemaview import SchemaView


def _annotation(element, name):
    entry = element.annotations[name] if element.annotations and name in element.annotations else None
    return entry.value if entry is not None else None


class RegistryConstraints(ValidationPlugin):
    """Enforce the three registry constraints missing from LinkML 1.12.1."""

    def process(self, instance, context):
        if not isinstance(instance, dict):
            return
        view = context.schema_view
        cls = view.get_class(context.target_class)
        if cls is None:
            raise ValueError(f"Unknown registry target class: {context.target_class}")
        slots = {slot.name: slot for slot in view.class_induced_slots(cls.name)}
        extra = next((view.get_class(name).extra_slots for name in view.class_ancestors(cls.name)
                      if view.get_class(name).extra_slots is not None), None)
        if extra and extra.allowed and extra.range_expression and extra.range_expression.range:
            if extra.range_expression.range != 'string':
                raise ValueError('Unsupported registry extra slot range')
            for name in instance.keys() - slots.keys():
                if not isinstance(instance[name], str):
                    yield ValidationResult(type='registry constraints', severity=Severity.ERROR,
                        message=f'{name} must be a string', instance=instance, instantiates=cls.name)
        for name, slot in slots.items():
            nonnull = _annotation(slot, 'margo_disallow_null')
            equal = _annotation(slot, 'margo_equal_to')
            if nonnull is not None and nonnull != 'true':
                raise ValueError(f'Invalid disallow-null annotation: {name}')
            if equal is not None and (not isinstance(equal, str) or equal not in slots):
                raise ValueError(f'Invalid equality annotation: {name}')
            if name in instance and nonnull and instance[name] is None:
                yield ValidationResult(type='registry constraints', severity=Severity.ERROR,
                    message=f'{name} must not be null', instance=instance, instantiates=cls.name)
            if name in instance and equal and equal in instance and instance[name] != instance[equal]:
                yield ValidationResult(type='registry constraints', severity=Severity.ERROR,
                    message=f'{name} must equal {equal}', instance=instance, instantiates=cls.name)


def make_registry_validator(schema):
    """Use LinkML's validator plus the three narrowly scoped registry checks."""
    return Validator(str(schema), validation_plugins=[JsonschemaValidationPlugin(), RegistryConstraints()])


def check_registry_context(schema, target, data, tag, revision):
    """Compare a validated document's modeled OCI version to external sources."""
    view = SchemaView(str(schema))
    for slot in view.class_induced_slots(target):
        encoding = _annotation(slot, 'margo_oci_tag_encoding')
        if encoding is None or slot.name not in data:
            continue
        if encoding != 'semver_plus_as_underscore' or not isinstance(data[slot.name], str):
            raise ValueError(f'Unsupported OCI tag encoding on {slot.name}')
        expected = data[slot.name].replace('+', '_')
        for label, supplied in (('tag', tag), ('revision', revision)):
            if supplied != expected:
                raise ValueError(f'Registry example context invalid: {label} must be {expected!r}, got {supplied!r}')
