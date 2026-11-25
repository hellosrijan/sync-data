from django_dramatiq.management.commands.rundramatiq import Command as RunDramatiq


class Command(RunDramatiq):
    """django_dramatiq's rundramatiq, with --reload working for files in app packages.

    dramatiq's default --watch-include is "**.py". watchdog>=7 on Python 3.13+ matches
    with PurePath.full_match, where "**.py" only matches top-level files, so edits
    under sync/, utils/, ... never trigger a reload. rundramatiq can't pass
    --watch-include through, so we splice it in ahead of the task module list.
    """

    # ponytail: piggybacks on discover_tasks_modules() output going last in argv;
    # drop this file once dramatiq's default pattern becomes "**/*.py".
    def discover_tasks_modules(self):
        return ["--watch-include", "**/*.py", *super().discover_tasks_modules()]
