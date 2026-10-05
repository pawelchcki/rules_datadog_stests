"""Ordinary application code instrumented through signed remote configuration."""
class DebuggerController:
    def calculate(self, value):
        return value * 2


def calculate(value):
    return DebuggerController().calculate(value)


def fail(value):
    local_value = value + 1
    raise ValueError("debugger-extra-error:" + str(local_value))
