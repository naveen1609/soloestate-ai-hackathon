def pytest_addoption(parser):
    parser.addoption("--run-tool-db-tests", action="store_true", default=False,
                     help="Run rollback-only tool integration tests on the configured application database")
    parser.addoption("--run-agent-db-tests", action="store_true", default=False,
                     help="Run rollback-only agent persistence tests on the configured application database")
    parser.addoption("--run-integrity-db-tests", action="store_true", default=False,
                     help="Run owner/recovery and isolated concurrent integrity checks against Supabase")
