import os

import pytest
from template_advanced_fixture import release_label


def test_release_label_preserves_the_selected_tag():
    assert release_label("v0.1.0-beta") == "v0.1.0-beta"


@pytest.mark.parametrize("value", ["../outside", "", "unsafe/label"])
def test_release_label_refuses_path_traversal(value):
    with pytest.raises(ValueError):
        release_label(value)


def test_host_token_is_not_implicitly_exported_to_tests():
    assert "RELEASE_TOKEN" not in os.environ
