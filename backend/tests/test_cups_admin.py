"""Unit tests for CUPS queue provisioning argv (``app.services.cups_admin``).

Pure argv-capture tests: ``_run`` (the subprocess wrapper) and
``_write_avahi_service`` (filesystem + avahi reload) are replaced with
recorders, so no CUPS/Avahi/subprocess is touched. They lock in the two
guarantees the "Papyrus default queue" fix depends on: every created queue
carries ``printer-error-policy=abort-job``, and the built-in default queue is
created WITHOUT a second Avahi advert (airprint.service already advertises it).
"""
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
    await cups_admin.add_network_queue("Office", "Office")

    argv = _lpadmin_for(run_calls, "Office")
    assert argv[argv.index("-v") + 1] == "papyrus:/"
    assert "printer-error-policy=abort-job" in argv
    assert avahi_writes == [("Office", "Office")]


async def test_add_physical_printer_sets_abort_job_on_both_queues(run_calls, avahi_writes):
    await cups_admin.add_physical_printer("Office", "Office", "ipp://printer/ipp")

    hold = _lpadmin_for(run_calls, "Office")
    assert hold[hold.index("-v") + 1] == "papyrus:/"
    assert "printer-error-policy=abort-job" in hold

    release = _lpadmin_for(run_calls, "Office_release")
    assert release[release.index("-v") + 1] == "ipp://printer/ipp"
    assert "everywhere" in release
    assert "printer-error-policy=abort-job" in release
