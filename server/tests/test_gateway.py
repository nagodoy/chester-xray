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


class TestVerification:
    """C-ECHO, which a sender uses to check the node before it will store."""

    class _Association:
        def __init__(self, ae_title: str):
            self.requestor = type("Requestor", (), {"ae_title": ae_title})()

    class _Event:
        def __init__(self, ae_title: str):
            self.assoc = TestVerification._Association(ae_title)

    def test_an_echo_is_answered(self):
        handler = gateway.build_echo_handler([])

        assert handler(self._Event("ANY_AE")) == gateway.STATUS_SUCCESS

    def test_an_allowed_caller_is_answered(self):
        handler = gateway.build_echo_handler(["OSIRIX", "PACS"])

        assert handler(self._Event("PACS")) == gateway.STATUS_SUCCESS

    def test_a_caller_the_gateway_would_refuse_an_image_from_is_refused_here_too(self):
        """Verification must not tell an unlisted peer the association is good."""
        handler = gateway.build_echo_handler(["OSIRIX"])

        assert handler(self._Event("STRANGER")) == gateway.STATUS_SOP_CLASS_NOT_SUPPORTED

    def test_the_ae_title_is_compared_without_its_padding(self):
        """pynetdicom pads AE titles out to 16 characters."""
        handler = gateway.build_echo_handler(["OSIRIX"])

        assert handler(self._Event("OSIRIX          ")) == gateway.STATUS_SUCCESS

    def test_the_server_offers_verification_and_dispatches_echo(self, monkeypatch):
        """Without the context the association is refused before any C-ECHO.

        Runs main() against a stand-in AE so this asserts what the gateway
        actually registers, not what pynetdicom happens to define.
        """
        import pynetdicom
        from pynetdicom import evt
        from pynetdicom.sop_class import Verification

        recorded: dict = {"contexts": [], "handlers": None}

        class _AE:
            def __init__(self, ae_title):
                recorded["ae_title"] = ae_title

            def add_supported_context(self, sop_class):
                recorded["contexts"].append(str(sop_class))

            def start_server(self, address, evt_handlers):
                recorded["handlers"] = evt_handlers
                recorded["address"] = address

        monkeypatch.setattr(pynetdicom, "AE", _AE)

        exit_code = gateway.main(
            ["--stow-url", "https://host.test", "--token", "t", "--owner", "a@b.test"]
        )

        assert exit_code == 0
        assert str(Verification) in recorded["contexts"]
        assert evt.EVT_C_ECHO in [event for event, _ in recorded["handlers"]]
        assert evt.EVT_C_STORE in [event for event, _ in recorded["handlers"]]
