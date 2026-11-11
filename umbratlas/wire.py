"""Read model text from JSON and event streams without mixing in reasoning."""

from collections import defaultdict
import json
import re
from time import monotonic

THINK = re.compile(r"<(?:think|analysis)>.*?</(?:think|analysis)>", re.I | re.S)
TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])')
QUESTION_LINE = re.compile(r"^\s*(?:\d+[.)]\s*|[-*•]\s+)(.+)\s*$")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def objects(answer):
    """Yield complete JSON objects, including ones embedded in prose."""
    text = THINK.sub("", answer).strip().lstrip("\ufeff")
    decoder = json.JSONDecoder()
    # Try the unmodified text first so Markdown and code inside JSON strings survive.
    variants = dict.fromkeys((answer.strip().lstrip("\ufeff"), text,
                             TRAILING_COMMA.sub(lambda m: "" if m[0] == "," else m[0], text)))
    for candidate in variants:
        offset = 0
        while (start := candidate.find("{", offset)) != -1:
            try:
                value, end = decoder.raw_decode(candidate, start)
            except (ValueError, RecursionError):
                offset = start + 1
            else:
                yield value
                offset = end


def plain_questions(answer):
    """Recover a numbered or bulleted list of questions as plain text."""
    questions = [
        match[1] for line in THINK.sub("", answer).splitlines()
        if (match := QUESTION_LINE.fullmatch(line))
    ]
    return questions if 1 <= len(questions) <= 17 else []


def text_parts(value):
    if isinstance(value, str):
        return [{"text": value}]
    if not isinstance(value, list):
        return []
    return [
        part for part in value
        if isinstance(part, dict) and isinstance(part.get("text"), str)
        and part.get("type") in (None, "text", "output_text")
    ]


def events(lines, timeout):
    """Yield JSON data from SSE events, stopping at the protocol terminator."""
    data = []
    bytes_read = 0
    deadline = monotonic() + timeout
    for line in lines:
        if monotonic() > deadline:
            raise TimeoutError
        bytes_read += len(line)
        if bytes_read > MAX_RESPONSE_BYTES:
            raise ValueError("Event stream is too large")
        line = line.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            payload = "\n".join(data)
            if payload == "[DONE]":
                return
            yield json.loads(payload)
            data.clear()
    if data and data != ["[DONE]"]:
        yield json.loads("\n".join(data))


def streamed_response(lines, timeout):
    """Rebuild the parts used by the app from OpenAI-compatible events."""
    pieces = defaultdict(list)
    annotations = defaultdict(list)
    output = {}
    choice = None
    finish = None
    response_events = False
    for event in events(lines, timeout):
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type", ""))
        if event.get("error") or kind == "error":
            return {"error": event.get("error") or event}
        if kind in ("response.completed", "response.incomplete", "response.failed"):
            response_events = True
            result = event.get("response") or {}
            if not isinstance(result, dict):
                raise ValueError("Unexpected event response")
            result.setdefault("status", kind.removeprefix("response."))
            if result.get("output"):
                return result
            finish = result["status"]
            break
        if kind.startswith("response."):
            response_events = True
        if kind == "response.output_item.done":
            output[event.get("output_index", 0)] = event.get("item", {})
        elif kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
            pieces[event.get("output_index", 0), event.get("content_index", 0)].append(event["delta"])
        elif kind == "response.output_text.done" and isinstance(event.get("text"), str):
            pieces[event.get("output_index", 0), event.get("content_index", 0)] = [event["text"]]
        elif kind == "response.output_text.annotation.added":
            annotations[event.get("output_index", 0), event.get("content_index", 0)].append(event.get("annotation", {}))
        elif event.get("choices"):
            choice = event["choices"][0]
            finish = choice.get("finish_reason") or finish
            for part in text_parts(choice.get("delta", {}).get("content")):
                pieces[0, 0].append(part["text"])
            if finish:
                break
    if response_events:
        for (index, part), values in sorted(pieces.items()):
            if index not in output:
                output[index] = {"type": "message", "content": []}
            content = output[index].get("content")
            if isinstance(content, list) and part >= len(content):
                content.append({"type": "output_text", "text": "".join(values),
                                "annotations": annotations[index, part]})
        return {"status": finish or "incomplete", "output": [output[i] for i in sorted(output)]}
    if choice is not None:
        return {"choices": [{"message": {"content": "".join(pieces[0, 0])},
                             "finish_reason": finish}]}
    return {}
