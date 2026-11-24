# Compatibility issues and inconsistencies

This document records differences between this Compose supplement and the
shared Margo specification, inconsistencies found during the LinkML migration,
and outstanding validation gaps. It is not normative. The shared specification
is a reference; Compose-specific requirements and examples in this repository
take precedence where they differ. References below are pinned where possible
because the specifications are evolving.

## Open specification questions

### Application Description versions

The [Compose example](./specification/applications/application-description.md)
has a top-level `id` and no `kind`. The requested
[`create-data-model` reference](https://github.com/Silvanoc/margo-specification/blob/e82cf368781abc61aec6ce2fe548a1c2bb2540b9/model/application-description.linkml.yaml#L27-L45)
instead requires `kind` and puts `id` under `metadata`. A later
[shared model](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L20-L40)
uses a top-level `id` like this repository's example. These references should
not be treated as interchangeable. **Decision for this migration:** preserve
the Compose example; do not add `kind` or move `id`.

### Placement selector property paths

The Compose [Application Description example](./specification/applications/application-description.md)
uses `/memory` and `/storage`, while the corresponding [profile characteristics](./specification/margo-management-interface/compose-profile-characteristics.md)
define an `available` property for each. The historical [shared model](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L392-L403)
evaluates `property` relative to the selected characteristic's `properties`.
Under that rule, these two paths would not resolve; `/available` would.
Whether that rule applies unchanged to this evolving supplement needs
confirmation. The example is preserved until that is resolved.

The placement-expression language is defined by Margo, with RFC 6901 JSON
Pointers for property paths. Its vocabulary resembles Kubernetes selectors,
but its nested item selectors and operators have Margo-specific semantics;
no source establishing its original inspiration was found.
The reference examples use string-form pointers such as `/peripherals`; it
does not explicitly say whether RFC 6901 URI-fragment representations are
also permitted. The model currently accepts the property as a string without
checking its JSON Pointer syntax, to avoid ruling out a representation without
clarification.

### Parameter arrays in the shared reference

The [shared model at 4137e6a](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L571-L581)
describes scalar and array parameter values, but its LinkML `any_of` lists
only scalar types and marks array support as still to do. The documentation
and machine-readable reference therefore do not yet express the same set of
values.

### Null in optional typed fields

The shared Application Description describes several optional fields as
strings, booleans, arrays or specific parameter value types, but its generated
LinkML JSON Schema also permits `null` for optional fields. The prose does not
say whether an explicit `null` is a valid value. In particular, this affects
`Parameter.value`, metadata descriptions and optional profile descriptions.
Missing fields and fields explicitly containing `null` must not be conflated.
**Decision for this migration:** optional typed Application Description
fields may be omitted, but an explicitly present value must have a documented
type; `null` is not accepted. OCI annotation values are a separate case: the
OCI specification requires them to be strings when present.

### Specification extensions in the shared reference

The shared [extension rules at 4137e6a](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/system-design/specification/extensibility/specification-extensions.md#L15-L40)
permit object-valued properties such as `x-acme-extensions` and require
unknown extensions to be accepted. In a local LinkML 1.12.1 check, the
Application Description schema at that commit rejected such a property as
additional, despite the prose. This is a mismatch in the reference, not a
decision to reject extensions here.

### Select option value types in the shared reference

The [shared model at 4137e6a](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L821-L831)
says each select option must match the parameter's data type, but declares
the options as strings. This leaves it unclear whether options for numeric or
boolean parameters may contain numeric or boolean values. This supplement does
not define a Compose-specific override. **Decision for this migration:**
support string, numeric and boolean options. The reference does not clearly
define whether an `array[...]` parameter's individual options are scalar
elements or arrays; that case still needs clarification.

### Selector value constraints in the shared reference

The shared [selector rules](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/resources/index.md.jinja2#L149-L190)
constrain the values accepted by each operator. The corresponding LinkML model
uses `Any` for selector values; in a local generated JSON Schema check, it
accepted mixed types, multiple booleans and objects that the prose does not
permit for `In` and `NotIn`. Compatibility cannot be established by copying
the reference schema without accounting for this difference.

The reference also uses the same operator enumeration for item expressions,
including `ContainsAll` and `ContainsAny`, but its item-expression structure
does not define another `itemSelector`. Whether recursive object-array
matching is intended is not specified. Numeric comparison values must be
"parsable as numbers", but the accepted syntax for numeric strings is not
defined either.

### YAML scalar interpretation

The inspected shared and Compose specifications do not identify a YAML
version or scalar-resolution profile for Application Descriptions. This can
change the meaning of unquoted values such as `on`, `yes`, or dates between
YAML implementations. The validation path currently follows LinkML's
PyYAML-based scalar resolution; a future clarification is needed before
claiming equivalence across YAML implementations.

### OCI repository URI authority

The [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/v1.1.0/spec.md#pulling-manifests)
defines repository path syntax but does not define a complete `oci://` URI or
registry-authority grammar. The validator uses the established
[distribution/reference](https://github.com/distribution/reference/blob/main/regexp.go)
style of authority, port and repository path, without DNS or registry access.
For example, it does not accept a DNS name ending in a dot; whether such an
authority should be supported here needs clarification.

### Numeric precision in LinkML generation

In a local LinkML 1.12.1 test, a schema bound written as
`9007199254740993.0` was emitted in the generated JSON Schema as
`9007199254740992.0`. This is a generator limitation, not a change to the
Compose specification. It must not be allowed to silently alter a constraint;
the generation and validation scripts currently fail explicitly when a source
schema declares numeric constraints that they cannot verify. The current
Compose models do not declare such constraints.

## Corrected inconsistencies

- The peripherals and interfaces examples in the [original profile page](https://github.com/margo/deployment-specification-compose/blob/7fe9e51dbe23ccb2b79bac5f321ad4851459a21f/specification/margo-management-interface/compose-profile-characteristics.md#L112-L152)
  had trailing commas in JSON objects. The commas were removed.
- The [original registry page](https://github.com/margo/deployment-specification-compose/blob/7fe9e51dbe23ccb2b79bac5f321ad4851459a21f/specification/applications/application-registry.md#L18-L25)
  required annotations on a layer, while its `oras push --annotation`
  example sets [manifest annotations](https://oras.land/docs/commands/oras_push/).
  The requirement now says manifest, as confirmed for this migration.
- The original registry page required the SemVer version and OCI tag to be
  identical, but SemVer build metadata uses `+`, which the [OCI Distribution
  Specification](https://github.com/opencontainers/distribution-spec/blob/v1.1.0/spec.md#pulling-manifests)
  does not allow in tags. The page now specifies replacing `+` with `_` in
  the tag while retaining the SemVer value in the annotation. This follows
  both [Helm 3's established OCI convention](https://helm.sh/docs/v3/topics/registries/)
  and the [shared model at 4137e6a](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L528-L540).
- The shared model's `apiVersion` description says it must be `v1`, but the
  corresponding LinkML slot did not enforce that value. The adapted model
  makes the constraint explicit.
- The reference's repository pattern only checked for an `oci://` prefix,
  which also admitted malformed references. The validation path checks the
  conventional OCI repository URI structure, including authority and path.
- Whole-string patterns using `$` accepted a trailing newline in common regex
  engines. The modeled identifiers, versions and known characteristic keys
  now use an absolute end. Explicit null values are rejected for the confirmed
  optional typed fields and OCI annotations; omission remains allowed.

## Current validation gaps

These are limitations of the migration, not necessarily conflicts between the
specifications. They must be resolved or explicitly accepted before claiming
full input compatibility.

- The Application Description model is adapted from the matching shared
  reference and now includes boolean selector values, array parameter values
  and component `timeout`. Full compatibility remains to be verified against
  the remaining rules and any differences in the evolving shared specification.
- The validation path checks the documented selector operator rules, except
  for nested object-array behavior, which is unspecified in the reference.
  The shared prose says numeric comparison values must be "parsable as
  numbers" but does not define a lexical grammar for numeric strings; the
  validator uses finite decimal notation.
- The validation path checks the required Compose characteristics and unique
  characteristic keys, and preserves empty `properties` objects. These checks
  supplement the generated JSON Schema; standard JSON Schema validators do not
  enforce all `x-margo-*` metadata.
- Additional annotation values are checked as strings and same-document
  version annotations are compared. Comparisons with an OCI tag and revision
  require those values as explicit validation context; no registry is queried.
  Archive and runtime requirements also require more than structural
  validation.
- JSON and YAML numbers are parsed without converting decimal values to
  binary `float`. Decimal implementation limits still apply. The generic
  `multipleOf` check fails explicitly for coefficients over 4,000 digits, and
  schemas containing numeric constraints that cannot be verified against
  LinkML's output are rejected rather than silently rounded. The current
  Compose models do not use `multipleOf` or numeric schema bounds.
- In a local LinkML 1.12.1 check, `linkml examples` accepted the Application
  Description fixtures but failed on a valid CPU characteristic fixture with
  a `'string'` error, both as JSON and as YAML. The migration therefore
  discovers fixture files by directory and validates parsed data through
  LinkML's Validator API and patched JSON Schemas; the cause of the
  `linkml examples` failure has not been established.
