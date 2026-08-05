"""Token accounting and cost estimation."""

import pytest

from app.providers.llm.base import TokenUsage, calculate_cost

RATES = {
    "input_cost_per_mtok": 3.0,
    "output_cost_per_mtok": 15.0,
    "cached_input_cost_per_mtok": 0.3,
}


class TestTokenUsage:
    def test_total_sums_every_component(self):
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=30,
        )
        assert usage.total_tokens == 200

    def test_usage_records_add(self):
        combined = TokenUsage(input_tokens=100, output_tokens=10) + TokenUsage(
            input_tokens=50, output_tokens=5, cache_read_input_tokens=25
        )
        assert combined.input_tokens == 150
        assert combined.output_tokens == 15
        assert combined.cache_read_input_tokens == 25

    def test_addition_does_not_mutate_either_operand(self):
        a = TokenUsage(input_tokens=100)
        b = TokenUsage(input_tokens=50)
        a + b
        assert a.input_tokens == 100 and b.input_tokens == 50

    def test_empty_usage(self):
        assert TokenUsage().total_tokens == 0


class TestCostCalculation:
    def test_a_million_input_tokens_costs_the_input_rate(self):
        cost = calculate_cost(TokenUsage(input_tokens=1_000_000), **RATES)
        assert cost == pytest.approx(3.0)

    def test_a_million_output_tokens_costs_the_output_rate(self):
        assert calculate_cost(TokenUsage(output_tokens=1_000_000), **RATES) == pytest.approx(15.0)

    def test_cached_reads_use_the_discounted_rate(self):
        assert calculate_cost(
            TokenUsage(cache_read_input_tokens=1_000_000), **RATES
        ) == pytest.approx(0.3)

    def test_cache_writes_are_billed_at_the_standard_input_rate(self):
        assert calculate_cost(
            TokenUsage(cache_creation_input_tokens=1_000_000), **RATES
        ) == pytest.approx(3.0)

    def test_caching_is_cheaper_than_not_caching(self):
        uncached = calculate_cost(TokenUsage(input_tokens=500_000), **RATES)
        cached = calculate_cost(TokenUsage(cache_read_input_tokens=500_000), **RATES)
        assert cached < uncached

    def test_a_realistic_analysis_is_costed(self):
        # 12 categories, roughly 4k input and 800 output tokens each.
        usage = TokenUsage(input_tokens=12 * 4000, output_tokens=12 * 800)
        cost = calculate_cost(usage, **RATES)
        assert 0.0 < cost < 1.0

    def test_zero_usage_is_free(self):
        assert calculate_cost(TokenUsage(), **RATES) == 0.0

    def test_cost_is_never_negative(self):
        assert calculate_cost(TokenUsage(input_tokens=-500), **RATES) >= 0.0

    def test_result_is_rounded_for_storage(self):
        cost = calculate_cost(TokenUsage(input_tokens=7, output_tokens=3), **RATES)
        assert cost == round(cost, 8)


class TestEmbeddingCost:
    def test_http_providers_estimate_cost_from_the_configured_rate(self):
        from app.providers.embedding.http_providers import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(
            model="text-embedding-3-small",
            dimensions=1536,
            api_key="test-key",
            cost_per_mtok=0.02,
        )
        assert provider.estimate_cost(1_000_000) == pytest.approx(0.02)

    def test_the_deterministic_provider_is_free(self):
        from app.providers.embedding.deterministic import DeterministicEmbeddingProvider

        assert DeterministicEmbeddingProvider(model="t", dimensions=32).estimate_cost(10**9) == 0.0

    def test_an_http_provider_requires_an_api_key(self):
        from app.providers.embedding.http_providers import VoyageEmbeddingProvider

        with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
            VoyageEmbeddingProvider(model="voyage-3", dimensions=1024, api_key="")


class TestProviderConstruction:
    def test_the_anthropic_provider_requires_a_key(self):
        from app.providers.llm.anthropic_provider import AnthropicProvider

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            AnthropicProvider(api_key="", model="claude-sonnet-4-5")

    def test_the_model_comes_from_configuration(self):
        from app.providers.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key", model="some-configured-model")
        assert provider.model == "some-configured-model"
        assert provider.name == "anthropic"

    def test_the_model_identifier_is_not_hard_coded_in_the_codebase(self):
        """The model must be read from ANTHROPIC_MODEL, never inlined in logic."""
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for path in root.rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # config.py holds the single default; everything else must not.
                if path.name != "config.py" and re.search(r"[\"']claude-[a-z0-9.\-]+[\"']", line):
                    offenders.append(f"{path.name}:{number}")
        assert not offenders, f"hard-coded model identifiers found: {offenders}"
