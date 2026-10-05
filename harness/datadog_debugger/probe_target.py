"""Small ordinary functions instrumented by remotely installed debugger probes."""

def calculate(value, password):
    doubled = value * 2
    message = "result:" + str(doubled)  # LINE_PROBE
    return message


def budget(value):
    result = value + 1  # BUDGET_PROBE
    return result
