from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent_model
import agent_runtime
from agent import LocalAgent
from agent_state import PlanInfo
from executor import SafeExecutor
from tests.conftest import FakeLLMClient, NullUI, make_plan


@pytest.mark.parametrize(
    "message",
    [
        "sk-proj-" + "a" * 24,
        "gsk_" + "b" * 24,
        "Authorization: Bearer synthetic-token-123456",
        "OPENAI_API_KEY=synthetic-value-123456",
        "https://user:synthetic-password@example.test/path",
        "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
        "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----",
    ],
)
def test_error_sanitizer_redacts_credential_forms(message):
    sanitized = agent_model.sanitize_error_message(message)

    assert "synthetic" not in sanitized
    assert "[REDACTED]" in sanitized


def test_select_action_model_prefers_smart_for_complex_or_error(agent_factory):
    agent = agent_factory(fast_model="fast", smart_model="smart")
    agent._state.plan_info = PlanInfo("g", "complex", ["x"], ["y"], [])
    assert agent._select_action_model_for_run() == "smart"

    agent._state.selected_action_role = "fast"
    assert agent._select_action_model_for_run() == "fast"


def test_count_tokens_falls_back_without_encoding(agent_factory):
    agent = agent_factory()
    agent._encoding = None
    assert agent._count_tokens("x" * 40) == 10


def test_token_encoding_initializes_once_on_first_count(
    agent_factory,
    monkeypatch,
):
    agent = agent_factory()
    calls = 0

    class Encoding:
        def encode(self, text, disallowed_special):
            assert disallowed_special == ()
            return text.split()

    def initialize():
        nonlocal calls
        calls += 1
        return Encoding()

    monkeypatch.setattr(agent, "_init_tiktoken_encoding", initialize)

    assert calls == 0
    assert agent._count_tokens("one two three") == 3
    assert agent._count_tokens("four five") == 2
    assert calls == 1


def test_agent_construction_does_not_load_token_encoding(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        LocalAgent,
        "_init_tiktoken_encoding",
        staticmethod(
            lambda: (_ for _ in ()).throw(
                AssertionError("token encoding loaded eagerly")
            )
        ),
    )

    agent = LocalAgent(
        FakeLLMClient(),
        SafeExecutor(tmp_path),
        ui=NullUI(),
    )

    assert agent._encoding is agent_model._TOKEN_ENCODING_UNINITIALIZED


def test_trim_prompt_to_token_limit_keeps_head_and_tail(agent_factory):
    agent = agent_factory(max_context_tokens=20)
    agent._count_tokens = lambda text: len(text)
    prompt = "HEAD" + "b" * 1000

    trimmed = agent._trim_prompt_to_token_limit("system", prompt)

    assert "CONTEXT TRIMMED BY PRIORITY" in trimmed
    assert "HEAD" in trimmed
    assert trimmed.endswith("[CONTEXT TRIMMED BY PRIORITY]")


def test_call_model_uses_cache_and_non_streaming_completion(agent_factory, monkeypatch):
    agent = agent_factory(stream=False, cache_enabled=True, max_retries=1)
    client = FakeLLMClient(content='{"thought":"ok"}')
    agent.client = client
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)

    first = agent._call_model("gpt-test", "system", "user")
    second = agent._call_model("gpt-test", "system", "user")

    assert first == '{"thought":"ok"}'
    assert second == first
    assert len(client.requests) == 1
    assert client.requests[0]["response_format"] == {"type": "json_object"}


def test_call_model_streaming_collects_chunks(agent_factory):
    agent = agent_factory(stream=True, verbose=False)
    agent.client = FakeLLMClient(stream_chunks=['{"thought"', ':"ok"}'])

    content = agent._call_model_streaming("gpt-test", [{"role": "user", "content": "x"}])

    assert content == '{"thought":"ok"}'
    assert agent._last_chunk_count == 2
    assert agent._last_avg_chunk_size > 0


def test_create_json_completion_falls_back_without_response_format(agent_factory):
    agent = agent_factory()
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("response_format is not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    agent.client.chat.completions.create = create
    response = agent._create_json_completion("model", [{"role": "user", "content": "x"}])

    assert response.choices[0].message.content == "ok"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_create_json_completion_redacts_messages_before_provider(agent_factory):
    agent = agent_factory()
    synthetic = "sk-proj-" + "m" * 24
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="ok"))
            ]
        )

    agent.client.chat.completions.create = create
    agent._create_json_completion(
        "model",
        [{"role": "user", "content": f"use api_key={synthetic}"}],
    )

    assert synthetic not in str(calls)
    assert "[REDACTED]" in calls[0]["messages"][0]["content"]


def test_call_model_raises_after_non_retryable_error(agent_factory, monkeypatch):
    agent = agent_factory(stream=False, max_retries=2)
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)
    synthetic = "sk-proj-" + "e" * 24

    def fail(**kwargs):
        raise ValueError(f"bad request Authorization: Bearer {synthetic}")

    agent.client.chat.completions.create = fail
    with pytest.raises(RuntimeError, match="Failed to get model response") as caught:
        agent._call_model("model", "system", "user")
    assert synthetic not in str(caught.value)


def test_empty_account_402_is_not_retried_and_is_terminal(agent_factory, monkeypatch):
    agent = agent_factory(stream=False, max_retries=3)
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("Error code: 402 - {'error': {'message': 'Insufficient Balance'}}")

    agent.client.chat.completions.create = fail
    with pytest.raises(RuntimeError, match="Failed to get model response") as caught:
        agent._call_model("model", "system", "user")

    assert len(calls) == 1
    assert agent_runtime._is_terminal_model_error(str(caught.value))
    assert "insufficient balance or credits" in agent._normalize_error_for_model(str(caught.value))


def _prime_pipeline_agent(agent_factory, content):
    from tests.conftest import make_plan

    agent = agent_factory(stream=False)
    agent.client = FakeLLMClient(content=content)
    agent._state.plan_info = make_plan()
    agent._state.selected_action_role = "fast"
    agent._state.selected_action_model = agent.config.fast_model
    agent._state.current_agent_role = "coder"
    return agent


def test_matching_prefetch_is_consumed_without_a_second_call(agent_factory):
    agent = _prime_pipeline_agent(agent_factory, '{"thought":"prefetched"}')
    agent_model._launch_action_prefetch(agent, "PROMPT-A")

    def _forbidden(**_kwargs):
        raise AssertionError("call_action_model made a duplicate model call")

    agent._call_model = _forbidden

    result = agent._call_action_model("PROMPT-A")

    assert result == '{"thought":"prefetched"}'
    assert agent._prefetch is None


def test_mispredicted_prefetch_falls_back_to_a_normal_call(agent_factory):
    agent = _prime_pipeline_agent(agent_factory, '{"thought":"live"}')
    agent_model._launch_action_prefetch(agent, "PROMPT-A")

    # A different prompt must drop the stale prefetch and call normally, never
    # reuse the wrong step's response.
    result = agent._call_action_model("PROMPT-B")

    assert result == '{"thought":"live"}'
    assert agent._prefetch is None


class _ReasoningClient:
    """Streams reasoning deltas before the answer, like OpenRouter/Kimi do."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        deltas = [
            SimpleNamespace(content=None, reasoning="I should "),
            SimpleNamespace(content=None, reasoning="read the file."),
            SimpleNamespace(content='{"thought":"ok"}', reasoning=None),
        ]
        return [SimpleNamespace(choices=[SimpleNamespace(delta=delta)]) for delta in deltas]


def test_streaming_reasoning_reaches_the_live_thinking_panel(agent_factory):
    agent = agent_factory(stream=True, verbose=False)
    agent.client = _ReasoningClient()
    pushed: list[str] = []
    agent._ui.set_thinking = pushed.append

    content = agent._call_model_streaming("gpt-test", [{"role": "user", "content": "x"}])

    assert content == '{"thought":"ok"}'
    # Cleared for the new call, then filled, then sealed by the first answer token.
    assert pushed[0] == ""
    assert pushed[-1] == "I should read the file."


def test_streaming_reasoning_keeps_summary_headings_apart(agent_factory):
    deltas = [
        SimpleNamespace(content=None, reasoning_content="**A**"),
        SimpleNamespace(content=None, reasoning_content="**B**"),
        SimpleNamespace(content='{"thought":"ok"}', reasoning_content=None),
    ]
    agent = agent_factory(stream=True, verbose=False)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: [SimpleNamespace(choices=[SimpleNamespace(delta=d)]) for d in deltas]
    )))
    pushed: list[str] = []
    agent._ui.set_thinking = pushed.append

    agent._call_model_streaming("gpt-test", [{"role": "user", "content": "x"}])

    assert pushed[-1] == "**A**\n\n**B**"


def test_action_call_clears_thinking_when_provider_streams_no_reasoning(agent_factory):
    agent = agent_factory(stream=True, verbose=False)
    agent.client = FakeLLMClient(stream_chunks=['{"thought"', ':"ok"}'])
    pushed: list[str] = []
    agent._ui.set_thinking = pushed.append

    agent._call_model_streaming("gpt-test", [{"role": "user", "content": "x"}])

    assert pushed == [""]


class _UsageCapturingUI(NullUI):
    def __init__(self):
        self.usages = []

    def print_usage(self, usage):
        self.usages.append(usage)


def _emit(agent, *, kind, input_tokens=9000):
    agent_model._emit_usage(
        agent,
        model="m",
        input_tokens=input_tokens,
        output_tokens=120,
        kind=kind,
    )
    return agent._ui.usages[-1]


def test_usage_reports_context_only_for_conversation_calls(agent_factory):
    agent = agent_factory(max_context_tokens=120000)
    agent._ui = _UsageCapturingUI()

    for kind in ("step", "chat"):
        usage = _emit(agent, kind=kind)
        assert usage["context_tokens"] == usage["input_tokens"] == 9000

    # A plan or report call sends its own one-off prompt: counting it as the
    # conversation size would make the context appear to shrink mid-run.
    for kind in ("plan", "report"):
        assert _emit(agent, kind=kind)["context_tokens"] == 0


def test_usage_carries_the_limit_the_prompt_is_trimmed_to(agent_factory):
    agent = agent_factory(max_context_tokens=120000)
    agent._ui = _UsageCapturingUI()
    assert _emit(agent, kind="step")["context_limit"] == 120000

    freeflow = agent_factory(provider="freeflow")
    freeflow._ui = _UsageCapturingUI()
    assert _emit(freeflow, kind="step")["context_limit"] == 11000
    assert agent_model.context_token_limit(freeflow) == 11000


def test_context_limit_follows_the_active_model_not_the_configured_one(agent_factory):
    """Escalating fast -> smart mid-run changes the model, so it must change
    the budget: a 1M-model budget carried onto a 200k model is a hard 400."""
    agent = agent_factory(fast_model="claude-sonnet-5", provider="anthropic")
    big = agent_model.context_token_limit(agent)

    agent._state.selected_action_model = "claude-haiku-4-5"
    small = agent_model.context_token_limit(agent)

    assert small < big
    assert small < 200_000


def test_env_overrides_the_derived_context_limit(agent_factory, monkeypatch):
    agent = agent_factory(fast_model="gpt-5.4-mini")
    monkeypatch.setenv("AGENT_MAX_CONTEXT_TOKENS", "40000")
    assert agent_model.context_token_limit(agent) == 40000


# ── a model sized wrong is corrected by the provider, not fatal ─────────────

def test_context_overflow_is_classified():
    for message in (
        "Input tokens exceed the configured limit of 272,000 tokens",
        "prompt is too long: 1050000 tokens > 1000000 maximum",
        "This model's maximum context length is 128000 tokens",
        "context_length_exceeded",
    ):
        assert agent_model.is_context_overflow_error(RuntimeError(message))

    assert not agent_model.is_context_overflow_error(RuntimeError("rate limit reached"))
    assert not agent_model.is_context_overflow_error(RuntimeError("invalid api key"))


def test_overflow_shrinks_the_budget_and_retries_instead_of_failing(
    agent_factory, monkeypatch
):
    """Model ids come from /v1/models, so a size guess can be wrong.

    The provider's rejection is the only authority on the real limit: it must
    lower the budget for that model and re-send, not break the step.
    """
    from context_budget import learned_budget_for, reset_learned_budgets

    reset_learned_budgets()
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)
    try:
        agent = agent_factory(
            fast_model="gpt-6-terra", stream=False, cache_enabled=False, max_retries=3
        )
        agent._count_tokens = lambda text: len(text)

        attempts: list[int] = []

        def flaky(model, messages, **kwargs):
            sent = sum(len(message["content"]) for message in messages)
            attempts.append(sent)
            if len(attempts) == 1:
                raise RuntimeError(
                    "Input tokens exceed the configured limit of 272,000 tokens"
                )
            return FakeLLMClient(content='{"thought":"ok"}').chat.completions.create(
                model=model, messages=messages
            )

        monkeypatch.setattr(agent, "_create_json_completion", flaky)

        content = agent._call_model("gpt-6-terra", "system", "u" * 900_000)

        assert content == '{"thought":"ok"}'
        assert len(attempts) == 2
        assert attempts[1] < attempts[0]
        assert learned_budget_for("gpt-6-terra") is not None
        assert agent_model.context_token_limit(agent) == learned_budget_for("gpt-6-terra")
    finally:
        reset_learned_budgets()


def test_a_learned_rejection_outranks_an_explicit_override(agent_factory, monkeypatch):
    """Otherwise the retry resends exactly the prompt that was just refused."""
    from context_budget import note_input_overflow, reset_learned_budgets

    reset_learned_budgets()
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    try:
        agent = agent_factory(fast_model="gpt-6-terra", max_context_tokens=900_000)
        assert agent_model.context_token_limit(agent) == 900_000

        note_input_overflow("gpt-6-terra", 500_000)
        assert agent_model.context_token_limit(agent) == 300_000

        monkeypatch.setenv("AGENT_MAX_CONTEXT_TOKENS", "1500000")
        assert agent_model.context_token_limit(agent) == 300_000
    finally:
        reset_learned_budgets()


# ── the trimmer must actually deliver what it promises ──────────────────────

_CONTENT_SHAPES = {
    "prose": "the quick brown fox jumps over the lazy dog ",
    # Cyrillic and code are far denser per character than prose. A trimmer that
    # assumes a fixed chars-per-token ratio silently overshoots on both, and
    # source code is most of what Kvist puts in a prompt.
    "cyrillic": "быстрая коричневая лиса прыгает через ленивую собаку ",
    "code": "def f(x):\n    return x + 1\n",
    "json": '{"path": "a/b.py", "content": "x"},\n',
    "minified": "a" * 400,
}


@pytest.mark.parametrize("shape", sorted(_CONTENT_SHAPES))
@pytest.mark.parametrize("budget", [1_200, 4_000, 30_000])
def test_trimmed_prompt_actually_fits_the_budget(agent_factory, shape, budget):
    """The property the whole trimmer exists for, checked against the real
    tokenizer rather than the estimate the trimmer used to trust.

    With a fixed 4-chars-per-token assumption a code-heavy prompt came back
    almost three times over budget — measured as fitting, refused by the API.
    """
    agent = agent_factory(max_context_tokens=budget)
    agent._state.plan_info = make_plan()
    agent._state.recent_result_details = [
        "Detailed recent action data:\n" + _CONTENT_SHAPES[shape] * 3_000
    ]
    system_prompt = "You are a coding agent.\n" * 20
    prompt = agent._build_runtime_prompt("проверь файл", 4)

    trimmed = agent._trim_prompt_to_token_limit(system_prompt, prompt)

    total = agent._count_tokens(system_prompt) + agent._count_tokens(trimmed)
    assert total <= budget, f"{shape}: {total} tokens against a {budget} budget"


def test_trimming_keeps_the_sections_the_model_cannot_work_without(agent_factory):
    """Fitting the budget by discarding the task would be a useless kind of
    correct."""
    agent = agent_factory(max_context_tokens=2_000)
    agent._state.plan_info = make_plan()
    agent._state.recent_result_details = ["Detailed recent action data:\n" + "x=1\n" * 20_000]
    agent._state.pending_instruction = "Your finish was not accepted as done. REASON_MARKER"

    trimmed = agent._trim_prompt_to_token_limit("system", agent._build_runtime_prompt("СДЕЛАЙ ЭТО", 2))

    assert "СДЕЛАЙ ЭТО" in trimmed
    assert "REASON_MARKER" in trimmed


def test_an_untrimmed_prompt_is_returned_untouched(agent_factory):
    """A prompt inside the budget must not be marked as trimmed — the marker is
    how a reader tells a whole prompt from a cut one."""
    agent = agent_factory(max_context_tokens=500_000)
    agent._state.plan_info = make_plan()

    prompt = agent._build_runtime_prompt("small task", 1)
    trimmed = agent._trim_prompt_to_token_limit("system", prompt)

    assert trimmed == prompt
    assert "CONTEXT TRIMMED" not in trimmed


def test_section_truncation_respects_the_real_token_cost(agent_factory):
    """Directly on the helper: the same character count costs very different
    numbers of tokens, and the target is expressed in tokens."""
    agent = agent_factory()
    for text in ("word " * 4_000, "слово " * 4_000, "x=1;\n" * 4_000):
        section = "Header:\n" + text
        out = agent_model._truncate_section_text(section, 300, agent._count_tokens)
        assert agent._count_tokens(out) <= 300
        assert out.startswith("Header:")


# ── an overflow must be told apart from a rate limit ────────────────────────

@pytest.mark.parametrize(
    "message",
    [
        "Rate limit reached for gpt-5.6-luna: Limit 200000 tokens per min",
        "429 Too Many Requests: retry after 20s",
        "Request too large for model on tokens per min (TPM): Limit 30000",
        "rate_limit_exceeded: you exceeded your current quota",
        # The dangerous one: a relayed throughput error that also carries the
        # exact wording of a context overflow. Marker matching alone reads this
        # as "your prompt is too big" and shrinks the budget permanently.
        "Rate limit reached for model on tokens per minute (TPM): Limit 30000, "
        "Requested 35000. Please reduce the length of the messages and try again in 12s.",
    ],
)
def test_a_rate_limit_is_not_mistaken_for_an_overflow(message):
    """Both are 'about tokens' and phrased alike, but the cures are opposite.

    Reading a rate limit as an overflow permanently shrinks the model's budget
    for the session *and* skips the backoff sleep, so Kvist hammers the
    provider with a crippled context.
    """
    assert not agent_model.is_context_overflow_error(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    [
        "Input tokens exceed the configured limit of 272,000 tokens",
        "prompt is too long: 1050000 tokens > 1000000 maximum",
        "This model's maximum context length is 128000 tokens",
        "context_length_exceeded",
        "Please reduce the length of the messages",
        # Numbers that merely look like status codes or throughput limits must
        # not disarm the classifier: over-broad rate-limit markers would leave
        # a genuine overflow unrecognised and the step dead.
        "This model's maximum context length is 429000 tokens (request id req_429ab)",
    ],
)
def test_a_real_overflow_is_recognised(message):
    assert agent_model.is_context_overflow_error(RuntimeError(message))


def test_overflow_does_not_resend_an_identical_prompt(agent_factory, monkeypatch):
    """A retry that cannot shrink is a retry that buys the same rejection.

    The prompt may be unshrinkable: the system prompt is never trimmed, and a
    learned cap is keyed to the model that was refused, which is not
    necessarily the one whose budget the trimmer consults.
    """
    from context_budget import reset_learned_budgets

    reset_learned_budgets()
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)
    try:
        agent = agent_factory(stream=False, cache_enabled=False, max_retries=4)
        sent: list[str] = []

        def always_overflows(model, messages, **kwargs):
            sent.append("".join(message["content"] for message in messages))
            raise RuntimeError("prompt is too long: too many input tokens")

        monkeypatch.setattr(agent, "_create_json_completion", always_overflows)

        with pytest.raises(Exception):
            agent._call_model("gpt-5.6-luna", "S" * 400_000, "u" * 40_000)

        assert len(sent) == len(set(sent)), "the same prompt was sent twice"
    finally:
        reset_learned_budgets()


def test_json_mode_rejection_keeps_the_rest_of_the_request_and_warns():
    """Losing JSON mode must cost only JSON mode, and must not be silent.

    The bare fallback rebuilds the request from scratch, dropping the timeout,
    usage reporting and reasoning kwargs along with response_format. Try the
    narrowed retry first so a provider that refuses only that parameter keeps
    everything else.
    """
    import agent_model
    from types import SimpleNamespace

    attempts: list[dict] = []
    warnings: list[str] = []

    class Completions:
        def create(self, **kwargs):
            attempts.append(kwargs)
            if "response_format" in kwargs:
                raise RuntimeError("response_format is not supported by this model")
            return "ok"

    agent = SimpleNamespace(
        config=SimpleNamespace(
            provider="deepseek",
            model_temperature=0.0,
            api_timeout=30,
            reasoning_effort="medium",
        ),
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        _ui=SimpleNamespace(
            print_error=warnings.append,
            debug_print=lambda *a, **k: None,
        ),
    )

    result = agent_model._create_json_completion(
        agent, "deepseek-reasoner", [{"role": "user", "content": "hi"}]
    )

    assert result == "ok"
    assert len(attempts) == 2, "expected one rejected attempt and one narrowed retry"
    assert "response_format" in attempts[0]
    retry = attempts[1]
    assert "response_format" not in retry
    # Everything unrelated to the refused parameter survives.
    assert retry["timeout"] == 30
    assert retry["reasoning_effort"] == "high"
    assert warnings, "dropping JSON mode must be surfaced, not silent"


def test_cached_json_mode_rejection_still_falls_through_to_a_temperature_error():
    """A step that skips the known-rejected response_format attempt must still
    reach the bare fallback tier when a *different* kwarg is refused next.

    Regression: the cache-hit path once returned straight from the narrowed
    retry, bypassing the tier that strips ``temperature`` for models (o1/o3
    and similar) that fix it to 1 and error on any other value — so a model
    that failed both checks crashed every step after the first instead of
    recovering on the bare fallback like a fresh rejection does.
    """
    import agent_model

    provider = "openai"
    model = "gpt-6-astra-regression"
    agent_model._JSON_MODE_UNSUPPORTED.add((provider, model))

    attempts: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            attempts.append(kwargs)
            if "temperature" in kwargs:
                raise RuntimeError(
                    "Unsupported value: 'temperature' does not support 0.1 "
                    "with this model. Only the default (1) value is supported."
                )
            return "ok"

    agent = SimpleNamespace(
        config=SimpleNamespace(
            provider=provider,
            model_temperature=0.1,
            api_timeout=30,
            reasoning_effort="medium",
        ),
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        _ui=SimpleNamespace(
            print_error=lambda *a, **k: None,
            debug_print=lambda *a, **k: None,
        ),
    )

    result = agent_model._create_json_completion(
        agent, model, [{"role": "user", "content": "hi"}]
    )

    assert result == "ok"
    assert all("response_format" not in call for call in attempts)
    assert "temperature" not in attempts[-1]
    assert (provider, model) in agent_model._TEMPERATURE_UNSUPPORTED


def test_json_mode_warning_prints_once_per_run_not_once_per_step():
    """The rejection is cached at process scope, but the warning should not
    repeat on every step of the same run once the run has already seen it —
    only a fresh run (a fresh RunState) earns a fresh warning.
    """
    import agent_model
    from agent_state import RunState

    provider = "openai"
    model = "gpt-6-astra-dedup"

    class Completions:
        def create(self, **kwargs):
            if "response_format" in kwargs:
                raise RuntimeError("response_format is not supported by this model")
            return "ok"

    warnings: list[str] = []
    agent = SimpleNamespace(
        config=SimpleNamespace(
            provider=provider,
            model_temperature=0.1,
            api_timeout=30,
            reasoning_effort="medium",
        ),
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        _ui=SimpleNamespace(
            print_error=warnings.append,
            debug_print=lambda *a, **k: None,
        ),
        _state=RunState(),
    )

    agent_model._create_json_completion(agent, model, [{"role": "user", "content": "1"}])
    agent_model._create_json_completion(agent, model, [{"role": "user", "content": "2"}])
    assert len(warnings) == 1, "second step of the same run must stay silent"

    agent._state = RunState()
    agent_model._create_json_completion(agent, model, [{"role": "user", "content": "3"}])
    assert len(warnings) == 2, "a new run must warn again"
