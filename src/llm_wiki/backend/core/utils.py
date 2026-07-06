import json


def ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def sse_json(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
