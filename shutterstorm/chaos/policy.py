"""Target allowlist: the only containers a fault may ever touch."""

from __future__ import annotations

from dataclasses import dataclass, field


class PolicyError(Exception):
    """A target (or parameter) is outside what the sandbox allows."""


@dataclass(frozen=True)
class TargetPolicy:
    allowed: frozenset[str]
    aliases: dict[str, str] = field(default_factory=dict)
    required_label: tuple[str, str] | None = None  # (key, value) the live container must carry

    def resolve(self, name: str) -> str:
        """Map an alias to a container name and enforce the allowlist."""
        resolved = self.aliases.get(name, name)
        if resolved not in self.allowed:
            raise PolicyError(
                f"{name!r} is not an allowed target. Allowed: {', '.join(sorted(self.allowed))}"
            )
        return resolved

    def check_container(self, name: str, labels: dict[str, str]) -> None:
        if self.required_label is None:
            return
        key, value = self.required_label
        if labels.get(key) != value:
            raise PolicyError(f"container {name!r} is missing required label {key}={value}")


IMMICH_POLICY = TargetPolicy(
    allowed=frozenset(
        {"immich_server", "immich_machine_learning", "immich_postgres", "immich_redis"}
    ),
    aliases={
        "server": "immich_server",
        "ml": "immich_machine_learning",
        "machine-learning": "immich_machine_learning",
        "db": "immich_postgres",
        "postgres": "immich_postgres",
        "redis": "immich_redis",
    },
    required_label=("shutterstorm.target", "true"),
)
