# Application Description Supplemental Material

The following content supplements the [Application Description](https://docs.margo.org/specification/applications/application-description) documentation in the core specification with requirements clarifications specific to Compose workloads.

## Application Description Property Handling

The following defines how certain properties in the Application Description file are handled specifically for Compose workloads. Workload target implementation MUST apply these rules when installing Compose workloads.

- The `component.properties.wait` property: if `wait` is set to `true` for a Compose component, the device MUST wait until all containers in the Compose project reach **running** state before reporting the deployment as successful. This is equivalent to `docker compose up` or `podman-compose up` completing synchronously without `--detach`. If any container exits with a non-zero exit code during startup, the deployment MUST be reported as failed immediately. If health checks are defined in `compose.yaml`, implementations SHOULD additionally wait for all containers to reach **healthy** state before reporting success.
- The `parameter.targets[].pointer` MUST be the environment variable name the Compose files expect to use.

## Example Application Description

```yaml
{{ example('application/ApplicationDescription-001.yaml') }}
```
