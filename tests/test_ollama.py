import json
from unittest.mock import MagicMock, patch

import pytest

from lastmile.agents.llm.base import LLMError
from lastmile.agents.llm.factory import get_llm
from lastmile.agents.llm.ollama import OllamaClient


def test_ollama_client_generate_json_success():
    client = OllamaClient(base_url="http://127.0.0.1:11434", model="llama3.2")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "message": {"content": json.dumps({"rationale": "Test template {{exposure}}", "script": "Hello {{first_name}}"})},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 120,
        "eval_count": 45,
        "total_duration": 1500000000,
    })
    mock_resp.json.return_value = json.loads(mock_resp.text)

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = client.generate_json(system="You are an assistant.", prompt="Draft a template.")
        assert result == {"rationale": "Test template {{exposure}}", "script": "Hello {{first_name}}"}
        assert client.last_exchange["finish_reason"] == "stop"
        assert client.last_exchange["usage"]["eval_count"] == 45
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://127.0.0.1:11434/api/chat"
        assert kwargs["json"]["model"] == "llama3.2"
        assert kwargs["json"]["format"] == "json"


def test_ollama_client_non_200_raises_llm_error():
    client = OllamaClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(LLMError, match="Ollama returned 500"):
            client.generate_json(system="sys", prompt="user")


def test_ollama_client_invalid_json_raises_llm_error():
    client = OllamaClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"message": {"content": "Not valid json {{..."}})
    mock_resp.json.return_value = json.loads(mock_resp.text)

    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(LLMError, match="not valid JSON"):
            client.generate_json(system="sys", prompt="user")


def test_factory_selects_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    llm, desc = get_llm()
    assert llm is not None
    assert isinstance(llm, OllamaClient)
    assert llm.model == "mistral"
    assert llm.base_url == "http://localhost:11434"
    # the display label names no vendor - the consoles show that a model wrote it, not whose.
    # provider and model are still recorded on every call in llm_calls, so an auditor can tell.
    assert desc == "llm" and "ollama" not in desc and "mistral" not in desc
