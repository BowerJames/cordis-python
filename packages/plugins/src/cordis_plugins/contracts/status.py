from cordis import Key

# The status a program should exit with, when a component is what decides it: a prompt that ran and failed, a search
# that found nothing. A component provides it once it knows, and whoever runs the composition reads it once the runtime
# is done, before closing it. A key has one provider, so one component decides; a composition in which none provides it
# exits with 0, or with 1 when something failed.
EXIT_STATUS: Key[int] = Key("exit_status")
