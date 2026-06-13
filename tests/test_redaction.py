from wendle.capture.redaction import field_name, is_sensitive
from wendle.capture.types import UINode


def _node(**kw):
    base = dict(
        cls="android.widget.EditText",
        resource_id="",
        text="",
        content_desc="",
        clickable=True,
        password=False,
        bounds=(0, 0, 100, 100),
    )
    base.update(kw)
    return UINode(**base)


def test_password_node_is_sensitive():
    assert is_sensitive(_node(password=True)) is True


def test_plain_node_is_not_sensitive():
    assert is_sensitive(_node(content_desc="Username")) is False


def test_field_name_from_resource_id():
    assert field_name(_node(resource_id="com.app:id/password")) == "password"


def test_field_name_from_content_desc():
    assert field_name(_node(content_desc="Card Number")) == "card_number"


def test_field_name_both_empty_is_generic():
    assert field_name(_node()) == "field"


def test_field_name_sensitive_ignores_content_desc():
    # a sensitive field with only a content-desc must not leak it into the name
    assert field_name(_node(password=True, content_desc="Card PIN")) == "field"
