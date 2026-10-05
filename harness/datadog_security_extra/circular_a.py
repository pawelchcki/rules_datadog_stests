"""First half of a controlled circular-import pair (imported by circular_b)."""


def describe():
    return "circular-a"


def load_b():
    import circular_b

    return circular_b.describe()
