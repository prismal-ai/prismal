"""Unit tests for SecurePromptBuilder."""

import uuid

import pytest

from prismal.security.prompt_builder import SecurePromptBuilder


@pytest.fixture
def builder() -> SecurePromptBuilder:
    """Fresh SecurePromptBuilder instance."""
    return SecurePromptBuilder()


def test_build_returns_two_messages(builder: SecurePromptBuilder) -> None:
    """build() must return exactly 2 messages."""
    messages = builder.build("You are helpful.", "Hello!")
    assert len(messages) == 2


def test_build_first_message_is_system(builder: SecurePromptBuilder) -> None:
    """First message must have role 'system'."""
    messages = builder.build("Be helpful.", "Hi")
    assert messages[0]["role"] == "system"


def test_build_second_message_is_user(builder: SecurePromptBuilder) -> None:
    """Second message must have role 'user'."""
    messages = builder.build("Be helpful.", "Hi")
    assert messages[1]["role"] == "user"


def test_user_input_wrapped_in_tags(builder: SecurePromptBuilder) -> None:
    """User input must be wrapped in <user_input> tags."""
    messages = builder.build("sys", "user question")
    assert "<user_input>user question</user_input>" in messages[1]["content"]


def test_user_input_sanitized_control_chars(builder: SecurePromptBuilder) -> None:
    """Control characters in user input must be stripped before wrapping."""
    messages = builder.build("sys", "\x00user input with null byte")
    content = messages[1]["content"]
    assert "\x00" not in content
    assert "user input with null byte" in content


def test_docs_wrapped_in_documents_tag(builder: SecurePromptBuilder) -> None:
    """Docs must be wrapped in <documents><document>...</document></documents>."""
    messages = builder.build("sys", "query", docs=["doc1 content", "doc2 content"])
    content = messages[1]["content"]
    assert "<documents>" in content
    assert "</documents>" in content
    assert "<document>doc1 content</document>" in content
    assert "<document>doc2 content</document>" in content


def test_no_docs_no_documents_tag(builder: SecurePromptBuilder) -> None:
    """Without docs, <documents> tag must not appear."""
    messages = builder.build("sys", "query")
    assert "<documents>" not in messages[1]["content"]


def test_empty_docs_list_no_documents_tag(builder: SecurePromptBuilder) -> None:
    """Empty docs list must not produce <documents> tag."""
    messages = builder.build("sys", "query", docs=[])
    assert "<documents>" not in messages[1]["content"]


def test_canary_embedded_in_system_prompt(builder: SecurePromptBuilder) -> None:
    """System prompt must contain canary comment."""
    messages = builder.build("Be helpful.", "Hello!")
    assert "<!-- canary:" in messages[0]["content"]


def test_canary_is_valid_uuid(builder: SecurePromptBuilder) -> None:
    """builder.canary must be a valid UUID string after build()."""
    builder.build("sys", "user")
    uuid.UUID(builder.canary)  # raises ValueError if not a valid UUID


def test_successive_builds_different_canaries(builder: SecurePromptBuilder) -> None:
    """Each build() call must generate a new unique canary."""
    builder.build("sys", "first")
    canary1 = builder.canary
    builder.build("sys", "second")
    canary2 = builder.canary
    assert canary1 != canary2


def test_system_prompt_preserved(builder: SecurePromptBuilder) -> None:
    """System prompt text must appear in system message."""
    messages = builder.build("You are a helpful AI.", "Hi")
    assert "You are a helpful AI." in messages[0]["content"]


def test_user_input_not_in_system_prompt(builder: SecurePromptBuilder) -> None:
    """User input must not appear in the system message."""
    messages = builder.build("sys", "secret user text")
    assert "secret user text" not in messages[0]["content"]


def test_system_prompt_not_in_user_message(builder: SecurePromptBuilder) -> None:
    """System prompt text must not appear in the user message."""
    messages = builder.build("top secret system prompt", "user question")
    assert "top secret system prompt" not in messages[1]["content"]


def test_canary_matches_builder_canary_attribute(builder: SecurePromptBuilder) -> None:
    """The canary stored in builder.canary must appear verbatim in system content."""
    messages = builder.build("Be helpful.", "Hello!")
    assert builder.canary in messages[0]["content"]


def test_canary_format_is_well_formed(builder: SecurePromptBuilder) -> None:
    """Canary in system prompt must match the expected HTML comment format."""
    import re

    messages = builder.build("sys", "user")
    assert re.search(r"<!-- canary:[0-9a-f-]{36} -->", messages[0]["content"])
