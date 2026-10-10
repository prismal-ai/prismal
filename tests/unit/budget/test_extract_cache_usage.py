"""Cache/reasoning extraction per provider shape (Phase TKE — SPEC-TKE-ACC-002).

Fixtures mirror what TKE0-01 verified: ``langchain_litellm`` writes the
LangChain-standard ``usage_metadata`` details; raw OpenAI/Anthropic shapes may
still arrive via ``response_metadata['token_usage']``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prismal.budget.usage import extract_token_usage


def _msg(usage_metadata: object = None, response_metadata: object = None) -> SimpleNamespace:
    return SimpleNamespace(usage_metadata=usage_metadata, response_metadata=response_metadata)


def test_langchain_standard_details_through_chatlitellm() -> None:
    msg = _msg(
        usage_metadata={
            "input_tokens": 1200,
            "output_tokens": 300,
            "input_token_details": {"cache_read": 1000, "cache_creation": 100},
            "output_token_details": {"reasoning": 120},
        }
    )
    t = extract_token_usage(msg)
    assert (t.prompt_tokens, t.completion_tokens) == (1200, 300)
    assert (t.cache_read_tokens, t.cache_write_tokens, t.reasoning_tokens) == (1000, 100, 120)


def test_openai_shape_in_token_usage() -> None:
    msg = _msg(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 1536},
                "completion_tokens_details": {"reasoning_tokens": 20},
            }
        }
    )
    t = extract_token_usage(msg)
    assert (t.cache_read_tokens, t.reasoning_tokens) == (1536, 20)


def test_openrouter_cache_write_tokens() -> None:
    msg = _msg(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 900,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cache_write_tokens": 850,
            }
        }
    )
    assert extract_token_usage(msg).cache_write_tokens == 850


def test_anthropic_raw_fields() -> None:
    msg = _msg(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 50,
                "completion_tokens": 10,
                "cache_read_input_tokens": 4000,
                "cache_creation_input_tokens": 300,
            }
        }
    )
    t = extract_token_usage(msg)
    assert (t.prompt_tokens, t.cache_read_tokens, t.cache_write_tokens) == (50, 4000, 300)
    assert t.normalized().prompt_tokens == 4350


def test_details_come_from_token_usage_when_usage_metadata_lacks_them() -> None:
    msg = _msg(
        usage_metadata={"input_tokens": 100, "output_tokens": 10},
        response_metadata={
            "token_usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 64}}
        },
    )
    t = extract_token_usage(msg)
    assert (t.prompt_tokens, t.cache_read_tokens) == (100, 64)


@pytest.mark.parametrize(
    "msg",
    [
        _msg(usage_metadata={"input_tokens": 7, "output_tokens": 3}),
        _msg(response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}),
    ],
)
def test_v3_12_contract_without_cache_fields(msg: SimpleNamespace) -> None:
    t = extract_token_usage(msg)
    assert (t.prompt_tokens, t.completion_tokens) == (7, 3)
    assert (t.cache_read_tokens, t.cache_write_tokens, t.reasoning_tokens) == (0, 0, 0)


def test_garbage_details_never_raise() -> None:
    msg = _msg(
        usage_metadata={
            "input_tokens": 5,
            "output_tokens": 1,
            "input_token_details": "nope",
            "output_token_details": {"reasoning": "x"},
        }
    )
    t = extract_token_usage(msg)
    assert (t.cache_read_tokens, t.reasoning_tokens) == (0, 0)
