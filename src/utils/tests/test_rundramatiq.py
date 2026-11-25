from unittest import mock

from django.core.management import call_command, get_commands


def test_utils_override_wins_over_django_dramatiq():
    assert get_commands()["rundramatiq"] == "utils"


def test_argv_has_watch_include_before_task_modules():
    # Parent handle() ends in os.execvp(executable_path, process_args); capture it.
    with mock.patch("django_dramatiq.management.commands.rundramatiq.os.execvp") as execvp:
        call_command("rundramatiq", "--reload", "--processes", "1", "--threads", "2")
    argv = execvp.call_args.args[1]
    i = argv.index("--watch-include")
    assert argv[i + 1] == "**/*.py"
    assert argv.index("--watch") < i  # --reload still passed through
    assert i + 2 == argv.index("django_dramatiq.setup")  # task modules follow directly
    assert argv.count("--watch-include") == 1
