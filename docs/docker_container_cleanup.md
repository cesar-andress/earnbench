# Docker container cleanup (Phase A / Phase B idempotency)

EarnBench wraps SWE-bench harness container creation so interrupted or
parallel runs do not leave **orphaned evaluation containers** that block
subsequent grading with Docker HTTP **409** (`container name already in use`).

## Managed containers

Only containers whose names start with the SWE-bench prefix are touched:

```text
sweb.eval.<instance_id>.<run_id>
```

Example:

```text
sweb.eval.django__django-13279.nominal_django__django-13279
```

Foreign containers (databases, dev stacks, etc.) are **never** stopped or removed.

## Behavior

Before each harness `ContainerCollection.create` call:

1. If a managed container with the target name exists, **stop** it when running
   and **remove** it (`force=True`).
2. Proceed with normal SWE-bench container creation.
3. On HTTP **409** name conflict, repeat cleanup once and **retry** creation.

This logic is applied in:

- `default_nominal_runner` (Phase A nominal, Phase B nominal, **and** `pi_verif.v1` when tamper is not detected — it reuses the nominal harness)
- `default_pi_vtest_runner` (Phase B visible overfit)
- `default_pi_env_runner` (Phase A/B environment hardening)

via `earnbench.adapters.docker_cleanup`.

## API

```python
from earnbench.adapters.docker_cleanup import cleanup_stale_container

cleanup_stale_container(
    "sweb.eval.django__django-13279.nominal_django__django-13279",
    client=docker_client,
)
```

Returns a `CleanupResult` with `removed`, `skipped`, and `reason` fields.

## Manual recovery (optional)

Automatic cleanup makes manual `docker rm` unnecessary for normal operation.
If needed for debugging:

```bash
docker rm -f $(docker ps -aq --filter name=sweb.eval.)
```

## Regression

`tests/test_docker_cleanup.py::test_wrap_container_create_retries_after_http_409`
simulates the historical E006 infrastructure failure and verifies the runner
retries successfully after cleanup.
