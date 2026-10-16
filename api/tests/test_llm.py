from app.services.llm import _chat_completion_url, _extract_message_content


def test_chat_completion_url_accepts_base_url() -> None:
    """验证“对话模型补全URLacceptsbaseURL”场景，确保重构后仍满足预期业务与安全边界。"""
    assert (
        _chat_completion_url("https://api.deepseek.com")
        == "https://api.deepseek.com/chat/completions"
    )


def test_chat_completion_url_accepts_full_endpoint() -> None:
    """验证“对话模型补全URLacceptsfull端点”场景，确保重构后仍满足预期业务与安全边界。"""
    assert (
        _chat_completion_url("https://api.deepseek.com/chat/completions")
        == "https://api.deepseek.com/chat/completions"
    )


def test_extract_message_content() -> None:
    """验证“提取消息内容”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    response_json = {
        "choices": [
            {
                "message": {
                    "content": '{"ok": true}',
                },
            },
        ],
    }

    assert _extract_message_content(response_json) == '{"ok": true}'
