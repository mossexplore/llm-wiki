import json


def ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
