Human review of your previous targeted re-test (keep all other tests unchanged):

1. `test_network_failures_record_error` asserts `pytest.raises(SystemExit)`. That encodes the
   defect as expected behaviour. Requirement 3 of the focus list says the refresh function the
   scheduler will reuse must NOT let `SystemExit` escape. Change this test so that calling
   `refresh._run_refresh()` with a connection error or HTTP 500 returns normally (no exception
   of any kind, including SystemExit) and records status "error" with the message. Keep the
   CLI exit-code behaviour out of this test.
2. `test_malformed_and_invalid_feeds` asserts `articles_fetched == 3`. The ticket does not
   define whether skipped entries count as fetched. Remove only that one assertion; keep the
   rest of the test.

Do not change any other test. If the implementation does not meet requirement 3, the test
must fail; do not weaken it.
