"""Order-independent adaptation of the pinned upstream baggage byte-limit check."""
from itertools import permutations

UPSTREAM_CASE = "test_headers_baggage.Test_Headers_Baggage.test_baggageheader_maxbytes_inject_D017[0]"
UPSTREAM_SELECTOR = "tests/parametric/test_headers_baggage.py::Test_Headers_Baggage::test_baggageheader_maxbytes_inject_D017"
MAX_BYTES = 8192


def assert_baggage_header(headers, expected, require_all=False):
    values = [value for key, value in headers if key.lower() == "baggage"]
    assert len(values) == 1, headers
    header = values[0]
    assert 0 < len(header.encode("utf-8")) <= MAX_BYTES, "Baggage exceeds the byte limit or is empty"
    items = header.split(",")
    actual = {}
    for item in items:
        key, separator, value = item.partition("=")
        assert separator and key in expected, "Invalid or unknown baggage member: " + item
        assert key not in actual, "Duplicate baggage member: " + key
        assert value == expected[key], "Truncated or changed baggage value: " + key
        actual[key] = value
    if require_all:
        assert actual == expected, "Baggage below the byte limit must propagate without loss"


class Test_BaggageLimits:
    def test_maxbytes_inject(self, test_library):
        baggage = {"key1": "a" * (MAX_BYTES // 3), "key2": "b" * (MAX_BYTES // 3),
                   "key3": "c" * (MAX_BYTES // 3), "key4": "d"}
        # Below the limit, every member must survive. This also rules out an
        # implementation which always drops baggage to satisfy the size bound.
        control = {key: baggage[key] for key in ("key1", "key2", "key4")}
        with test_library.dd_start_span("baggage.maxbytes.control") as span:
            for key, value in control.items():
                span.set_baggage(key, value)
            headers = test_library.dd_inject_headers(span.span_id)
        assert_baggage_header(headers, control, require_all=True)

        # The specification permits choosing which over-limit members to drop.
        # Exercise every insertion order; two or three intact members can fit.
        for order in permutations(baggage):
            with test_library.dd_start_span("baggage.maxbytes.limit") as span:
                for key in order:
                    span.set_baggage(key, baggage[key])
                headers = test_library.dd_inject_headers(span.span_id)
            assert_baggage_header(headers, baggage)


def adapt_case(case):
    if case["name"] != UPSTREAM_CASE:
        return case
    return dict(case, name="baggage_cases.Test_BaggageLimits.test_maxbytes_inject[0]",
                file="baggage_cases.py", **{"class": "Test_BaggageLimits"}, method="test_maxbytes_inject",
                instance=Test_BaggageLimits(), function=Test_BaggageLimits.test_maxbytes_inject,
                local=True, adaptedFrom=UPSTREAM_SELECTOR)
