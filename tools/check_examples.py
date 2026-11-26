"""Check documented examples, valid fixtures, and class-specific counterexamples."""
import re
import shlex
import sys
from pathlib import Path

from linkml_runtime.utils.schemaview import SchemaView
from linkml.validator.report import Severity

from common import ROOT, markdown_fences, parse_data, parse_oras_annotations, semantic_equal
from schema_validation import ValidationEngine
from registry_examples import make_registry_validator, check_registry_context
from profile_examples import ProfileValidator
from profile_structure import profile_target as resolve_profile_target

GROUPS = ("application", "profile", "registry")
DOCUMENTS = (
    ("applications/application-description.md", {"yaml", "yml"}, "application", ".yaml", "ApplicationDescription"),
    ("applications/application-registry.md", {"yaml", "yml"}, "application", ".yaml", "ComposeComponent"),
    ("margo-management-interface/compose-profile-characteristics.md", {"json"}, "profile", ".json", None),
    ("applications/application-registry.md", {"bash"}, "registry", "oras", "ComposeComponentAnnotations"),
)
_UNPARSED = object()


class _RegistryFixtureValidator:
    def __init__(self, schema, view):
        self.schema = schema
        self.view = view
        self.validators = {}

    def validate(self, schema, target, data):
        if target not in self.validators:
            self.validators[target] = make_registry_validator(self.schema)
        report = self.validators[target].validate(data, target)
        notes = []
        if isinstance(data, dict):
            for slot in self.view.class_induced_slots(target):
                if slot.name not in data or not slot.annotations or 'margo_oci_tag_encoding' not in slot.annotations:
                    continue
                if slot.annotations['margo_oci_tag_encoding'].value != 'semver_plus_as_underscore':
                    raise ValueError(f'Unsupported OCI tag encoding on {slot.name}')
                notes.append('External OCI comparison not performed for: tag, revision')
                break
        return [r.message for r in report.results if r.severity in (Severity.ERROR, Severity.FATAL)], notes


def profile_target(data, schema):
    schema = Path(schema)
    return resolve_profile_target(SchemaView(str(schema)), data)


def registry_component(fragment):
    if not isinstance(fragment, dict) or set(fragment) != {"components"} or not isinstance(fragment["components"], list) or len(fragment["components"]) != 1:
        raise ValueError("Registry fragment must provide exactly one component in a components list")
    return fragment["components"][0]


def registry_context(fence, document):
    tokens = shlex.split(re.sub(r"\\\r?\n", " ", fence), comments=True)
    reference = tokens[2] if len(tokens) > 2 and tokens[:2] == ["oras", "push"] else ""
    parent, slash, last = reference.rpartition("/")
    name, colon, tag = last.rpartition(":")
    if not slash or not parent or not colon or not name or not tag or "@" in last:
        raise ValueError("Documented ORAS reference must include a repository and tag")
    repository = f"{parent}/{name}"
    components = []
    for text in markdown_fences(document, {"yaml", "yml"}):
        fragment = parse_data(text, ".yaml")
        components.append(registry_component(fragment))
    if len(components) != 1 or not isinstance(components[0], dict) or not isinstance(components[0].get("properties"), dict):
        raise ValueError("Registry fragment must provide exactly one component repository/revision")
    props = components[0]["properties"]
    uri, revision = props.get("repository"), props.get("revision")
    if not isinstance(uri, str) or not uri.startswith("oci://") or not uri[6:] or not isinstance(revision, str) or not revision:
        raise ValueError("Registry fragment must provide one OCI repository and revision")
    if uri[6:] != repository:
        raise ValueError(f"Registry repository mismatch: {uri[6:]!r} != {repository!r}")
    return tag, revision


def validate(schema, target, fixture, negative=False, engine=None, data=_UNPARSED):
    if data is _UNPARSED:
        data = parse_data(fixture.read_text(encoding="utf-8"), fixture.suffix)
    errors, notes = engine.validate(schema, target, data)
    if negative:
        if not errors:
            raise ValueError(f"Expected validation failure for {fixture}")
    elif errors:
        raise ValueError(f"Validation failed for {fixture}: {'; '.join(errors)}")
    elif notes:
        print(f"{fixture}: {'; '.join(notes)}")


def check(root=ROOT):
    engine = ValidationEngine()
    try:
        return _check(root, engine)
    finally:
        engine.close()


def _check(root, engine):
    valid_data = {}
    for group in GROUPS:
        schema = root / "model" / f"compose-{group}.linkml.yaml"
        if schema.is_symlink() or not schema.is_file():
            raise ValueError(f"Missing or unsafe schema: {schema}")
        targets = {}
        registry_view = SchemaView(str(schema)) if group == 'registry' else None
        registry_validator = _RegistryFixtureValidator(schema, registry_view) if registry_view else None
        profile_validator = ProfileValidator(schema) if group == 'profile' else None
        valid_data[group] = {}
        for kind in ("valid", "invalid"):
            directory = root / "model/examples" / group / kind
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError(f"Missing or unsafe fixture directory: {directory}")
            fixtures = sorted(directory.iterdir())
            if not fixtures:
                raise ValueError(f"No {kind} examples in {directory}")
            for fixture in fixtures:
                match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)-([A-Za-z0-9][A-Za-z0-9_-]*)\.(json|yaml|yml)", fixture.name)
                if not match or fixture.is_symlink() or not fixture.is_file():
                    raise ValueError(f"Invalid fixture path: {fixture}")
                target = match.group(1)
                data = parse_data(fixture.read_text(encoding="utf-8"), fixture.suffix)
                if registry_view:
                    if registry_view.get_class(target) is None:
                        raise ValueError(f"Unknown registry target class: {target}")
                validate(schema, target, fixture, negative=kind == "invalid",
                         engine=registry_validator or profile_validator or engine, data=data)
                targets.setdefault(target, set()).add(kind)
                if kind == "valid":
                    valid_data[group].setdefault(target, []).append(data)
        for target, kinds in targets.items():
            if kinds != {"valid", "invalid"}:
                raise ValueError(f"Both positive and negative examples required for {group}/{target}")
        print(f"Checked {group}")
    for path, languages, group, kind, target in DOCUMENTS:
        document = root / "specification" / path
        fences = markdown_fences(document.read_text(encoding="utf-8"), languages)
        if not fences:
            raise ValueError(f"No {languages} examples in {document}")
        for fence in fences:
            data = parse_oras_annotations(fence) if kind == "oras" else parse_data(fence, kind)
            if target == "ComposeComponent":
                data = registry_component(data)
            intended = target or profile_target(data, root / "model/compose-profile.linkml.yaml")
            if not any(semantic_equal(data, fixture) for fixture in valid_data[group].get(intended, [])):
                raise ValueError(f"Example drift in {document} ({group}/{intended})")
            if kind == "oras":
                tag, revision = registry_context(fence, document.read_text(encoding="utf-8"))
                check_registry_context(root / "model/compose-registry.linkml.yaml", intended, data, tag, revision)


if __name__ == "__main__":
    try:
        check()
    except (ValueError, OSError, TypeError) as error:
        print(f"Example check failed: {error}", file=sys.stderr)
        sys.exit(1)
