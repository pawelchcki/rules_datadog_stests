"""Second half of a controlled circular-import pair.

Importing this module triggers a lazy back-import of circular_a at call time;
tracer/instrumentation must not turn that into a circular ImportError.
"""
import circular_a


def describe():
    return "circular-b:" + circular_a.describe()


def roundtrip():
    return circular_a.load_b()
