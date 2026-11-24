"""Check documented examples, valid fixtures, and class-specific counterexamples."""
import re
import shlex
import sys

from linkml_runtime.utils.schemaview import SchemaView

from common import ROOT, markdown_fences, parse_data, parse_oras_annotations, semantic_equal
from schema_validation import ValidationEngine

GROUPS = ("application", "profile", "registry")
DOCUMENTS = (
    ("applications/application-description.md", {"yaml", "yml"}, "application", ".yaml", "ApplicationDescription"),
    ("applications/application-registry.md", {"yaml", "yml"}, "application", ".yaml", "ComponentListFragment"),
    ("margo-management-interface/compose-profile-characteristics.md", {"json"}, "profile", ".json", None),
    ("applications/application-registry.md", {"bash"}, "registry", "oras", "ComposeComponentAnnotations"),
)
PROFILE_CLASSES = ("ComposeDeploymentCharacteristic", "CpuCharacteristic", "MemoryCharacteristic", "StorageCharacteristic", "PeripheralsCharacteristic", "InterfacesCharacteristic")


def profile_target(data, schema):
    if not isinstance(data, dict):
        raise ValueError("Profile example must be an object")
    if "characteristics" in data:
        return "ComposeProfile"
    key = data.get("key")
    if not isinstance(key, str):
        raise ValueError("Profile characteristic must have a string key")
    view = SchemaView(str(schema))
    matches = []
    for name in PROFILE_CLASSES:
        slot = view.induced_slot("key", name)
        if not slot or not slot.pattern:
            raise ValueError(f"Missing key pattern for {name}")
        if re.fullmatch(slot.pattern, key):
            matches.append(name)
    if len(matches) != 1:
        raise ValueError(f"Profile key {key!r} matches {len(matches)} characteristic classes")
    return matches[0]


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
        if not isinstance(fragment, dict) or not isinstance(fragment.get("components"), list):
            raise ValueError("Registry fragment must provide one component repository/revision")
        components.extend(fragment["components"])
    if len(components) != 1 or not isinstance(components[0], dict) or not isinstance(components[0].get("properties"), dict):
        raise ValueError("Registry fragment must provide exactly one component repository/revision")
    props = components[0]["properties"]
    uri, revision = props.get("repository"), props.get("revision")
    if not isinstance(uri, str) or not uri.startswith("oci://") or not uri[6:] or not isinstance(revision, str) or not revision:
        raise ValueError("Registry fragment must provide one OCI repository and revision")
    if uri[6:] != repository:
        raise ValueError(f"Registry repository mismatch: {uri[6:]!r} != {repository!r}")
    return tag, revision


def validate(schema, target, fixture, negative=False, engine=None):
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
                validate(schema, target, fixture, negative=kind == "invalid", engine=engine)
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
            intended = target or profile_target(data, root / "model/compose-profile.linkml.yaml")
            if not any(semantic_equal(data, fixture) for fixture in valid_data[group].get(intended, [])):
                raise ValueError(f"Example drift in {document} ({group}/{intended})")
            if kind == "oras":
                tag, revision = registry_context(fence, document.read_text(encoding="utf-8"))
                errors, notes = engine.validate(root / "model/compose-registry.linkml.yaml", intended, data, tag=tag, revision=revision)
                if errors:
                    raise ValueError(f"Registry example context invalid: {'; '.join(errors)}")
                for note in notes:
                    print(f"{document}: {note}")


if __name__ == "__main__":
    try:
        check()
    except (ValueError, OSError, TypeError) as error:
        print(f"Example check failed: {error}", file=sys.stderr)
        sys.exit(1)
