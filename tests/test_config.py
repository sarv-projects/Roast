import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.config import (
    GROQ_API_KEYS,
    TAVILY_API_KEY_DEEP,
    UPSTASH_REDIS_REST_URL,
    ENVIRONMENT,
)


def test_config_loads():
    assert GROQ_API_KEYS != "", "GROQ_API_KEYS is empty"
    assert TAVILY_API_KEY_DEEP != "", "TAVILY_API_KEY_DEEP is empty"
    assert UPSTASH_REDIS_REST_URL != "", "UPSTASH_REDIS_REST_URL is empty"
    print(f"\nENVIRONMENT = {ENVIRONMENT}")
    print("Config loaded correctly")


if __name__ == "__main__":
    test_config_loads()