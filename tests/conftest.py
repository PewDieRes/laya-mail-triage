import dataclasses
from pathlib import Path

import pytest

from triage.config import load_config

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return dataclasses.replace(
        load_config(REPO / "tests" / "fixtures" / "config"), vip=frozenset({"boss@example.com", "@family.org"})
    )
