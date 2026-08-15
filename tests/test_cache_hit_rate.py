from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT
    / "plugins"
    / "codex-cache-hit-rate"
    / "scripts"
    / "cache_hit_rate.py"
)
SPEC = importlib.util.spec_from_file_location("cache_hit_rate", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
cache_hit_rate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cache_hit_rate
SPEC.loader.exec_module(cache_hit_rate)


def token_record(
    input_tokens: int,
    cached_tokens: int,
    total_input_tokens: int,
    total_cached_tokens: int,
    cache_write_tokens: int = 0,
    model: str | None = None,
) -> dict:
    record = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_write_input_tokens": cache_write_tokens,
                },
                "total_token_usage": {
                    "input_tokens": total_input_tokens,
                    "cached_input_tokens": total_cached_tokens,
                    "cache_write_input_tokens": cache_write_tokens,
                    "total_tokens": total_input_tokens + 100,
                },
            },
        },
    }
    if model is not None:
        record["payload"]["model"] = model
    return record


class CacheHitRateTests(unittest.TestCase):
    def write_transcript(self, records: list[object], malformed: bool = False) -> str:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        with handle:
            if malformed:
                handle.write("not-json\n")
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return handle.name

    def test_aggregates_only_requested_turn_and_deduplicates_totals(self) -> None:
        duplicate = token_record(200, 150, 300, 200)
        path = self.write_transcript(
            [
                {"type": "turn_context", "payload": {"turn_id": "old"}},
                token_record(100, 50, 100, 50),
                {
                    "type": "turn_context",
                    "payload": {"turn_id": "target", "model": "gpt-5.6-sol"},
                },
                duplicate,
                duplicate,
                token_record(
                    300,
                    240,
                    600,
                    440,
                    cache_write_tokens=25,
                    model="gpt-5.6-luna",
                ),
            ]
        )

        usage = cache_hit_rate.read_turn_usage(path, "target")

        self.assertEqual(
            usage,
            {
                "gpt-5.6-sol": cache_hit_rate.Usage(200, 150, 0, 1),
                "gpt-5.6-luna": cache_hit_rate.Usage(300, 240, 25, 1),
            },
        )

    def test_falls_back_to_latest_usage_when_turn_markers_are_unavailable(self) -> None:
        path = self.write_transcript(
            [token_record(100, 20, 100, 20), token_record(250, 200, 350, 220)],
            malformed=True,
        )

        usage = cache_hit_rate.read_turn_usage(path, "missing")

        self.assertEqual(
            usage,
            {cache_hit_rate.UNKNOWN_MODEL: cache_hit_rate.Usage(250, 200, 0, 1)},
        )

    def test_supports_responses_api_cached_token_shape(self) -> None:
        path = self.write_transcript(
            [
                {
                    "type": "turn_context",
                    "payload": {"turn_id": "target", "model": "gpt-5.6-sol"},
                },
                {
                    "type": "response",
                    "usage": {
                        "input_tokens": 1000,
                        "input_tokens_details": {
                            "cached_tokens": 750,
                            "cache_write_tokens": 100,
                        },
                    },
                },
            ]
        )

        usage = cache_hit_rate.read_turn_usage(path, "target")

        self.assertEqual(
            usage,
            {"gpt-5.6-sol": cache_hit_rate.Usage(1000, 750, 100, 1)},
        )

    def test_formats_rate_and_counts(self) -> None:
        message = cache_hit_rate.format_message(
            {
                "gpt-5.6-sol": cache_hit_rate.Usage(500, 390, 25, 2),
                "gpt-5.6-luna": cache_hit_rate.Usage(1000, 900, 0, 1),
            }
        )

        self.assertEqual(
            message,
            "缓存命中率（按模型）：\n"
            "- gpt-5.6-sol：78.00% · 缓存输入 390 / 500 tokens · "
            "缓存写入 25 tokens · 2 次调用\n"
            "- gpt-5.6-luna：90.00% · 缓存输入 900 / 1,000 tokens · 1 次调用",
        )

    def test_returns_unavailable_message_without_transcript(self) -> None:
        output = cache_hit_rate.build_hook_output(
            {"hook_event_name": "Stop", "turn_id": "target"}
        )

        self.assertEqual(
            output,
            {"continue": True, "systemMessage": "缓存命中率：暂无可用 token 统计"},
        )

    def test_ignores_other_hook_events(self) -> None:
        self.assertEqual(
            cache_hit_rate.build_hook_output({"hook_event_name": "SessionStart"}),
            {"continue": True},
        )


if __name__ == "__main__":
    unittest.main()
