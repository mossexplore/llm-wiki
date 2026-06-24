import json

from llm_wiki.backend.api.chat import feedback_reason_json, normalize_feedback
from llm_wiki.backend.schemas import FeedbackReasonReq, FeedbackReq


def test_normalize_feedback_accepts_contract_values():
    assert normalize_feedback("like") == "like"
    assert normalize_feedback("dislike") == "dislike"
    assert normalize_feedback("none") == "none"
    assert normalize_feedback("cancel") == "none"
    assert normalize_feedback("up") is None
    assert normalize_feedback("down") is None
    assert normalize_feedback("bad") is None


def test_feedback_reason_json_keeps_structured_reason():
    reason = FeedbackReasonReq(
        feedback_info="回答不清楚",
        feedback_info_types=["not_helpful", "incorrect_information"],
    )

    saved = json.loads(feedback_reason_json(reason))

    assert saved == {
        "feedback_info": "回答不清楚",
        "feedback_info_types": ["not_helpful", "incorrect_information"],
    }


def test_feedback_reason_json_filters_unknown_and_duplicate_types():
    reason = FeedbackReasonReq(
        feedback_info="",
        feedback_info_types=["not_helpful", "unknown", "not_helpful", "misunderstood_intent"],
    )

    saved = json.loads(feedback_reason_json(reason))

    assert saved == {
        "feedback_info": "",
        "feedback_info_types": ["not_helpful", "misunderstood_intent"],
    }


def test_feedback_req_accepts_nested_reason_payload():
    req = FeedbackReq.model_validate(
        {
            "feedback": "dislike",
            "reason": {
                "feedback_info": "回答不清楚",
                "feedback_info_types": ["incorrect_information"],
            },
        }
    )

    assert req.feedback == "dislike"
    assert req.reason.feedback_info == "回答不清楚"
    assert req.reason.feedback_info_types == ["incorrect_information"]


def test_feedback_reason_json_keeps_legacy_string_reason():
    assert feedback_reason_json(" 不准 ") == "不准"
