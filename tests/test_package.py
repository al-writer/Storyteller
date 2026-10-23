import re

import app


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", app.__version__)
