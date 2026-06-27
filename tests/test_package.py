import earnbench


def test_version_is_string() -> None:
    assert isinstance(earnbench.__version__, str)
    assert earnbench.__version__
