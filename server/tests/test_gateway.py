"""The on-premises C-STORE gateway's forwarding contract."""

from __future__ import annotations

import pytest

from chester import gateway


def test_multipart_wraps_the_instance_intact(make_dicom):
    data = make_dicom()

    body, content_type = gateway.build_multipart(data)

    assert data in body
    assert "multipart/related" in content_type
    assert gateway.BOUNDARY in content_type
    assert body.endswith(f"--{gateway.BOUNDARY}--\r\n".encode())


def test_the_gateway_refuses_plaintext_transport(capsys):
    """The ingest token travels in a header on every forwarded instance."""
    exit_code = gateway.main(
        ["--stow-url", "http://insecure.test", "--token", "t", "--owner", "a@b.test"]
    )
    assert exit_code == 2


def test_missing_configuration_is_refused():
    assert gateway.main(["--stow-url", "https://host.test", "--token", "", "--owner", ""]) == 2


@pytest.mark.parametrize(
    ("argv", "attribute", "expected"),
    [
        (["--port", "1234"], "port", 1234),
        (["--ae-title", "MYSCP"], "ae_title", "MYSCP"),
        (["--allowed-calling-aes", "A,B"], "allowed_calling_aes", "A,B"),
    ],
)
def test_arguments_are_parsed(argv, attribute, expected):
    assert getattr(gateway.parse_args(argv), attribute) == expected
