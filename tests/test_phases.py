import pytest

from praxium.agent.phases import ADVANCE_PHASE, PHASE_TOOLS, Phase, PhaseError, PhaseMachine


def machine_in(phase: Phase) -> PhaseMachine:
    return PhaseMachine(phase)


def test_starts_in_evaluate() -> None:
    assert PhaseMachine().phase is Phase.EVALUATE


def test_tools_per_phase() -> None:
    assert machine_in(Phase.EVALUATE).allows("read_file")
    assert not machine_in(Phase.EVALUATE).allows("write_file")
    assert not machine_in(Phase.IDEATE).allows("run_command")
    assert machine_in(Phase.BUILD).allows("write_file")
    assert machine_in(Phase.TEST).allows("run_command")
    assert not machine_in(Phase.TEST).allows("write_file")
    assert PHASE_TOOLS[Phase.DONE] == frozenset()
    assert all(ADVANCE_PHASE in PHASE_TOOLS[p] for p in Phase if p is not Phase.DONE)


def test_illegal_move_names_allowed_moves() -> None:
    with pytest.raises(PhaseError, match="ideate"):
        machine_in(Phase.EVALUATE).check_move(Phase.BUILD, verified=None)


def test_cannot_finish_verified_without_a_passing_check() -> None:
    machine = machine_in(Phase.TEST)
    with pytest.raises(PhaseError, match="No check has passed"):
        machine.check_move(Phase.DONE, verified=True)


def test_cannot_finish_verified_without_check_after_last_write() -> None:
    machine = machine_in(Phase.TEST)
    machine.record_check(True)
    machine.record_write()
    with pytest.raises(PhaseError):
        machine.check_move(Phase.DONE, verified=True)

    machine.record_check(True)
    machine.check_move(Phase.DONE, verified=True)


def test_failed_check_does_not_count() -> None:
    machine = machine_in(Phase.TEST)
    machine.record_write()
    machine.record_check(False)
    with pytest.raises(PhaseError):
        machine.check_move(Phase.DONE, verified=True)


def test_unverified_done_always_allowed_from_test() -> None:
    machine = machine_in(Phase.TEST)
    machine.record_write()
    machine.check_move(Phase.DONE, verified=False)
    machine.move(Phase.DONE, verified=False)
    assert machine.verified is False


def test_test_can_go_back_to_build_and_any_phase_to_ideate() -> None:
    machine_in(Phase.TEST).check_move(Phase.BUILD, verified=None)
    for phase in (Phase.IDEATE, Phase.BUILD, Phase.TEST):
        machine_in(phase).check_move(Phase.IDEATE, verified=None)


def test_done_starts_new_cycle() -> None:
    machine = machine_in(Phase.TEST)
    machine.record_check(True)
    machine.move(Phase.DONE, verified=True)
    assert machine.verified is True

    assert machine.start_cycle() is True
    assert machine.phase is Phase.EVALUATE
    assert machine.verified is None
    assert machine.start_cycle() is False


def test_a_new_cycle_cannot_reuse_the_last_cycles_check() -> None:
    machine = machine_in(Phase.TEST)
    machine.record_write()
    machine.record_check(True)
    machine.move(Phase.DONE, verified=True)
    machine.start_cycle()
    machine.move(Phase.TEST)
    with pytest.raises(PhaseError):
        machine.check_move(Phase.DONE, verified=True)
