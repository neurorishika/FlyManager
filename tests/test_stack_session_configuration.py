"""Guard the Redis-session settings required by the performance rollout."""

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative_path",
    [
        "compose.yaml",
        "deploy/portainer-stack-2026-09-02.yaml",
        "compose.synology.yaml",
    ],
)
def test_application_stack_uses_a_dedicated_redis_session_database(relative_path):
    stack = yaml.safe_load((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    environment = stack["services"]["app"]["environment"]

    assert environment["REDIS_URL"] in {
        "redis://redis:6379/0",
        "${REDIS_URL:-redis://redis:6379/0}",
    }
    assert environment["SESSION_TYPE"] == "redis"
    assert environment["SESSION_REDIS_URL"] == "redis://redis:6379/1"
