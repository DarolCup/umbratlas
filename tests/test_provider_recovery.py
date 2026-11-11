from copy import deepcopy
from http.client import RemoteDisconnected
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from umbratlas.provider import ApiError, GHOSTMODEL_URL, OutputError, Provider
from umbratlas.samples import SAMPLES
from umbratlas.core import validate_roadmap


def api_response(body):
    return io.BytesIO(json.dumps(body, ensure_ascii=False).encode())


def chat_response(text, finish="stop"):
    return api_response({"choices": [{"message": {"content": text}, "finish_reason": finish}]})


def sse_response(events):
    body = "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
    response = io.BytesIO((body + "data: [DONE]\n\n").encode())
    response.headers = {"Content-Type": "text/event-stream; charset=utf-8"}
    return response


def rejection(status, error):
    return HTTPError(GHOSTMODEL_URL, status, "API error", {}, api_response({"error": error}))


class ProviderRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.provider = Provider("fixture-key", "fixture-model", url=GHOSTMODEL_URL, protocol="chat")

    def test_recovers_json_with_reasoning_markdown_and_trailing_commas_in_one_call(self):
        text = (
            '<think>пример {"test": 1}</think>\nВот вопросы:\n'
            '\x60\x60\x60JSON\n{"questions":["Какая цель?", "Есть опыт?", "Сколько времени?",],}\n'
            '\x60\x60\x60\nНачнём после ответов.'
        )
        reply = chat_response([{"type": "reasoning", "text": "internal"}, {"type": "text", "text": text}])
        with patch("umbratlas.provider.build_opener") as opener:
            opener.return_value.open.return_value = reply
            self.assertEqual(self.provider.interview("C++"), ["Какая цель?", "Есть опыт?", "Сколько времени?"])
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_recovers_plain_interview_questions_without_a_second_paid_request(self):
        with patch("umbratlas.provider.build_opener") as opener:
            opener.return_value.open.return_value = chat_response(
                "1. Какая цель?\n2. Опиши свой опыт.\n3. Сколько времени есть?"
            )
            self.assertEqual(len(self.provider.interview("C++")), 3)
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_explicit_json_parameter_rejection_retries_without_that_parameter(self):
        refused = rejection(400, {"message": "response_format is not supported"})
        successful = chat_response('{"questions":["Цель?", "Опыт?", "Время?"]}')
        with patch("umbratlas.provider.build_opener") as opener:
            opener.return_value.open.side_effect = [refused, successful]
            self.assertEqual(len(self.provider.interview("C++")), 3)
            requests = [json.loads(call.args[0].data) for call in opener.return_value.open.call_args_list]
            self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
            self.assertNotIn("response_format", requests[1])

    def test_ghostmodel_switches_protocol_after_explicit_model_endpoint_rejection(self):
        refused = rejection(400, {"type": "MODEL_ENDPOINT_NOT_SUPPORTED"})
        successful = api_response({"output_text": '{"questions":["Цель?", "Опыт?", "Время?"]}'})
        with patch("umbratlas.provider.build_opener") as opener:
            opener.return_value.open.side_effect = [refused, successful]
            self.assertEqual(len(self.provider.interview("C++")), 3)
            urls = [call.args[0].full_url for call in opener.return_value.open.call_args_list]
            self.assertEqual(urls, [GHOSTMODEL_URL + "/chat/completions", GHOSTMODEL_URL + "/responses"])

    def test_streamed_roadmap_is_assembled_and_validated(self):
        roadmap = deepcopy(SAMPLES["cpp"])
        roadmap["topics"][0]["id"] = "1"
        roadmap["topics"][1]["prerequisites"] = ["1"]
        raw = json.dumps({"roadmap": roadmap}, ensure_ascii=False)
        chunks = (raw[:150], raw[150:])
        reply = sse_response([
            {"choices": [{"delta": {"reasoning_content": "internal", "content": chunk}}]} for chunk in chunks
        ] + [{"choices": [{"delta": {}, "finish_reason": "stop"}]}])
        with patch("umbratlas.provider.build_opener") as opener:
            opener.return_value.open.return_value = reply
            result = self.provider.roadmap("C++", [])
            self.assertEqual(result, validate_roadmap(SAMPLES["cpp"]))
            request = opener.return_value.open.call_args
            self.assertTrue(json.loads(request.args[0].data)["stream"])
            self.assertGreater(request.kwargs["timeout"], 65)

    def test_timeout_disconnect_balance_and_truncation_have_distinct_errors_and_no_retry(self):
        cases = [
            (URLError(TimeoutError()), "мог уже использовать токены"),
            (RemoteDisconnected(), "Соединение оборвалось"),
            (rejection(402, {"type": "INSUFFICIENT_BALANCE", "message": "fixture-key"}), "недостаточно средств"),
            (chat_response('{"title":"C++","topics":[', "length"), "лимит ответа"),
        ]
        for result, expected in cases:
            with self.subTest(expected=expected), patch("umbratlas.provider.build_opener") as opener:
                if isinstance(result, Exception):
                    opener.return_value.open.side_effect = result
                else:
                    opener.return_value.open.return_value = result
                with self.assertRaisesRegex(ApiError, expected) as raised:
                    self.provider.roadmap("C++", [])
                self.assertNotIn("fixture-key", str(raised.exception))
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_ignores_unsupported_search_and_unattributed_links(self):
        with patch("umbratlas.provider.build_opener") as opener:
            opener.return_value.open.return_value = api_response({
                "status": "completed", "output_text": "Вот ссылка: https://unverified.example"
            })
            with self.assertRaisesRegex(OutputError, "не вернула источники"):
                self.provider.resources("C++", SAMPLES["cpp"]["topics"][0], "Русский")
            self.assertTrue(opener.return_value.open.call_args.args[0].full_url.endswith("/responses"))
