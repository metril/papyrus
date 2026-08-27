"""Unit tests for CUPS queue provisioning argv (``app.services.cups_admin``).

Pure argv-capture tests: ``_run`` (the subprocess wrapper) and
``_write_avahi_service`` (filesystem + avahi reload) are replaced with
recorders, so no CUPS/Avahi/subprocess is touched. They lock in the two
guarantees the "Papyrus default queue" fix depends on: every created queue
carries ``printer-error-policy=abort-job``, and the built-in default queue is
created WITHOUT a second Avahi advert (airprint.service already advertises it).

Also covers the real (unstubbed) ``_run`` (F33: raises RuntimeError on a
non-zero exit unless ``ignore_errors``) and ``_avahi_service_xml`` (F32:
escapes interpolated values so a display name with '&'/'<' still produces
well-formed XML).
"""
import xml.etree.ElementTree as ET

import pytest

from app.services import cups_admin


@pytest.fixture
def run_calls(monkeypatch):
    """Capture every ``cups_admin._run(argv)`` as a list of argv lists."""
    calls: list[list[str]] = []

    async def fake_run(args, ignore_errors=False):
        calls.append(list(args))

    monkeypatch.setattr(cups_admin, "_run", fake_run)
    return calls


@pytest.fixture
def avahi_writes(monkeypatch):
    """Record Avahi service writes without touching the filesystem."""
    writes: list[tuple[str, str]] = []

    async def fake_write(display_name, cups_name):
        writes.append((display_name, cups_name))

    monkeypatch.setattr(cups_admin, "_write_avahi_service", fake_write)
    return writes


def _lpadmin_for(calls, name):
    """The single ``lpadmin`` argv that created queue ``name``."""
    matches = [
        c for c in calls
        if c[:2] == ["lpadmin", "-p"] and len(c) > 2 and c[2] == name
    ]
    assert len(matches) == 1, f"expected one lpadmin create for {name!r}, got {matches}"
    return matches[0]


async def test_ensure_default_queue_argv_and_no_avahi(run_calls, avahi_writes):
    await cups_admin.ensure_default_queue()

    argv = _lpadmin_for(run_calls, cups_admin.DEFAULT_QUEUE_NAME)
    assert argv[argv.index("-v") + 1] == "papyrus:/"
    assert argv[argv.index("-P") + 1] == cups_admin.PPD_PATH
    assert "printer-is-shared=true" in argv
    assert "printer-error-policy=abort-job" in argv
    # The static airprint.service already advertises printers/Papyrus, so the
    # default queue must NOT write a second Avahi service.
    assert avahi_writes == []


async def test_default_queue_name_matches_self_advert_marker():
    from app.routers.printers import _SELF_ADVERTISEMENT_RESOURCE_MARKER

    assert (
        _SELF_ADVERTISEMENT_RESOURCE_MARKER
        == f"printers/{cups_admin.DEFAULT_QUEUE_NAME}"
    )


async def test_add_network_queue_sets_abort_job(run_calls, avahi_writes):
    # cups_name and display_name are deliberately distinct (F155) -- a swap
    # inside add_network_queue would make lpadmin create a queue named after
    # the free-text display name (spaces and all) and point the Avahi advert
    # at the wrong queue, and identical strings would hide that.
    await cups_admin.add_network_queue("Office_Brother", "Office Brother")

    argv = _lpadmin_for(run_calls, "Office_Brother")
    assert argv[argv.index("-v") + 1] == "papyrus:/"
    assert "printer-error-policy=abort-job" in argv
    assert avahi_writes == [("Office Brother", "Office_Brother")]


async def test_add_physical_printer_sets_abort_job_on_both_queues(run_calls, avahi_writes):
    # Same rationale as above: distinct cups_name/display_name so a swap in
    # add_physical_printer's argument order is caught rather than hidden by
    # both queue names happening to match the advert name.
    await cups_admin.add_physical_printer("Office_Brother", "Office Brother", "ipp://printer/ipp")

    hold = _lpadmin_for(run_calls, "Office_Brother")
    assert hold[hold.index("-v") + 1] == "papyrus:/"
    assert "printer-error-policy=abort-job" in hold

    release = _lpadmin_for(run_calls, "Office_Brother_release")
    assert release[release.index("-v") + 1] == "ipp://printer/ipp"
    assert "everywhere" in release
    assert "printer-error-policy=abort-job" in release

    assert avahi_writes == [("Office Brother", "Office_Brother")]


# --------------------------------------------------------------------------- #
# _run (F33): raises on failure unless ignore_errors
# --------------------------------------------------------------------------- #
async def test_run_raises_runtime_error_on_nonzero_exit():
    with pytest.raises(RuntimeError):
        await cups_admin._run(["false"])


async def test_run_ignores_nonzero_exit_when_ignore_errors():
    # Must not raise even though the command fails.
    await cups_admin._run(["false"], ignore_errors=True)


async def test_run_does_not_raise_on_success():
    await cups_admin._run(["true"])


async def test_add_physical_printer_propagates_lpadmin_failure(monkeypatch):
    """Regression (F33): a real lpadmin failure must surface as a RuntimeError
    from add_physical_printer instead of silently 'succeeding' with no queue
    created."""
    async def fake_run(args, ignore_errors=False):
        if not ignore_errors:
            raise RuntimeError(f"{args[0]} failed (rc=1): bad URI")

    monkeypatch.setattr(cups_admin, "_run", fake_run)
    monkeypatch.setattr(cups_admin, "_write_avahi_service", lambda *a: None)

    with pytest.raises(RuntimeError):
        await cups_admin.add_physical_printer("Office", "Office", "garbage://uri")


# --------------------------------------------------------------------------- #
# _avahi_service_xml (F32): escapes interpolated values
# --------------------------------------------------------------------------- #
def test_avahi_service_xml_escapes_ampersand_and_angle_brackets():
    xml_str = cups_admin._avahi_service_xml("A&B<c>", "AB_c")

    # Must parse as well-formed XML -- an unescaped '&'/'<' would raise here.
    root = ET.fromstring(xml_str)
    assert root.tag == "service-group"
    name_el = root.find("name")
    assert name_el.text == "A&B<c> @ %h"


def test_avahi_service_xml_well_formed_for_plain_name():
    # Sanity check: the escaping doesn't corrupt an ordinary name.
    xml_str = cups_admin._avahi_service_xml("Office Brother", "Office_Brother")
    root = ET.fromstring(xml_str)
    assert root.find("name").text == "Office Brother @ %h"
