# -*- coding: utf-8 -*-
"""Live integration tests for role_router.py using FreeFlow LLM provider.

These tests verify that the role router works correctly with a real LLM backend.
They are opt-in and only run when RUN_LIVE_LLM_TESTS=1 is set.

Test modes:
- Smoke tests (marker: smoke): 1-2 cases per role, ~6 requests total
  Run: env RUN_LIVE_LLM_TESTS=1 pytest tests/test_role_router_live.py -m "live_llm and smoke"

- Full matrix: all 36 test cases (requires RUN_FULL_LIVE_LLM_MATRIX=1)
  Run: env RUN_LIVE_LLM_TESTS=1 RUN_FULL_LIVE_LLM_MATRIX=1 pytest tests/test_role_router_live.py -m live_llm

Requirements:
- FreeFlow LLM provider configured in ~/.kvist/config.json
- At least one backend (Gemini or Groq) with valid API key
- freeflow-llm package installed
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import pytest

import role_router
from llm_clients import (
    FREEFLOW_PROVIDER,
    configured_freeflow_backends,
    create_llm_client,
    infer_freeflow_backend,
    required_secret_keys_for_provider,
    resolve_models_for_provider,
)
from role_router import RouteDecision, route_role_decision_with_llm, _match_direct_rule
from user_config import get_saved_models, load_available_credentials


# ── Test result tracking ─────────────────────────────────────────────

@dataclass
class LiveRouteResult:
    """Single test result for JSON reporting."""
    group: str
    input: str
    expected_role: str
    actual_role: str
    source: str
    model: str
    raw_response: str
    status: str  # "passed", "failed", "skipped"
    skip_reason: str = ""
    matched_rule: str = ""
    error: str = ""


_TEST_RESULTS: list[LiveRouteResult] = []


def _record_result(result: LiveRouteResult) -> None:
    """Record a test result for JSON reporting."""
    _TEST_RESULTS.append(result)


def _save_json_report() -> None:
    """Save test results to JSON report."""
    report_dir = Path.home() / ".kvist" / "test-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "role-router-live.json"

    # Count results
    passed = sum(1 for r in _TEST_RESULTS if r.status == "passed")
    failed = sum(1 for r in _TEST_RESULTS if r.status == "failed")
    skipped = sum(1 for r in _TEST_RESULTS if r.status == "skipped")
    total = len(_TEST_RESULTS)

    # Separate routing cases from infrastructure tests
    routing_cases = [r for r in _TEST_RESULTS if r.group != "infrastructure"]
    infrastructure_tests = [r for r in _TEST_RESULTS if r.group == "infrastructure"]

    routing_passed = sum(1 for r in routing_cases if r.status == "passed")
    routing_failed = sum(1 for r in routing_cases if r.status == "failed")
    routing_skipped = sum(1 for r in routing_cases if r.status == "skipped")

    infra_passed = sum(1 for r in infrastructure_tests if r.status == "passed")
    infra_failed = sum(1 for r in infrastructure_tests if r.status == "failed")

    # Determine coverage status
    coverage_status = "complete"
    if skipped > 0:
        coverage_status = "incomplete"

    report = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "coverage": coverage_status,
        },
        "routing_cases": {
            "total": len(routing_cases),
            "passed": routing_passed,
            "failed": routing_failed,
            "skipped": routing_skipped,
        },
        "infrastructure_tests": {
            "total": len(infrastructure_tests),
            "passed": infra_passed,
            "failed": infra_failed,
        },
        "results": [asdict(r) for r in _TEST_RESULTS],
    }

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nJSON report saved to: {report_path}")
    print(f"Summary: {passed} passed, {failed} failed, {skipped} skipped (coverage: {coverage_status})")
    print(f"  Routing cases: {routing_passed}/{len(routing_cases)} passed")
    print(f"  Infrastructure: {infra_passed}/{len(infrastructure_tests)} passed")



# ── Test case definition ─────────────────────────────────────────────


@dataclass
class LiveTestCase:
    """A single live test case for role routing."""

    group: str
    input: str
    expected_role: str
    description: str = ""
    smoke: bool = False  # Mark as smoke test


# ── Live test matrix ─────────────────────────────────────────────────

# Smoke tests: 1-2 cases per role, ~6 requests total
SMOKE_TEST_CASES: list[LiveTestCase] = [
    LiveTestCase(
        group="A_clear_chat",
        input="I need guidance on selecting architecture for an automation assistant",
        expected_role="chat",
        description="Architecture guidance without file/code action",
        smoke=True,
    ),
    LiveTestCase(
        group="B_clear_coder",
        input="Inspect the repository structure and determine why the test suite fails",
        expected_role="coder",
        description="Repository inspection and analysis",
        smoke=True,
    ),
    LiveTestCase(
        group="C_clear_devops",
        input="Inspect the Linux service deployment, check reverse proxy and TLS runtime state, and report why rollout is failing",
        expected_role="devops",
        description="DevOps diagnostics with explicit server/deployment action",
        smoke=True,
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="Can you help with this project",
        expected_role="chat",
        description="Generic help request without specific action",
        smoke=True,
    ),
    LiveTestCase(
        group="E_mixed_intent",
        input="Describe the architecture and then inspect the repository for weak points",
        expected_role="coder",
        description="Explanation + repository inspection",
        smoke=True,
    ),
    LiveTestCase(
        group="F_garbage",
        input="asdf qwer zxcv",
        expected_role="chat",
        description="Random characters",
        smoke=True,
    ),
]

# Full test matrix: all 36 test cases
LIVE_TEST_CASES: list[LiveTestCase] = [
    # GROUP A: clear chat (6 cases)
    # These should NOT match direct rules and should go through LLM
    LiveTestCase(
        group="A_clear_chat",
        input="I need guidance on selecting architecture for an automation assistant",
        expected_role="chat",
        description="Architecture guidance without file/code action",
        smoke=True,  # SMOKE TEST
    ),
    LiveTestCase(
        group="A_clear_chat",
        input="Local versus cloud coding agents: which approach works better",
        expected_role="chat",
        description="Comparison question without action (no direct rule keywords)",
    ),
    LiveTestCase(
        group="A_clear_chat",
        input="Best approach for handling user prompts safely",
        expected_role="chat",
        description="Best practices question (no 'what is' keyword)",
    ),
    LiveTestCase(
        group="A_clear_chat",
        input="Using one general agent versus multiple specialized agents",
        expected_role="chat",
        description="Comparison without implementation (no 'compare' keyword)",
    ),
    LiveTestCase(
        group="A_clear_chat",
        input="I want to learn how routing decisions should be designed",
        expected_role="chat",
        description="Understanding request without code changes (no 'help me understand')",
    ),
    LiveTestCase(
        group="A_clear_chat",
        input="Give me guidance on improving the UX of a CLI assistant",
        expected_role="chat",
        description="UX guidance without implementation",
    ),
    # GROUP B: clear coder (6 cases)
    # These should NOT match direct rules and should go through LLM
    LiveTestCase(
        group="B_clear_coder",
        input="Inspect the repository structure and determine why the test suite fails",
        expected_role="coder",
        description="Repository inspection and analysis",
        smoke=True,  # SMOKE TEST
    ),
    LiveTestCase(
        group="B_clear_coder",
        input="Find the source of the parser bug in the project files",
        expected_role="coder",
        description="Bug investigation in project files",
    ),
    LiveTestCase(
        group="B_clear_coder",
        input="Review the implementation and identify what code needs to change",
        expected_role="coder",
        description="Code review and change identification",
    ),
    LiveTestCase(
        group="B_clear_coder",
        input="Look at the git diff and tell me what changed",
        expected_role="coder",
        description="Git diff analysis (no 'check' keyword)",
    ),
    LiveTestCase(
        group="B_clear_coder",
        input="Analyze the existing source code and locate the broken behavior",
        expected_role="coder",
        description="Source code analysis",
    ),
    LiveTestCase(
        group="B_clear_coder",
        input="Execute the project tests and report failures",
        expected_role="coder",
        description="Test execution and reporting (no 'run' keyword)",
    ),
    # GROUP C: clear devops (6 cases)
    # These should NOT match direct rules and should go through LLM
    LiveTestCase(
        group="C_clear_devops",
        input="Prepare rollout guidance for running a Linux service behind TLS and a reverse proxy",
        expected_role="devops",
        description="Deployment guidance with infrastructure",
        smoke=True,  # SMOKE TEST
    ),
    LiveTestCase(
        group="C_clear_devops",
        input="Look at the server runtime state and diagnose why the service is unavailable",
        expected_role="devops",
        description="Server diagnostics (no 'check' keyword)",
    ),
    LiveTestCase(
        group="C_clear_devops",
        input="Outline steps for VPS with nginx and SSL",
        expected_role="devops",
        description="VPS deployment preparation (no 'deploy' keyword)",
    ),
    LiveTestCase(
        group="C_clear_devops",
        input="Inspect production logs and service status for a failing daemon",
        expected_role="devops",
        description="Production diagnostics",
    ),
    LiveTestCase(
        group="C_clear_devops",
        input="Set up operational monitoring for a deployed Linux service",
        expected_role="devops",
        description="Monitoring setup",
    ),
    LiveTestCase(
        group="C_clear_devops",
        input="Diagnose why the reverse proxy is not forwarding traffic",
        expected_role="devops",
        description="Reverse proxy diagnostics",
    ),
    # GROUP D: ambiguous (8 cases)
    # Default to chat when no clear action
    LiveTestCase(
        group="D_ambiguous",
        input="Make it better",
        expected_role="chat",
        description="Vague improvement request",
        smoke=True,  # SMOKE TEST
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="Can you help with this project",
        expected_role="chat",
        description="Generic help request",
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="Something is broken, what should I do",
        expected_role="chat",
        description="Problem without specifics",
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="I want this to work properly",
        expected_role="chat",
        description="Desire without action",
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="What should be the next step",
        expected_role="chat",
        description="Next step question",
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="This agent feels unstable",
        expected_role="chat",
        description="Observation without action",
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="I am not sure whether this is a code problem or infrastructure problem",
        expected_role="chat",
        description="Uncertainty about problem type",
    ),
    LiveTestCase(
        group="D_ambiguous",
        input="How should I think about fixing this",
        expected_role="chat",
        description="Approach question",
    ),
    # GROUP E: mixed intent (6 cases)
    # Role determined by required action
    LiveTestCase(
        group="E_mixed_intent",
        input="Describe the architecture and then inspect the repository for weak points",
        expected_role="coder",
        description="Explanation + repository inspection (no 'explain' keyword)",
        smoke=True,  # SMOKE TEST
    ),
    LiveTestCase(
        group="E_mixed_intent",
        input="Give advice about deployment and then prepare VPS rollout steps",
        expected_role="devops",
        description="Advice + deployment preparation",
    ),
    LiveTestCase(
        group="E_mixed_intent",
        input="Explain Docker without changing anything",
        expected_role="chat",
        description="Explanation without action",
    ),
    LiveTestCase(
        group="E_mixed_intent",
        input="Create a docker compose file for this project",
        expected_role="coder",
        description="File creation in project",
    ),
    LiveTestCase(
        group="E_mixed_intent",
        input="Run docker compose on the server and check logs",
        expected_role="devops",
        description="Server runtime action",
    ),
    LiveTestCase(
        group="E_mixed_intent",
        input="Review the code before we deploy it",
        expected_role="coder",
        description="Code review action",
    ),
    # GROUP F: garbage / out-of-domain (4 cases)
    # Default to chat
    LiveTestCase(
        group="F_garbage",
        input="asdf qwer zxcv",
        expected_role="chat",
        description="Random characters",
        smoke=True,  # SMOKE TEST
    ),
    LiveTestCase(
        group="F_garbage",
        input="просто сделай нормально",
        expected_role="chat",
        description="Vague Russian request",
    ),
    LiveTestCase(
        group="F_garbage",
        input="оно не то",
        expected_role="chat",
        description="Vague Russian complaint",
    ),
    LiveTestCase(
        group="F_garbage",
        input="12345 ??? help",
        expected_role="chat",
        description="Numbers and symbols",
    ),
]


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def skip_if_not_enabled():
    """Skip live tests unless RUN_LIVE_LLM_TESTS=1 is set."""
    if os.getenv("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Live LLM tests are opt-in. Set RUN_LIVE_LLM_TESTS=1 to run.")


@pytest.fixture(scope="module")
def skip_if_full_matrix_not_enabled(request):
    """Skip full matrix tests unless RUN_FULL_LIVE_LLM_MATRIX=1 is set."""
    # Only check if this is NOT a smoke test
    if "smoke" not in request.keywords:
        if os.getenv("RUN_FULL_LIVE_LLM_MATRIX") != "1":
            pytest.skip("Full matrix tests require RUN_FULL_LIVE_LLM_MATRIX=1")


@pytest.fixture(scope="session", autouse=True)
def save_json_report_on_exit():
    """Save JSON report at the end of the test session."""
    yield
    if _TEST_RESULTS:
        _save_json_report()


@pytest.fixture(scope="module")
def skip_if_freeflow_not_installed():
    """Skip if freeflow-llm package is not installed."""
    try:
        import freeflow_llm  # noqa: F401
    except ImportError:
        pytest.skip("freeflow-llm package is not installed")


@pytest.fixture(scope="module")
def freeflow_credentials(skip_if_not_enabled, skip_if_freeflow_not_installed):
    """Load FreeFlow credentials from config.json."""
    # Get saved models for FreeFlow provider
    saved_models = get_saved_models(FREEFLOW_PROVIDER)
    if not saved_models:
        pytest.skip("No FreeFlow models configured in ~/.kvist/config.json")

    # Resolve models
    resolved_models, warnings = resolve_models_for_provider(FREEFLOW_PROVIDER, saved_models)
    if warnings:
        for warning in warnings:
            print(f"Warning: {warning}")

    # Determine required keys
    model_list = list(resolved_models.values())
    required_keys = required_secret_keys_for_provider(FREEFLOW_PROVIDER, model_list)

    # Load credentials
    credentials = load_available_credentials(required_keys)
    if not credentials:
        pytest.skip(
            f"No FreeFlow credentials found in ~/.kvist/config.json. "
            f"Required keys: {', '.join(required_keys)}"
        )

    return {
        "credentials": credentials,
        "models": resolved_models,
        "required_keys": required_keys,
    }


@pytest.fixture(scope="module")
def freeflow_client(freeflow_credentials):
    """Create FreeFlow LLM client."""
    credentials = freeflow_credentials["credentials"]
    try:
        client = create_llm_client(FREEFLOW_PROVIDER, credentials, verbose=False)
        return client
    except RuntimeError as error:
        pytest.skip(f"Failed to create FreeFlow client: {error}")


def _validate_model_backend_available(model: str, credentials: dict[str, str]) -> tuple[bool, str]:
    """Check whether the model's required backend is configured.

    Returns (is_available, reason).
    """
    backend = infer_freeflow_backend(model)
    if backend is None:
        return False, f"Cannot infer backend for model '{model}'"
    configured = configured_freeflow_backends(credentials)
    if backend not in configured:
        return False, (
            f"Model '{model}' requires {backend.capitalize()} backend, "
            f"but only {', '.join(b.capitalize() for b in configured) if configured else 'no backends'} "
            f"are configured. Add {backend.capitalize()} API key to ~/.kvist/config.json."
        )
    return True, ""


@pytest.fixture(scope="module")
def fast_model(freeflow_credentials):
    """Get the fast model for routing.

    Prefer Groq models over Gemini to avoid rate limits during testing.
    If the preferred model's backend is not configured, fall back to
    whatever model/backend is actually available, or skip with a clear reason.
    """
    models = freeflow_credentials["models"]
    credentials = freeflow_credentials["credentials"]

    # Try to use a Groq model first (more stable for testing)
    groq_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    for groq_model in groq_models:
        for role, model in models.items():
            if model == groq_model:
                ok, reason = _validate_model_backend_available(model, credentials)
                if ok:
                    return model
                # Groq model is configured but backend unavailable — skip
                pytest.skip(f"Cannot use Groq model for live tests: {reason}")

    # Fallback to configured fast model
    fast = models.get("fast")
    if not fast:
        pytest.skip("No fast model configured for FreeFlow provider")

    # Validate the fast model's backend is available
    ok, reason = _validate_model_backend_available(fast, credentials)
    if not ok:
        pytest.skip(f"Live tests skipped: {reason}")

    # If fast model is Gemini and Groq is available, prefer Groq for rate-limit reasons
    if fast.startswith("gemini-"):
        if "groq" in configured_freeflow_backends(credentials):
            return "llama-3.3-70b-versatile"

    return fast


# ── Helper functions ─────────────────────────────────────────────────


def is_llm_error_skippable(error_msg: str) -> tuple[bool, str]:
    """Check if an LLM error should cause a skip rather than a failure."""
    lowered = error_msg.lower()
    skippable_patterns = [
        ("rate limit", "Rate limit exceeded"),
        ("quota", "Quota exceeded"),
        ("timeout", "Request timeout"),
        ("authentication", "Authentication error"),
        ("unauthorized", "Unauthorized"),
        ("no healthy backend available", "No healthy backend available"),
        ("backend unavailable", "Backend unavailable"),
        ("unavailable or misconfigured", "Backend misconfigured"),
        ("invalid api key", "Invalid API key"),
        ("not found for api version", "Model not found (provider/model mismatch)"),
        ("not found", "Resource not found"),
        ("high demand", "Provider overloaded (transient)"),
        ("all providers exhausted", "All providers exhausted"),
    ]
    for pattern, reason in skippable_patterns:
        if pattern in lowered:
            return True, reason
    return False, ""


# ── Live tests ───────────────────────────────────────────────────────


@pytest.mark.live_llm
@pytest.mark.smoke
@pytest.mark.parametrize("case", SMOKE_TEST_CASES, ids=lambda c: f"{c.group}_{c.input[:40]}")
def test_smoke_role_routing(case: LiveTestCase, freeflow_client, fast_model, monkeypatch, request):
    """Smoke test: minimal role routing validation with real FreeFlow LLM."""
    _run_role_routing_test(case, freeflow_client, fast_model, monkeypatch, request)


@pytest.mark.live_llm
@pytest.mark.parametrize("case", LIVE_TEST_CASES, ids=lambda c: f"{c.group}_{c.input[:40]}")
def test_live_role_routing(case: LiveTestCase, freeflow_client, fast_model, monkeypatch, skip_if_full_matrix_not_enabled, request):
    """Test role routing with real FreeFlow LLM (full matrix)."""
    _run_role_routing_test(case, freeflow_client, fast_model, monkeypatch, request)


def _run_role_routing_test(case: LiveTestCase, freeflow_client, fast_model, monkeypatch, request):
    """Shared test logic for smoke and full matrix tests."""

    # Patch router prompt loader to avoid file read issues in tests
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: role_router._ROUTER_PROMPT_PATH.read_text(encoding="utf-8").strip())

    # Check if this case should match a direct rule
    direct_match = _match_direct_rule(case.input)

    # Call the router
    try:
        decision: RouteDecision = route_role_decision_with_llm(
            user_goal=case.input,
            client=freeflow_client,
            model=fast_model,
            temperature=0.0,
        )
    except Exception as error:
        # Check if this is a skippable error
        is_skippable, skip_reason = is_llm_error_skippable(str(error))

        # Record result
        _record_result(LiveRouteResult(
            group=case.group,
            input=case.input,
            expected_role=case.expected_role,
            actual_role="",
            source="",
            model=fast_model,
            raw_response="",
            status="skipped",
            skip_reason=skip_reason if is_skippable else f"Error: {error}",
        ))

        if is_skippable:
            pytest.skip(f"LLM provider error: {skip_reason}")
        # Not skippable - this is a real failure
        pytest.fail(
            f"LLM call failed with non-skippable error:\n"
            f"  Group: {case.group}\n"
            f"  Input: {case.input}\n"
            f"  Error: {error}"
        )

    # Collect diagnostic data
    diagnostic = {
        "group": case.group,
        "input": case.input,
        "expected_role": case.expected_role,
        "direct_match": direct_match,
        "actual_role": decision.role,
        "source": decision.source,
        "raw_response": decision.raw_response,
        "matched_rule": decision.matched_rule,
        "error": decision.error,
        "model": decision.model,
    }

    # If there's an error in the decision, check if it's skippable
    if decision.error:
        is_skippable, skip_reason = is_llm_error_skippable(decision.error)

        # Record result
        _record_result(LiveRouteResult(
            group=case.group,
            input=case.input,
            expected_role=case.expected_role,
            actual_role=decision.role,
            source=decision.source,
            model=decision.model,
            raw_response=decision.raw_response,
            status="skipped",
            skip_reason=skip_reason,
            error=decision.error,
        ))

        if is_skippable:
            pytest.skip(f"LLM provider error: {skip_reason}")

    # For groups A, B, C (clear cases that should go through LLM):
    # Verify that direct_match is None and source is "llm"
    if case.group.startswith(("A_", "B_", "C_")):
        if direct_match is not None:
            _record_result(LiveRouteResult(
                group=case.group,
                input=case.input,
                expected_role=case.expected_role,
                actual_role=decision.role,
                source=decision.source,
                model=decision.model,
                raw_response=decision.raw_response,
                status="failed",
                matched_rule=str(direct_match),
            ))
            pytest.fail(
                f"Test case should NOT match direct rule but it did:\n"
                f"  Group: {case.group}\n"
                f"  Input: {case.input}\n"
                f"  Matched rule: {direct_match}\n"
                f"  Fix: Reformulate test case to avoid direct rule match"
            )

        if decision.source != "llm":
            _record_result(LiveRouteResult(
                group=case.group,
                input=case.input,
                expected_role=case.expected_role,
                actual_role=decision.role,
                source=decision.source,
                model=decision.model,
                raw_response=decision.raw_response,
                status="failed",
                matched_rule=decision.matched_rule,
            ))
            pytest.fail(
                f"Test case should use LLM routing but used {decision.source}:\n"
                f"  Group: {case.group}\n"
                f"  Input: {case.input}\n"
                f"  Source: {decision.source}\n"
                f"  Matched rule: {decision.matched_rule}"
            )

        # Verify LLM was actually called
        if not decision.raw_response:
            _record_result(LiveRouteResult(
                group=case.group,
                input=case.input,
                expected_role=case.expected_role,
                actual_role=decision.role,
                source=decision.source,
                model=decision.model,
                raw_response=decision.raw_response,
                status="failed",
            ))
            pytest.fail(
                f"LLM source but no raw_response:\n"
                f"  Group: {case.group}\n"
                f"  Input: {case.input}"
            )

    # Check if role matches expectation
    if decision.role != case.expected_role:
        # Record failure
        _record_result(LiveRouteResult(
            group=case.group,
            input=case.input,
            expected_role=case.expected_role,
            actual_role=decision.role,
            source=decision.source,
            model=decision.model,
            raw_response=decision.raw_response,
            status="failed",
            matched_rule=decision.matched_rule,
            error=decision.error,
        ))

        # Build detailed failure message
        failure_msg = (
            f"\n{'='*70}\n"
            f"ROLE MISMATCH\n"
            f"{'='*70}\n"
            f"Group:         {diagnostic['group']}\n"
            f"Input:         {diagnostic['input']}\n"
            f"Expected role: {diagnostic['expected_role']}\n"
            f"Actual role:   {diagnostic['actual_role']}\n"
            f"Source:        {diagnostic['source']}\n"
            f"Direct match:  {diagnostic['direct_match']}\n"
            f"Matched rule:  {diagnostic['matched_rule']}\n"
            f"Model:         {diagnostic['model']}\n"
            f"Raw response:  {diagnostic['raw_response'][:200]}\n"
            f"Error:         {diagnostic['error']}\n"
            f"{'='*70}\n"
        )

        # Provide diagnostic guidance
        if diagnostic['source'] == "llm" and diagnostic['raw_response']:
            raw = diagnostic['raw_response'].strip().lower()
            if case.expected_role in raw:
                failure_msg += (
                    f"\nDIAGNOSTIC: Raw response contains expected role '{case.expected_role}' "
                    f"but parser extracted '{decision.role}'.\n"
                    f"ACTION: Check _parse_llm_role function.\n"
                )
            else:
                failure_msg += (
                    f"\nDIAGNOSTIC: LLM returned wrong role.\n"
                    f"ACTION: Improve prompts/router_prompt.txt with better examples.\n"
                )
        elif diagnostic['source'] == "direct":
            failure_msg += (
                f"\nDIAGNOSTIC: Direct rule matched when it shouldn't.\n"
                f"ACTION: Check if direct rule is too broad or test case needs reformulation.\n"
            )
        elif diagnostic['source'] == "keyword_fallback":
            failure_msg += (
                f"\nDIAGNOSTIC: LLM failed, keyword fallback chose wrong role.\n"
                f"ACTION: Check keyword lists in role_router.py.\n"
            )

        pytest.fail(failure_msg)

    # Record success
    _record_result(LiveRouteResult(
        group=case.group,
        input=case.input,
        expected_role=case.expected_role,
        actual_role=decision.role,
        source=decision.source,
        model=decision.model,
        raw_response=decision.raw_response,
        status="passed",
        matched_rule=decision.matched_rule,
    ))


@pytest.mark.live_llm
@pytest.mark.smoke
def test_live_llm_connection(freeflow_client, fast_model, monkeypatch):
    """Smoke test: verify we can reach the LLM and get a response."""
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: role_router._ROUTER_PROMPT_PATH.read_text(encoding="utf-8").strip())

    try:
        decision = route_role_decision_with_llm(
            user_goal="Create a Python script",
            client=freeflow_client,
            model=fast_model,
            temperature=0.0,
        )
    except Exception as error:
        is_skippable, skip_reason = is_llm_error_skippable(str(error))

        # Record result
        _record_result(LiveRouteResult(
            group="infrastructure",
            input="Create a Python script",
            expected_role="coder",
            actual_role="",
            source="",
            model=fast_model,
            raw_response="",
            status="skipped",
            skip_reason=skip_reason if is_skippable else f"Error: {error}",
        ))

        if is_skippable:
            pytest.skip(f"LLM provider error: {skip_reason}")
        pytest.fail(f"Failed to connect to LLM: {error}")

    # This should match direct rule, so source should be "direct"
    if decision.source != "direct":
        _record_result(LiveRouteResult(
            group="infrastructure",
            input="Create a Python script",
            expected_role="coder",
            actual_role=decision.role,
            source=decision.source,
            model=fast_model,
            raw_response=decision.raw_response,
            status="failed",
        ))
        pytest.fail(
            f"Expected direct rule match for 'Create a Python script', "
            f"got source={decision.source}"
        )

    if decision.role != "coder":
        _record_result(LiveRouteResult(
            group="infrastructure",
            input="Create a Python script",
            expected_role="coder",
            actual_role=decision.role,
            source=decision.source,
            model=fast_model,
            raw_response=decision.raw_response,
            status="failed",
        ))
        pytest.fail(f"Expected role=coder, got role={decision.role}")

    # Record success
    _record_result(LiveRouteResult(
        group="infrastructure",
        input="Create a Python script",
        expected_role="coder",
        actual_role=decision.role,
        source=decision.source,
        model=fast_model,
        raw_response=decision.raw_response,
        status="passed",
    ))


@pytest.mark.live_llm
@pytest.mark.smoke
def test_validate_model_backend_mismatch_is_skipped(freeflow_credentials):
    """Verify that a model requiring an unconfigured backend is detected and skipped.

    This test does NOT reach the LLM — it validates the pre-flight check that
    prevents the kind of provider/model mismatch described in the bug report
    (e.g. llama-3.3-70b-versatile through gemini-only backends).
    """
    credentials = freeflow_credentials["credentials"]
    configured = configured_freeflow_backends(credentials)

    # Pick a model whose backend is NOT configured
    if "groq" not in configured:
        test_model = "llama-3.3-70b-versatile"
        expected_backend = "groq"
    elif "gemini" not in configured:
        test_model = "gemini-3.5-flash"
        expected_backend = "gemini"
    else:
        # Both backends are configured — this check is a no-op, which is fine
        _record_result(LiveRouteResult(
            group="infrastructure",
            input="",
            expected_role="",
            actual_role="",
            source="",
            model="",
            raw_response="",
            status="passed",
            skip_reason="",
            matched_rule="",
        ))
        return

    ok, reason = _validate_model_backend_available(test_model, credentials)
    assert not ok, (
        f"Expected model '{test_model}' to be rejected (requires {expected_backend}), "
        f"but _validate_model_backend_available returned ok=True"
    )
    assert expected_backend in reason.lower(), (
        f"Skip reason should mention '{expected_backend}' backend, got: {reason}"
    )

    # Record the check as passed
    _record_result(LiveRouteResult(
        group="infrastructure",
        input=f"validate_backend_check({test_model})",
        expected_role="",
        actual_role="",
        source="",
        model=test_model,
        raw_response="",
        status="passed",
        skip_reason="",
        matched_rule=reason,
    ))


@pytest.mark.live_llm
@pytest.mark.smoke
def test_live_provider_info(freeflow_credentials, fast_model):
    """Display provider configuration info."""
    print("\n" + "="*70)
    print("FREEFLOW PROVIDER CONFIGURATION")
    print("="*70)
    print(f"Fast model: {fast_model}")
    print(f"Required keys: {', '.join(freeflow_credentials['required_keys'])}")
    print(f"Configured keys: {', '.join(freeflow_credentials['credentials'].keys())}")
    print("="*70)
