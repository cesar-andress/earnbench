"""Remove stale SWE-bench evaluation containers before harness runs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SWEBENCH_EVAL_CONTAINER_PREFIX = "sweb.eval."


def is_managed_swebench_container(name: str) -> bool:
    """Return True when ``name`` belongs to SWE-bench / EarnBench harness runs."""
    normalized = name.strip().lstrip("/")
    return normalized.startswith(SWEBENCH_EVAL_CONTAINER_PREFIX)


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Outcome of attempting to remove one named container."""

    name: str
    existed: bool
    removed: bool
    skipped: bool
    reason: str = ""


def _is_container_name_conflict(exc: BaseException) -> bool:
    try:
        import docker.errors

        if isinstance(exc, docker.errors.APIError):
            return exc.status_code == 409
    except ImportError:
        pass
    message = str(exc).lower()
    return "409" in message and "already in use" in message


def cleanup_stale_container(
    name: str,
    *,
    client: Any | None = None,
) -> CleanupResult:
    """Stop and remove a stale managed container so creation can proceed.

    Foreign containers (names not under ``sweb.eval.``) are never touched.
    """
    if not name:
        return CleanupResult(
            name=name,
            existed=False,
            removed=False,
            skipped=True,
            reason="empty name",
        )
    if not is_managed_swebench_container(name):
        return CleanupResult(
            name=name,
            existed=False,
            removed=False,
            skipped=True,
            reason="not a managed SWE-bench container",
        )

    owns_client = client is None
    if owns_client:
        import docker

        client = docker.from_env()

    try:
        import docker.errors

        try:
            container = client.containers.get(name)
        except docker.errors.NotFound:
            return CleanupResult(
                name=name,
                existed=False,
                removed=False,
                skipped=False,
            )

        status = container.status
        if status == "running":
            container.stop(timeout=10)
        container.remove(force=True)
        logger.info(
            "removed stale managed container %s (previous status=%s)",
            name,
            status,
        )
        return CleanupResult(
            name=name,
            existed=True,
            removed=True,
            skipped=False,
        )
    finally:
        if owns_client:
            client.close()


def wrap_container_create_with_cleanup(
    original_create: Callable[..., Any],
) -> Callable[..., Any]:
    """Wrap ``ContainerCollection.create`` with pre-create cleanup and 409 retry."""

    def patched_create(
        self: Any,
        image: Any,
        command: Any = None,
        **kwargs: Any,
    ) -> Any:
        name = kwargs.get("name")
        client = getattr(self, "client", None)
        if isinstance(name, str) and name:
            cleanup_stale_container(name, client=client)
        try:
            return original_create(self, image, command, **kwargs)
        except Exception as exc:
            if (
                isinstance(name, str)
                and name
                and _is_container_name_conflict(exc)
            ):
                logger.warning(
                    "container name conflict for %s; retrying after cleanup",
                    name,
                )
                cleanup_stale_container(name, client=client)
                return original_create(self, image, command, **kwargs)
            raise

    return patched_create


@contextmanager
def managed_swebench_container_create() -> Iterator[None]:
    """Patch Docker container creation for idempotent SWE-bench harness runs."""
    from docker.models.containers import ContainerCollection

    original_create = ContainerCollection.create
    ContainerCollection.create = wrap_container_create_with_cleanup(original_create)  # type: ignore[method-assign]
    try:
        yield
    finally:
        ContainerCollection.create = original_create  # type: ignore[method-assign]


__all__ = [
    "CleanupResult",
    "SWEBENCH_EVAL_CONTAINER_PREFIX",
    "cleanup_stale_container",
    "is_managed_swebench_container",
    "managed_swebench_container_create",
    "wrap_container_create_with_cleanup",
]
