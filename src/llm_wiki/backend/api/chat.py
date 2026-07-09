import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from llm_wiki import chat_store
from llm_wiki.chat import agent
from llm_wiki.chat.retriever import WikiRetriever
from llm_wiki.chat_store import MessageMetrics

from ..core.app_logging import logger
from ..core.error_codes import ErrorCode, raise_api_error, stream_error_text
from ..core.response import success
from ..core.utils import sse_json
from ..schemas import (
    ChatMessageReq,
    ChatStopReq,
    FeedbackReq,
    SessionCreateReq,
    SessionListReq,
    SessionScopeReq,
)

router = APIRouter()

# 对话用的检索器。默认走本地 wiki 检索(RAG);只想要纯对话时可换成 retriever.NullRetriever()。
RETRIEVER = WikiRetriever()

FEEDBACK_INFO_TYPE_LABELS = {
    "not_helpful": "回答没有用",
    "misunderstood_intent": "没有理解我的意图",
    "incorrect_information": "信息/数据有误",
}

MESSAGE_FORMAT_ALIASES = {
    "openai": "dict",
    "dict": "dict",
    "langchain": "tuple",
    "tuple": "tuple",
}


@dataclass
class ActiveChatStream:
    session_id: str
    user_id: Optional[str]
    message_id: str
    request_id: str
    started: float
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    acc: str = ""
    source: str = "llm"
    mode: str = "none"
    refs: list = field(default_factory=list)
    retrieval_ms: int = 0
    first_delta_ms: Optional[int] = None
    model_wait_ms: Optional[int] = None
    total_ms: Optional[int] = None
    message_count: Optional[int] = None
    prompt_chars: Optional[int] = None
    stream_obj: object = None


ACTIVE_CHAT_STREAMS: dict[str, ActiveChatStream] = {}
ACTIVE_CHAT_STREAMS_LOCK = threading.Lock()


def register_active_stream(active: ActiveChatStream) -> None:
    with ACTIVE_CHAT_STREAMS_LOCK:
        ACTIVE_CHAT_STREAMS[active.message_id] = active


def get_active_stream(message_id: Optional[str]) -> Optional[ActiveChatStream]:
    if not message_id:
        return None
    with ACTIVE_CHAT_STREAMS_LOCK:
        return ACTIVE_CHAT_STREAMS.get(message_id)


def unregister_active_stream(message_id: str, active: ActiveChatStream) -> None:
    with ACTIVE_CHAT_STREAMS_LOCK:
        if ACTIVE_CHAT_STREAMS.get(message_id) is active:
            ACTIVE_CHAT_STREAMS.pop(message_id, None)


def close_stream_obj(stream_obj) -> None:
    close = getattr(stream_obj, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.exception("chat stream close failed")


def session_title(text: str) -> str:
    """用首条提问生成会话标题:取首行前 20 字。"""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else "新会话"
    line = line.strip() or "新会话"
    return line[:20] + ("…" if len(line) > 20 else "")


def should_rename_default_session(session_id: str, user_id: Optional[str]) -> bool:
    """仅当当前会话标题仍是默认值时,才用用户问题生成标题。"""
    for session in chat_store.list_sessions(user_id=user_id):
        if session.get("id") == session_id:
            return (session.get("title") or "").strip() == "新会话"
    return False


def normalize_message_format(value: Optional[str]) -> str:
    """归一化 chat message 结构:OpenAI=dict,LangChain=tuple。"""
    key = (value or "openai").strip().lower()
    message_format = MESSAGE_FORMAT_ALIASES.get(key)
    if message_format:
        return message_format
    logger.warning("chat.send.invalid_message_format value=%s fallback=openai", value)
    return "dict"


def normalize_feedback(value: Optional[str]) -> Optional[str]:
    feedback = (value or "").strip()
    if feedback in ("like", "unlike", "NONE"):
        return feedback
    return None


def feedback_reason_json(reason) -> Optional[str]:
    if reason is None:
        return None
    if isinstance(reason, str):
        return reason.strip() or None

    info = (reason.feedback_info or "").strip()
    types = []
    seen = set()
    for item in reason.feedback_info_types or []:
        key = (item or "").strip()
        if key and key not in seen:
            types.append(key)
            seen.add(key)
    if not info and not types:
        return None
    return json.dumps(
        {"feedback_info": info, "feedback_info_types": types},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _matching_recent_assistant(session_id: str, content: str) -> Optional[dict]:
    """查找最近一轮用户问题之后已保存的同一条 assistant 回复。"""
    needle = content or ""
    if not needle:
        return None
    for message in reversed(chat_store.get_messages(session_id)):
        role = message.get("role")
        if role == "user":
            return None
        if role != "assistant":
            continue
        saved = message.get("content") or ""
        if saved == needle or saved.startswith(needle) or needle.startswith(saved):
            return message
    return None


def _message_by_id(session_id: str, message_id: Optional[str]) -> Optional[dict]:
    if not message_id:
        return None
    for message in reversed(chat_store.get_messages(session_id)):
        if message.get("id") == message_id:
            return message
    return None


@router.post("/api/chat/sessions")
def chat_create_session(req: SessionCreateReq):
    session_id = (req.session_id or "").strip() or None
    user_id = (req.user_id or "").strip() or None
    source_code = (req.source_code or "").strip() or "web"
    return success(
        chat_store.create_session(
            req.title or "新会话",
            user_id=user_id,
            source_code=source_code,
            session_id=session_id,
        )
    )


def _scope_user_id(req: Optional[SessionScopeReq]) -> Optional[str]:
    """从可选请求体取出归一化后的 user_id(缺省/空白视为不限定用户)。"""
    return ((req.user_id if req else None) or "").strip() or None


# 部署环境强制 POST:原 GET/DELETE 端点统一改为 POST,user_id 从 query 改到请求体。
@router.post("/api/chat/sessions/list")
def chat_list_sessions(req: Optional[SessionListReq] = None):
    """分页列出会话;请求体带 user_id 时只返回该用户的会话。"""
    user_id = _scope_user_id(req)
    page = req.page if req else 1
    page_size = req.page_size if req else 10
    sessions = chat_store.list_sessions(user_id=user_id)
    start = (page - 1) * page_size
    return success(
        {
            "items": sessions[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": len(sessions),
        }
    )


@router.post("/api/chat/sessions/{session_id}/exists")
def chat_session_exists(session_id: str, req: Optional[SessionScopeReq] = None):
    """判断会话是否存在;请求体带 user_id 时要求会话归属该用户。"""
    user_id = _scope_user_id(req)
    return success({"exists": chat_store.session_exists(session_id, user_id=user_id)})


@router.post("/api/chat/sessions/clear")
def chat_clear_sessions(req: Optional[SessionScopeReq] = None):
    """清空会话;请求体带 user_id 时只清该用户的会话,不传则清空全部。"""
    user_id = _scope_user_id(req)
    deleted = chat_store.clear_sessions(user_id=user_id)
    logger.info(
        f"chat.sessions.clear user_id={user_id or '*'} sessions={deleted['sessions']} "
        f"messages={deleted['messages']} feedback={deleted['feedback']}"
    )
    return success({"ok": True, "deleted": deleted})


@router.post("/api/chat/sessions/{session_id}/messages/list")
def chat_get_messages(session_id: str, req: Optional[SessionScopeReq] = None):
    """读会话消息;请求体带 user_id 时要求会话归属该用户,否则按不存在处理。"""
    user_id = _scope_user_id(req)
    if not chat_store.session_exists(session_id, user_id=user_id):
        raise_api_error(ErrorCode.CHAT_SESSION_NOT_FOUND)
    return success({"items": chat_store.get_messages(session_id)})


@router.post("/api/chat/sessions/{session_id}/delete")
def chat_delete_session(session_id: str, req: Optional[SessionScopeReq] = None):
    """删会话;请求体带 user_id 时只删归属该用户的会话,否则按不存在处理。"""
    user_id = _scope_user_id(req)
    ok = chat_store.delete_session(session_id, user_id=user_id)
    if not ok:
        raise_api_error(ErrorCode.CHAT_SESSION_NOT_FOUND)
    return success({"ok": True})


@router.post("/api/chat/sessions/{session_id}/messages")
def chat_send_message(session_id: str, req: ChatMessageReq):
    """对话主流程:存用户消息 → 检索 → 流式生成 → 存 Agent 回复。"""
    text = (req.content or "").strip()
    if not text:
        raise_api_error(ErrorCode.CHAT_MESSAGE_EMPTY)
    user_id = (req.user_id or "").strip() or None
    if not chat_store.session_exists(session_id, user_id=user_id):
        raise_api_error(ErrorCode.CHAT_SESSION_NOT_FOUND)
    message_format = normalize_message_format(req.message_format)

    should_rename = should_rename_default_session(session_id, user_id)
    chat_store.add_message(session_id, "user", text, user_id=user_id)
    if should_rename:
        try:
            chat_store.rename_session(session_id, session_title(text))
        except Exception:
            logger.exception("chat rename_session failed session_id=%s", session_id)

    request_id = uuid.uuid4().hex[:12]
    assistant_message_id = uuid.uuid4().hex
    started = time.perf_counter()
    active_stream = ActiveChatStream(
        session_id=session_id,
        user_id=user_id,
        message_id=assistant_message_id,
        request_id=request_id,
        started=started,
    )
    register_active_stream(active_stream)
    logger.info(
        "chat.send.start session_id=%s request_id=%s message_id=%s len=%s message_format=%s",
        session_id,
        request_id,
        assistant_message_id,
        len(text),
        message_format,
    )

    def gen():
        acc = ""
        source, mode, refs = "llm", "none", []
        retrieval_ms = 0
        first_delta_ms = None
        model_wait_ms = None
        model_request_start_ms = None
        prompt_stats = {"message_count": None, "char_count": None}
        answer_persisted = False

        def persist_answer(total_ms: Optional[int] = None, allow_empty: bool = False, reason: str = "done"):
            nonlocal answer_persisted
            if answer_persisted or (not allow_empty and not acc.strip()):
                return None
            elapsed_total_ms = (
                total_ms if total_ms is not None else int((time.perf_counter() - started) * 1000)
            )
            existing_by_id = _message_by_id(session_id, assistant_message_id)
            if existing_by_id:
                answer_persisted = True
                return existing_by_id
            existing = _matching_recent_assistant(session_id, acc)
            if existing:
                answer_persisted = True
                logger.info(
                    "chat.send.persist_answer.dedup "
                    "session_id=%s request_id=%s reason=%s message_id=%s chars=%s",
                    session_id,
                    request_id,
                    reason,
                    existing.get("id"),
                    len(acc),
                )
                return existing
            saved_answer = chat_store.add_message(
                session_id,
                "assistant",
                acc,
                MessageMetrics(
                    answer_source=source,
                    retrieval_mode=mode,
                    refs=refs,
                    elapsed_ms=elapsed_total_ms,
                    retrieval_ms=retrieval_ms,
                    model_wait_ms=model_wait_ms,
                    first_delta_ms=first_delta_ms,
                    total_ms=elapsed_total_ms,
                    message_count=prompt_stats["message_count"],
                    prompt_chars=prompt_stats["char_count"],
                ),
                user_id=user_id,
                message_id=assistant_message_id,
            )
            answer_persisted = True
            logger.info(
                "chat.send.persist_answer session_id=%s request_id=%s reason=%s chars=%s total_ms=%s",
                session_id,
                request_id,
                reason,
                len(acc),
                elapsed_total_ms,
            )
            return saved_answer

        try:
            yield sse_json(
                {
                    "type": "status",
                    "request_id": request_id,
                    "session_id": session_id,
                    "message_id": assistant_message_id,
                    "stage": "retrieving",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            retrieve_started = time.perf_counter()
            decision = RETRIEVER.retrieve(text)
            if active_stream.cancel_event.is_set():
                return
            retrieval_ms = decision.get("elapsed_ms", int((time.perf_counter() - retrieve_started) * 1000))
            source = decision["source"]
            mode = decision["mode"]
            refs = decision["refs"]
            with active_stream.lock:
                active_stream.source = source
                active_stream.mode = mode
                active_stream.refs = refs
                active_stream.retrieval_ms = retrieval_ms
            logger.info(
                f"chat.send.retrieved session_id={session_id} request_id={request_id} "
                f"source={source} mode={mode} refs={len(refs)} retrieval_ms={retrieval_ms} "
                f"elapsed_ms={int((time.perf_counter() - started) * 1000)}"
            )
            yield sse_json(
                {
                    "type": "meta",
                    "request_id": request_id,
                    "session_id": session_id,
                    "message_id": assistant_message_id,
                    "source": source,
                    "mode": mode,
                    "refs": refs,
                    "retrieval_ms": retrieval_ms,
                    "message_format": message_format,
                }
            )
            messages = agent.build_answer_messages_compatible(text, decision, message_format=message_format)
            prompt_stats = agent.message_stats(messages)
            logger.info(
                f"chat.send.prompt session_id={session_id} request_id={request_id} "
                f"message_format={message_format} "
                f"message_count={prompt_stats['message_count']} char_count={prompt_stats['char_count']} "
                f"message_lengths={prompt_stats['message_lengths']}"
            )
            stream = agent.stream_messages_compatible(messages, message_format=message_format)
            with active_stream.lock:
                active_stream.stream_obj = stream
                active_stream.message_count = prompt_stats["message_count"]
                active_stream.prompt_chars = prompt_stats["char_count"]
            model_request_start_ms = int((time.perf_counter() - started) * 1000)
            if active_stream.cancel_event.is_set():
                close_stream_obj(stream)
                return
            yield sse_json(
                {
                    "type": "status",
                    "request_id": request_id,
                    "session_id": session_id,
                    "message_id": assistant_message_id,
                    "stage": "generating",
                    "source": source,
                    "mode": mode,
                    "retrieval_ms": retrieval_ms,
                    "message_format": message_format,
                    "message_count": prompt_stats["message_count"],
                    "prompt_chars": prompt_stats["char_count"],
                    "model_start_ms": model_request_start_ms,
                    "elapsed_ms": model_request_start_ms,
                }
            )
            logger.info(
                f"chat.send.model_stream.start session_id={session_id} request_id={request_id} "
                f"source={source} mode={mode} retrieval_ms={retrieval_ms} "
                f"elapsed_ms={int((time.perf_counter() - started) * 1000)}"
            )
            for delta in stream:
                if active_stream.cancel_event.is_set():
                    close_stream_obj(stream)
                    break
                if first_delta_ms is None:
                    first_delta_ms = int((time.perf_counter() - started) * 1000)
                    model_wait_ms = first_delta_ms - (model_request_start_ms or 0)
                    with active_stream.lock:
                        active_stream.first_delta_ms = first_delta_ms
                        active_stream.model_wait_ms = model_wait_ms
                    yield sse_json(
                        {
                            "type": "status",
                            "request_id": request_id,
                            "session_id": session_id,
                            "message_id": assistant_message_id,
                            "stage": "first_delta",
                            "source": source,
                            "mode": mode,
                            "retrieval_ms": retrieval_ms,
                            "message_format": message_format,
                            "model_start_ms": model_request_start_ms,
                            "model_wait_ms": model_wait_ms,
                            "first_delta_ms": first_delta_ms,
                            "elapsed_ms": first_delta_ms,
                        }
                    )
                    logger.info(
                        f"chat.send.first_delta session_id={session_id} request_id={request_id} "
                        f"source={source} mode={mode} retrieval_ms={retrieval_ms} "
                        f"first_delta_ms={first_delta_ms}"
                    )
                acc += delta
                with active_stream.lock:
                    active_stream.acc = acc
                yield sse_json(
                    {
                        "type": "delta",
                        "request_id": request_id,
                        "session_id": session_id,
                        "message_id": assistant_message_id,
                        "text": delta,
                    }
                )
            if active_stream.cancel_event.is_set():
                return
            total_ms = int((time.perf_counter() - started) * 1000)
            if model_wait_ms is None and model_request_start_ms is not None:
                model_wait_ms = total_ms - model_request_start_ms
            with active_stream.lock:
                active_stream.total_ms = total_ms
                active_stream.model_wait_ms = model_wait_ms
            saved = persist_answer(total_ms, allow_empty=True, reason="done")
            yield sse_json(
                {
                    "type": "done",
                    "request_id": request_id,
                    "session_id": session_id,
                    "message_id": saved["id"] if saved else assistant_message_id,
                    "source": source,
                    "mode": mode,
                    "refs": refs,
                    "retrieval_ms": retrieval_ms,
                    "message_format": message_format,
                    "model_start_ms": model_request_start_ms,
                    "model_wait_ms": model_wait_ms,
                    "first_delta_ms": first_delta_ms,
                    "total_ms": total_ms,
                    "message_count": prompt_stats["message_count"],
                    "prompt_chars": prompt_stats["char_count"],
                }
            )
            logger.info(
                f"chat.send.done session_id={session_id} request_id={request_id} source={source} "
                f"mode={mode} chars={len(acc)} retrieval_ms={retrieval_ms} model_wait_ms={model_wait_ms} "
                f"first_delta_ms={first_delta_ms} total_ms={total_ms}"
            )
        except Exception:
            logger.exception("chat.send.error session_id=%s request_id=%s", session_id, request_id)
            if acc.strip():
                try:
                    persist_answer(reason="error")
                except Exception:
                    logger.exception("chat persist partial answer failed")
            yield sse_json(
                {
                    "type": "error",
                    "request_id": request_id,
                    "session_id": session_id,
                    "message_id": assistant_message_id,
                    "code": ErrorCode.INTERNAL_ERROR.code,
                    "error": stream_error_text(request_id),
                }
            )
        finally:
            if acc.strip() and not answer_persisted:
                try:
                    persist_answer(reason="disconnect")
                except Exception:
                    logger.exception("chat persist disconnected partial answer failed")
            unregister_active_stream(assistant_message_id, active_stream)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={"X-Request-ID": request_id, "X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.post("/api/chat/sessions/{session_id}/messages/stop")
def chat_stop_message(session_id: str, req: ChatStopReq):
    """停止生成时立即保存当前 assistant 片段,让前端马上拿到可反馈的 message id。"""
    user_id = (req.user_id or "").strip() or None
    if not chat_store.session_exists(session_id, user_id=user_id):
        raise_api_error(ErrorCode.CHAT_SESSION_NOT_FOUND)

    message_id = (req.message_id or "").strip() or None
    active = get_active_stream(message_id)
    active_snapshot = {}
    if active and active.session_id == session_id:
        active.cancel_event.set()
        with active.lock:
            active_snapshot = {
                "content": active.acc,
                "source": active.source,
                "mode": active.mode,
                "refs": active.refs,
                "retrieval_ms": active.retrieval_ms,
                "model_wait_ms": active.model_wait_ms,
                "first_delta_ms": active.first_delta_ms,
                "total_ms": active.total_ms,
                "message_count": active.message_count,
                "prompt_chars": active.prompt_chars,
                "stream_obj": active.stream_obj,
                "elapsed_ms": int((time.perf_counter() - active.started) * 1000),
            }
        close_stream_obj(active_snapshot.get("stream_obj"))

    content = (active_snapshot.get("content") or req.content or "").strip()
    if not content:
        raise_api_error(ErrorCode.CHAT_MESSAGE_EMPTY)

    existing_by_id = _message_by_id(session_id, message_id)
    if existing_by_id:
        return success({"ok": True, "message": existing_by_id, "deduped": True})

    existing = _matching_recent_assistant(session_id, content)
    if existing:
        return success({"ok": True, "message": existing, "deduped": True})

    total_ms = (
        req.total_ms
        or active_snapshot.get("total_ms")
        or req.elapsed_ms
        or active_snapshot.get("elapsed_ms")
    )
    metrics = MessageMetrics(
        answer_source=req.answer_source or active_snapshot.get("source") or "llm",
        retrieval_mode=req.retrieval_mode or active_snapshot.get("mode") or "none",
        refs=req.refs or active_snapshot.get("refs") or [],
        elapsed_ms=total_ms,
        retrieval_ms=req.retrieval_ms or active_snapshot.get("retrieval_ms"),
        model_wait_ms=req.model_wait_ms or active_snapshot.get("model_wait_ms"),
        first_delta_ms=req.first_delta_ms or active_snapshot.get("first_delta_ms"),
        total_ms=total_ms,
        message_count=req.message_count or active_snapshot.get("message_count"),
        prompt_chars=req.prompt_chars or active_snapshot.get("prompt_chars"),
    )
    try:
        saved = chat_store.add_message(
            session_id,
            "assistant",
            content,
            metrics,
            user_id=user_id,
            message_id=message_id,
        )
    except Exception:
        existing_by_id = _message_by_id(session_id, message_id)
        if existing_by_id:
            return success({"ok": True, "message": existing_by_id, "deduped": True})
        raise
    logger.info(
        "chat.stop.persist session_id=%s message_id=%s chars=%s",
        session_id,
        saved["id"],
        len(content),
    )
    return success({"ok": True, "message": saved, "deduped": False})


@router.post("/api/chat/messages/{message_id}/feedback")
def chat_feedback(message_id: str, req: FeedbackReq):
    feedback = normalize_feedback(req.feedback)
    if feedback not in ("like", "unlike", "NONE"):
        raise_api_error(ErrorCode.CHAT_FEEDBACK_INVALID_RATING)
    req_user_id = (req.user_id or "").strip() or None
    msg = chat_store.message_exists(message_id, user_id=req_user_id)  # 传 user_id 时要求消息归属该用户
    if not msg:
        raise_api_error(ErrorCode.CHAT_MESSAGE_NOT_FOUND)
    if msg["role"] != "assistant":
        raise_api_error(ErrorCode.CHAT_FEEDBACK_ASSISTANT_ONLY)
    if feedback == "NONE":
        chat_store.clear_feedback(message_id)
        return success({"ok": True, "message_id": message_id, "feedback": None, "reason": None})
    reason = feedback_reason_json(req.reason)
    user_id = req_user_id or msg.get("user_id")
    return success(chat_store.set_feedback(message_id, msg["session_id"], feedback, reason, user_id))
