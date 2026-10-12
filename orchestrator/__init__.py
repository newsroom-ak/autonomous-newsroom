"""Harness that moves tickets between the Hermes and OpenClaw agents of the newsroom team.

The harness does plumbing only: git worktrees, GitHub API calls under each agent's own
token, offline test execution, and the token ledger. Every decision (ticket breakdown,
assignment, code, review verdict, acceptance tests) is made by an agent.
"""
