"""Tests for Account parsing and Chrome profile naming."""

import pytest

from src.models.account import Account


@pytest.mark.parametrize(
    "line,expected",
    [
        ("user@gmail.com:secret", ("user@gmail.com", "secret")),
        ("user@gmail.com,secret", ("user@gmail.com", "secret")),
        ("  user@gmail.com : secret  ", ("user@gmail.com", "secret")),
        ("user@gmail.com,my:pass:word", ("user@gmail.com", "my:pass:word")),
        ("# a comment line", None),
        ("", None),
        ("   ", None),
        ("no-separator-here", None),
        (":password-without-email", None),
        ("email-without-password:", None),
    ],
)
def test_parse_line(line, expected):
    account = Account.parse_line(line)
    if expected is None:
        assert account is None
    else:
        assert account is not None
        assert (account.email, account.password) == expected


def test_profile_dir_name_is_filesystem_safe():
    account = Account(email="First.Last+tag@gmail.com", password="pw")
    profile = account.get_profile_dir_name()
    assert profile == "profile_First_Last_tag_gmail_com"
    assert all(c.isalnum() or c in ("-", "_") for c in profile)


def test_profile_dir_name_defaults_status():
    account = Account(email="a@b.com", password="pw")
    assert account.status == "Ready"
