import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..app_logging import logger
from ..schemas import ChatMessageReq, FeedbackReq, SessionCreateReq
from ..utils import ndjson

from llm_wiki import chat_store
from llm_wiki.knowledge import agent  # noqa: E402

router = APIRouter()


def session_title(text: str) -> str:
    """用首条提问生成会话标题:取首行前 20 字。"""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else "新会话"
    line = line.strip() or "新会话"
    return line[:20] + ("…" if len(line) > 20 else "")


@router.post("/api/chat/sessions")
def chat_create_session(req: SessionCreateReq):
    user_id = (req.user_id or "").strip() or None
    source_code = (req.source_code or "").strip() or "web"
    return chat_store.create_session(req.title or "新会话", user_id=user_id, source_code=source_code)


@router.get("/api/chat/sessions")
def chat_list_sessions():
    return {"items": chat_store.list_sessions()}


@router.delete("/api/chat/sessions")
def chat_clear_sessions():
    deleted = chat_store.clear_sessions()
    logger.info(
        "chat.sessions.clear sessions=%s messages=%s feedback=%s",
        deleted["sessions"], deleted["messages"], deleted["feedback"],
    )
    return {"ok": True, "deleted": deleted}


@router.get("/api/chat/sessions/{session_id}/messages")
def chat_get_messages(session_id: str):
    if not chat_store.session_exists(session_id):
        raise HTTPException(404, "会话不存在")
    return {"items": chat_store.get_messages(session_id)}


@router.delete("/api/chat/sessions/{session_id}")
def chat_delete_session(session_id: str):
    ok = chat_store.delete_session(session_id)
    if not ok:
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.post("/api/chat/sessions/{session_id}/messages")
def chat_send_message(session_id: str, req: ChatMessageReq):
    """对话主流程:存用户消息 → 检索 → 流式生成 → 存 Agent 回复。"""
    text = (req.content or "").strip()
    if not text:
        raise HTTPException(400, "内容为空")
    if not chat_store.session_exists(session_id):
        raise HTTPException(404, "会话不存在")

    has_history = chat_store.has_messages(session_id)
    user_id = (req.user_id or "").strip() or None
    chat_store.add_message(session_id, "user", text, user_id=user_id)
    if not has_history:
        try:
            chat_store.rename_session(session_id, session_title(text))
        except Exception:
            logger.exception("chat rename_session failed session_id=%s", session_id)

    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    logger.info("chat.send.start session_id=%s request_id=%s len=%s", session_id, request_id, len(text))

    def gen():
        acc = ""
        source, mode, refs = "llm", "none", []
        retrieval_ms = 0
        first_delta_ms = None
        model_wait_ms = None
        model_request_start_ms = None
        prompt_stats = {"message_count": None, "char_count": None, "history_messages": None}
        try:
            yield ndjson({
                "type": "status", "request_id": request_id, "stage": "retrieving",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            })
            retrieve_started = time.perf_counter()
            decision = agent.retrieve(text)
            retrieval_ms = decision.get("elapsed_ms", int((time.perf_counter() - retrieve_started) * 1000))
            source = decision["source"]
            mode = decision["mode"]
            refs = decision["refs"]
            logger.info(
                "chat.send.retrieved session_id=%s request_id=%s source=%s mode=%s refs=%s retrieval_ms=%s elapsed_ms=%s",
                session_id, request_id, source, mode, len(refs), retrieval_ms,
                int((time.perf_counter() - started) * 1000),
            )
            yield ndjson({
                "type": "meta", "request_id": request_id, "session_id": session_id,
                "source": source, "mode": mode, "refs": refs,
                "retrieval_ms": retrieval_ms,
            })
            messages = agent.build_answer_messages(text, None, decision)
            prompt_stats = agent.message_stats(messages)
            logger.info(
                "chat.send.prompt session_id=%s request_id=%s message_count=%s char_count=%s history_messages=%s message_lengths=%s",
                session_id, request_id, prompt_stats["message_count"], prompt_stats["char_count"],
                prompt_stats["history_messages"], prompt_stats["message_lengths"],
            )
            stream = agent.stream_messages(messages)
            model_request_start_ms = int((time.perf_counter() - started) * 1000)
            yield ndjson({
                "type": "status", "request_id": request_id, "stage": "generating",
                "source": source, "mode": mode, "retrieval_ms": retrieval_ms,
                "message_count": prompt_stats["message_count"],
                "prompt_chars": prompt_stats["char_count"],
                "history_messages": prompt_stats["history_messages"],
                "model_start_ms": model_request_start_ms,
                "elapsed_ms": model_request_start_ms,
            })
            logger.info(
                "chat.send.model_stream.start session_id=%s request_id=%s source=%s mode=%s retrieval_ms=%s elapsed_ms=%s",
                session_id, request_id, source, mode, retrieval_ms,
                int((time.perf_counter() - started) * 1000),
            )
            for delta in stream:
                if first_delta_ms is None:
                    first_delta_ms = int((time.perf_counter() - started) * 1000)
                    model_wait_ms = first_delta_ms - (model_request_start_ms or 0)
                    yield ndjson({
                        "type": "status", "request_id": request_id, "stage": "first_delta",
                        "source": source, "mode": mode, "retrieval_ms": retrieval_ms,
                        "model_start_ms": model_request_start_ms,
                        "model_wait_ms": model_wait_ms,
                        "first_delta_ms": first_delta_ms,
                        "elapsed_ms": first_delta_ms,
                    })
                    logger.info(
                        "chat.send.first_delta session_id=%s request_id=%s source=%s mode=%s retrieval_ms=%s first_delta_ms=%s",
                        session_id, request_id, source, mode, retrieval_ms, first_delta_ms,
                    )
                acc += delta
                yield ndjson({"type": "delta", "request_id": request_id, "text": delta})
            total_ms = int((time.perf_counter() - started) * 1000)
            if model_wait_ms is None and model_request_start_ms is not None:
                model_wait_ms = total_ms - model_request_start_ms
            saved = chat_store.add_message(
                session_id, "assistant", acc,
                answer_source=source, retrieval_mode=mode, refs=refs,
                elapsed_ms=total_ms,
                retrieval_ms=retrieval_ms,
                model_wait_ms=model_wait_ms,
                first_delta_ms=first_delta_ms,
                total_ms=total_ms,
                message_count=prompt_stats["message_count"],
                prompt_chars=prompt_stats["char_count"],
                history_messages=prompt_stats["history_messages"],
                user_id=user_id,
            )
            yield ndjson({
                "type": "done", "request_id": request_id, "message_id": saved["id"],
                "source": source, "mode": mode, "refs": refs,
                "retrieval_ms": retrieval_ms,
                "model_start_ms": model_request_start_ms,
                "model_wait_ms": model_wait_ms,
                "first_delta_ms": first_delta_ms,
                "total_ms": total_ms,
                "message_count": prompt_stats["message_count"],
                "prompt_chars": prompt_stats["char_count"],
                "history_messages": prompt_stats["history_messages"],
            })
            logger.info(
                "chat.send.done session_id=%s request_id=%s source=%s mode=%s chars=%s retrieval_ms=%s model_wait_ms=%s first_delta_ms=%s total_ms=%s",
                session_id, request_id, source, mode, len(acc), retrieval_ms, model_wait_ms,
                first_delta_ms, total_ms,
            )
        except Exception as e:
            logger.exception("chat.send.error session_id=%s request_id=%s", session_id, request_id)
            if acc.strip():
                try:
                    chat_store.add_message(
                        session_id, "assistant", acc,
                        answer_source=source, retrieval_mode=mode, refs=refs,
                        retrieval_ms=retrieval_ms,
                        model_wait_ms=model_wait_ms,
                        first_delta_ms=first_delta_ms,
                        total_ms=int((time.perf_counter() - started) * 1000),
                        message_count=prompt_stats.get("message_count"),
                        prompt_chars=prompt_stats.get("char_count"),
                        history_messages=prompt_stats.get("history_messages"),
                        user_id=user_id,
                    )
                except Exception:
                    logger.exception("chat persist partial answer failed")
            yield ndjson({"type": "error", "request_id": request_id, "error": str(e)})

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"X-Request-ID": request_id, "X-Accel-Buffering": "no"},
    )


@router.post("/api/chat/messages/{message_id}/feedback")
def chat_feedback(message_id: str, req: FeedbackReq):
    if req.rating not in ("up", "down"):
        raise HTTPException(400, "rating 必须为 up 或 down")
    msg = chat_store.message_exists(message_id)
    if not msg:
        raise HTTPException(404, "消息不存在")
    if msg["role"] != "assistant":
        raise HTTPException(400, "只能对 Agent 回复反馈")
    reason = (req.reason or "").strip() or None
    if req.rating == "down" and not reason:
        raise HTTPException(400, "点踩请填写原因")
    user_id = (req.user_id or "").strip() or msg.get("user_id")
    return chat_store.set_feedback(message_id, msg["session_id"], req.rating, reason, user_id)
