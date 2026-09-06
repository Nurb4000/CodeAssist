"""Tests for prompt/message building."""
from codeassist.prompts import build_openai_messages


class TestBuildOpenAiMessages:
    def test_plain_history_unchanged(self):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "tool", "content": "result", "tool_call_id": "call_1"},
        ]
        messages = build_openai_messages("You are a helper.", history)
        assert messages[0] == {"role": "system", "content": "You are a helper."}
        assert messages[1] == {"role": "user", "content": "Hello"}
        assert messages[2]["content"] == "Hi!"
        assert messages[3]["tool_call_id"] == "call_1"

    def test_user_message_with_attachments_is_multipart(self):
        history = [{
            "role": "user",
            "content": "What is this?",
            "attachments": [
                {
                    "attachment_type": "image",
                    "mime_type": "image/png",
                    "file_name": "pic.png",
                    "data": "data:image/png;base64,abc123",
                },
                {
                    "attachment_type": "image",
                    "mime_type": "image/jpeg",
                    "file_name": "pic.jpg",
                    "data": "data:image/jpeg;base64,def456",
                },
            ],
        }]
        messages = build_openai_messages("You are a helper.", history)
        content = messages[1]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "What is this?"}
        assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}}
        assert content[2] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,def456"}}

    def test_user_message_without_attachments_stays_string(self):
        history = [{"role": "user", "content": "plain"}]
        messages = build_openai_messages("You are a helper.", history)
        assert isinstance(messages[1]["content"], str)