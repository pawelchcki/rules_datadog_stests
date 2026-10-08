"""Regression checks for valid subsets and byte-limit or value corruption failures."""
from itertools import permutations
import unittest

from baggage_cases import MAX_BYTES, UPSTREAM_CASE, UPSTREAM_SELECTOR, adapt_case, assert_baggage_header


class BaggageLimitsTests(unittest.TestCase):
    def setUp(self):
        self.baggage = {"key1": "a" * (MAX_BYTES // 3), "key2": "b" * (MAX_BYTES // 3),
                        "key3": "c" * (MAX_BYTES // 3), "key4": "d"}

    def header(self, keys):
        return [("baggage", ",".join(key + "=" + self.baggage[key] for key in keys))]

    def test_two_and_three_intact_items_pass_in_every_order(self):
        for keys in (('key1', 'key2'), ('key1', 'key2', 'key4')):
            for order in permutations(keys):
                with self.subTest(order=order):
                    assert_baggage_header(self.header(order), self.baggage)

    def test_overflow_truncation_duplicates_and_missing_headers_fail(self):
        for headers in (self.header(self.baggage), self.header(('key1', 'key1')),
                        [('baggage', 'key1=a')], [('baggage', 'unknown=value')],
                        [('baggage', 'key4=d,')], [('baggage', '')], [],
                        [('baggage', 'key4=d'), ('Baggage', 'key4=d')]):
            with self.subTest(header_lengths=[len(value) for _, value in headers]):
                with self.assertRaises(AssertionError):
                    assert_baggage_header(headers, self.baggage)

    def test_below_limit_control_cannot_drop_items(self):
        control = {key: self.baggage[key] for key in ('key1', 'key2', 'key4')}
        assert_baggage_header(self.header(control), control, require_all=True)
        with self.assertRaises(AssertionError):
            assert_baggage_header(self.header(('key1', 'key2')), control, require_all=True)

    def test_only_the_order_dependent_case_is_adapted(self):
        original = dict(name=UPSTREAM_CASE, parameters={}, features=['datadog_headers_propagation'])
        adapted = adapt_case(original)
        self.assertEqual(original['name'], UPSTREAM_CASE)
        self.assertEqual(adapted['adaptedFrom'], UPSTREAM_SELECTOR)
        self.assertEqual(adapted['file'], 'baggage_cases.py')
        self.assertTrue(adapted['local'])
        # The old exact-count skip must not mask failures of the corrected
        # local size/value checks: its actual selector is a different method.
        self.assertNotIn('upstreamSelector', adapted)
        other = dict(name='test_headers_baggage.Test_Headers_Baggage.test_baggage_inject_header_D004[0]')
        self.assertIs(adapt_case(other), other)


if __name__ == '__main__':
    unittest.main()
