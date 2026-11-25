# Application Description Supplemental Material

The following content supplements the [Application Description](https://docs.margo.org/specification/applications/application-description) documentation in the core specification with requirements clarifications specific to Compose workloads.

## Application Description Property Handling

The following defines how certain properties in the Application Description file are handled specifically for Compose workloads. Workload target implementation MUST apply these rules when installing Compose workloads.

- {{ schemaview.induced_slot('wait', 'ComposeComponentProperties').description }}
- {{ schemaview.induced_slot('pointer', 'ComposeParameterTarget').description }}

## Example Application Description

```yaml
{{ example('application/ApplicationDescription-001.yaml') }}
```
