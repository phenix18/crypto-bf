"""The model transport. Mostly: how it behaves when the model is not there.

The free tier fails constantly — spent quota, retired model names, timeouts —
so "no reply" is the normal case these tests pin down, not the exceptional one.
"""

import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from llm_client import (  # noqa: E402
    DEFAULT_FREE_MODELS,
    LlmReply,
    NullClient,
    OpenRouterClient,
    ScriptedClient,
    build_llm_client,
)


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def completion(text="hello", model="m"):
    return FakeResponse(
        json.dumps({"choices": [{"message": {"content": text}}], "model": model}).encode()
    )


def http_error(code, body=b"nope"):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


class NullClientTests(unittest.TestCase):
    def test_it_is_never_available_and_never_answers(self):
        client = NullClient()
        self.assertFalse(client.available)
        self.assertIsNone(client.complete(system="s", user="u"))


class ScriptedClientTests(unittest.TestCase):
    def test_replies_are_handed_out_in_order(self):
        client = ScriptedClient(replies=["one", "two"])
        self.assertEqual(client.complete(system="s", user="u").text, "one")
        self.assertEqual(client.complete(system="s", user="u").text, "two")

    def test_running_out_of_replies_reads_as_no_reply(self):
        client = ScriptedClient(replies=["one"])
        client.complete(system="s", user="u")
        self.assertIsNone(client.complete(system="s", user="u"))

    def test_an_exception_in_the_script_is_raised(self):
        client = ScriptedClient(replies=[RuntimeError("boom")])
        with self.assertRaises(RuntimeError):
            client.complete(system="s", user="u")

    def test_calls_are_recorded(self):
        client = ScriptedClient(replies=["one"])
        client.complete(system="sys", user="usr")
        self.assertEqual(client.calls[0]["system"], "sys")
        self.assertEqual(client.calls[0]["user"], "usr")


class OpenRouterTests(unittest.TestCase):
    def test_no_key_means_not_available_and_no_request(self):
        client = OpenRouterClient(api_key="")
        self.assertFalse(client.available)
        with mock.patch("urllib.request.urlopen") as opened:
            self.assertIsNone(client.complete(system="s", user="u"))
        opened.assert_not_called()

    def test_a_completion_is_returned_with_the_model_that_answered(self):
        client = OpenRouterClient(api_key="k", models=("model-a",))
        with mock.patch("urllib.request.urlopen", return_value=completion("the answer")):
            reply = client.complete(system="s", user="u")
        self.assertEqual(reply, LlmReply(text="the answer", model="model-a"))

    def test_a_dead_model_falls_through_to_the_next(self):
        client = OpenRouterClient(api_key="k", models=("gone", "alive"))
        opened = mock.Mock(side_effect=[http_error(404), completion("second")])
        with mock.patch("urllib.request.urlopen", opened):
            reply = client.complete(system="s", user="u")
        self.assertEqual(reply.model, "alive")
        self.assertEqual(opened.call_count, 2)

    def test_a_spent_quota_falls_through_too(self):
        client = OpenRouterClient(api_key="k", models=("a", "b"))
        opened = mock.Mock(side_effect=[http_error(429), completion("ok")])
        with mock.patch("urllib.request.urlopen", opened):
            self.assertEqual(client.complete(system="s", user="u").model, "b")

    def test_every_model_failing_is_no_reply_not_an_exception(self):
        client = OpenRouterClient(api_key="k", models=("a", "b", "c"))
        opened = mock.Mock(side_effect=http_error(429))
        with mock.patch("urllib.request.urlopen", opened):
            self.assertIsNone(client.complete(system="s", user="u"))
        self.assertEqual(opened.call_count, 3)

    def test_a_network_failure_is_no_reply(self):
        client = OpenRouterClient(api_key="k", models=("a",))
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.assertIsNone(client.complete(system="s", user="u"))
        self.assertIn("down", client.last_error)

    def test_unparseable_json_is_no_reply(self):
        client = OpenRouterClient(api_key="k", models=("a",))
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"<html>")):
            self.assertIsNone(client.complete(system="s", user="u"))

    def test_an_empty_completion_is_no_reply(self):
        body = FakeResponse(json.dumps({"choices": [{"message": {"content": "  "}}]}).encode())
        client = OpenRouterClient(api_key="k", models=("a",))
        with mock.patch("urllib.request.urlopen", return_value=body):
            self.assertIsNone(client.complete(system="s", user="u"))

    def test_content_as_parts_is_joined(self):
        body = FakeResponse(
            json.dumps(
                {"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]}
            ).encode()
        )
        client = OpenRouterClient(api_key="k", models=("a",))
        with mock.patch("urllib.request.urlopen", return_value=body):
            self.assertEqual(client.complete(system="s", user="u").text, "ab")

    def test_the_key_travels_in_the_header_and_nowhere_else(self):
        client = OpenRouterClient(api_key="sk-secret", models=("a",))
        captured = {}

        def capture(request, timeout=None):
            captured["headers"] = dict(request.headers)
            captured["body"] = request.data.decode()
            return completion("ok")

        with mock.patch("urllib.request.urlopen", side_effect=capture):
            client.complete(system="s", user="u")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-secret")
        self.assertNotIn("sk-secret", captured["body"])

    def test_a_failure_message_carries_no_credential(self):
        client = OpenRouterClient(api_key="sk-secret", models=("a",))
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401, b"unauthorized")):
            client.complete(system="s", user="u")
        self.assertNotIn("sk-secret", client.last_error)

    def test_the_request_asks_for_a_near_deterministic_answer(self):
        client = OpenRouterClient(api_key="k", models=("a",))
        captured = {}

        def capture(request, timeout=None):
            captured["body"] = json.loads(request.data.decode())
            return completion("ok")

        with mock.patch("urllib.request.urlopen", side_effect=capture):
            client.complete(system="sys", user="usr", max_tokens=123)
        self.assertLessEqual(captured["body"]["temperature"], 0.2)
        self.assertEqual(captured["body"]["max_tokens"], 123)
        self.assertEqual(captured["body"]["messages"][0]["content"], "sys")
        self.assertEqual(captured["body"]["messages"][1]["content"], "usr")


class BuildClientTests(unittest.TestCase):
    def test_no_key_builds_the_null_client(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(build_llm_client(), NullClient)

    def test_a_blank_key_builds_the_null_client(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "   "}, clear=True):
            self.assertIsInstance(build_llm_client(), NullClient)

    def test_a_key_builds_an_openrouter_client_on_the_free_models(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}, clear=True):
            client = build_llm_client()
        self.assertIsInstance(client, OpenRouterClient)
        self.assertEqual(client.models, DEFAULT_FREE_MODELS)
        self.assertTrue(client.available)

    def test_the_model_list_is_overridable(self):
        # Free model names get retired; nobody should have to patch the source.
        env = {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODELS": "a:free, b:free "}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(build_llm_client().models, ("a:free", "b:free"))

    def test_an_empty_model_list_falls_back_to_the_defaults(self):
        env = {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODELS": " , "}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(build_llm_client().models, DEFAULT_FREE_MODELS)


if __name__ == "__main__":
    unittest.main()
