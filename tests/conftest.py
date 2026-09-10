import pytest

from my20q.topics import Topic, load_topics


@pytest.fixture
def topics() -> list[Topic]:
    """The bundled flat topic list."""
    return load_topics()
