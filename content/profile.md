# Compose Profile Characteristics

The following [Profile](https://docs.margo.org/specification/margo-management-interface/) characteristics are applicable for workload targets supporting the Compose deployment technology. Workload targets supporting the Compose deployment technology MUST report the applicable characteristics as defined below in the profile.

{% from 'model/profile/index.md.jinja2' import profile_group with context %}{{ profile_group('deployment') }}

## Additional Required Characteristics

The following characteristics MUST be reported for workload targets supporting the Compose deployment technology.

{{ profile_group('characteristic') }}

## Example Profile Payload

```json
{{ example('profile/ComposeProfile-001.json') }}
```
