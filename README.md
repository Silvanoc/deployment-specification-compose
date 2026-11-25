# Compose Deployment Technology Specification

This repository contains the supplemental requirements for application and workload target suppliers to support the Compose workload deployment technology. This documentation supplements the requirements documented in Margo's [core specification](https://github.com/margo/specification) and does not stand on its own.

**Deployment Technology Key:** `margo.org/deployment/compose`

**Status:** Candidate

**Specification Version:** `1.0.0-rc.3`

> **Note:** This specification follows the same [Versioning Management Strategy](https://docs.margo.org/specification/specification-version-management) outlined in Margo's core specification. This specification is versioned independently of Margo's core specification.

## Supplemental Requirements

The following pages define the additional requirements for supporting the Compose deployment technology:

- [Compose Profile Characteristics](./specification/margo-management-interface/compose-profile-characteristics.md)
- [Application Description Supplemental Material](./specification/applications/application-description.md)
- [Application Registry Supplemental Material](./specification/applications/application-registry.md)
- [Workload Target Requirements Supplemental Material](./specification/margo-workload-targets/workload-target-requirements.md)
- [Collecting Workload Observability Data Supplemental Material](./specification/observability/collecting-workload-observability-data.md)

## Data models and generated documentation

The LinkML schemas in [`model/`](./model/) describe Compose profile
characteristics, Application Descriptions and Compose component manifest
annotations. The schemas and their example fixtures are inputs to the
documentation and validation tools. The Application Description model imports
the shared model and adds Compose-specific constraints; see the
compatibility report for differences and remaining validation gaps.
Compose-specific constraints apply to Compose-key deployment profiles. Other
keys are checked against the shared model only and reported as generic-only
unless their specializations are loaded. To register a loaded deployment type,
subclass `DeploymentProfile` and set a unique `slot_usage.key.equals_string`;
unknown keys remain generic-only.
An importing bundle must declare `margo-common` as
`https://raw.githubusercontent.com/Silvanoc/margo-specification/externalize-component-specs/model/`;
otherwise LinkML may raise `Unknown CURIE prefix: margo-common` while resolving
transitive imports.

Common Application Description, OCI component and workload-target characteristic
definitions are imported from the core specification via raw GitHub URLs on
the `externalize-component-specs` branch. Generation and validation require
those URLs to be reachable. The imports follow a moving branch and
are therefore not version-pinned. Generation and validation fetch the remote
schemas independently, and they can change between checks. Numeric-source
verification therefore cannot guarantee consistency with the schemas consumed
by LinkML until the dependency is immutable or snapshotted.

With [uv](https://docs.astral.sh/uv/) installed, validate the examples and
generate and check the Markdown and JSON Schemas with:

```bash
uv sync --locked
uv run --locked python tools/check_examples.py
uv run --locked python tools/generate.py --check
```

Run the workflow tests with `uv run --locked python -m unittest discover -s tests -v`.
An optional [`justfile`](./justfile) provides shortcuts for these commands.
Use this repository's `tools/generate.py` and `tools/validate.py`, or `just
check`, for key-based subtype dispatch. A bare LinkML JSON Schema generator can
accept a permissive base branch and does not by itself enforce known deployment
keys' constraints or the native `has_member` required-characteristic constraints.
The generated JSON Schema enforces structural constraints;
the validation tools also enforce the `x-margo` semantic checks.

The generated supplemental pages are in `build/site/specification/` and are
checked against the corresponding source pages. Generated JSON Schemas are in
`build/artifacts/json-schemas/`; `build/` is disposable generated output. The
three generated pages combine Markdown sources in `content/` with validated
examples and, where needed, LinkML model data and formatting templates in
`tools/templates/`. Edit the models
or Markdown sources when changing content, then review the generated pages
against the checked-in pages. The remaining two pages contain prose and are
copied unchanged. The profile template iterates over the model's annotated
characteristic classes, and documented JSON and YAML examples are inserted
from the validated fixture files.

The example checker compares the modeled examples with valid fixtures and checks
that valid and invalid fixtures pass and fail, respectively. These structural
checks do not replace the normative prose, validate every core-specification
or OCI requirement, or verify archive contents, deployment behavior or
relationships with an OCI registry.

Registry and profile fixture validation use LinkML's `Validator` and JSON
Schema plugin, with targeted adaptations for modeled constraints that LinkML
currently omits. These include required characteristic members, uniqueness,
extensions, registry annotation types, null restrictions and version equality.
The existing fixture driver retains strict parsing, including exact numeric
values, which LinkML's example runner cannot currently preserve. Documentation
drift and OCI tag/revision consistency are checked separately. Application
fixture validation still uses the existing validation engine. The
`margo_profile_example` annotation is not used to validate fixtures; it is
currently used to select examples for generated profile documentation.

Fixtures are discovered under `model/examples/<model>/valid/` and
`model/examples/<model>/invalid/`. Name each file `<ClassName>-<name>.yaml`
or `<ClassName>-<name>.json` so the checker can select its LinkML class.

To validate another document, select its schema and target class:

```bash
uv run --locked python tools/validate.py --schema model/compose-application.linkml.yaml --target-class ApplicationDescription path/to/application.yaml
```

The generated JSON Schemas contain additional `x-margo-*` metadata for rules
that ordinary JSON Schema cannot express. Use this validation script to check
those rules as well. When validating Compose component annotations, optional
`--tag` and `--revision` arguments enable comparison with those external
values; no registry is contacted. The script reports when external comparisons
were not performed.

The validation script preserves parsed empty objects and checks additional
rules such as characteristic-key uniqueness and annotation equality. An
ordinary JSON Schema validator may ignore the `x-margo-*` metadata and will
not perform every one of these checks. Optional typed Application Description
fields may be omitted, but an explicitly supplied `null` is rejected.

See [Compatibility issues and inconsistencies](./COMPATIBILITY-ISSUES.md) for
the differences from the shared specification and unresolved questions.

## Local Registries

This section describes options for configuring local registries to avoid reliance on public Internet-accessible registries for situations where the Application Package and workload artifacts are not accessible via the internet.

### Option - Container Image Registry as Pull-through Cache on Docker Level

To configure a pull-through cache in Docker for the Container Image Registry, a Docker Registry can be set up that acts as a caching proxy for a remote Docker Registry. Such a Docker Registry can be defined using the following `config.yml`:

```yaml
version: 0.1
log:
  fields:
    service: registry
storage:
  filesystem:
    rootdirectory: /var/lib/registry
http:
  addr: :5000
  headers:
    X-Content-Type-Options: [nosniff]
health:
  storagedriver:
    enabled: true
    interval: 10s
    threshold: 3
proxy:
  remoteurl: https://registry-1.docker.io
```

This registry can be started using the following command:
`docker run -d -p 5000:5000 --restart unless-stopped --name registry -v $(pwd)/config.yml:/etc/docker/registry/config.yml registry:2`

Then, the Docker daemon needs to be configured to use the private registry as a mirror in the `/etc/docker/daemon.json` file:

```json
{
  "registry-mirrors": ["http://10.100.2.102:5000"],
  "insecure-registries": ["10.100.2.102:5000"]
}
```

## Licenses

Margo contributors contributing to this draft specification have made those contributions subject to the Open Web Foundation Contributor License Agreement Contribution Copyright Grant, available at [Open Web Foundation - CLA Copyright Grant 0.9](https://www.openwebfoundation.org/the-agreements/the-owf-0-9-agreements-necessary-claims/cla-copyright-grant-0-9).

Copyright ©️ 2025 Margo.
