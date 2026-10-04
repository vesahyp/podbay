import os
import subprocess

import pytest


def test_a_test_cannot_signal_a_process_it_did_not_start():
    with pytest.raises(AssertionError):
        os.kill(os.getppid(), 15)
    with pytest.raises(AssertionError):
        os.killpg(os.getpgrp(), 15)
    os.kill(os.getpid(), 0)  # the liveness probe stays allowed


@pytest.mark.parametrize("command", [["pkill", "-f", "podbay"], ["killall", "podbay"], ["kill", "-TERM", "1"]])
def test_a_test_cannot_run_a_kill_command(command):
    with pytest.raises(AssertionError):
        subprocess.Popen(command)
