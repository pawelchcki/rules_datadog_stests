"""Application validators configured through the public IAST controls setting."""
def sanitize(value):
    return value


def validate(value):
    return bool(value)


def different(value):
    return value
