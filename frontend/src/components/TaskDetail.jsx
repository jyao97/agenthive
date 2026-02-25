import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { fetchTask } from "../lib/api";
import { relativeTime, renderMarkdown, extractFileAttachments } from "../lib/formatters";
import FileAttachments from "./FilePreview";
import { POLL_INTERVAL } from "../lib/constants";

function MiniChatBubble({ message, project }) {
  if (message.role === "SYSTEM") {
    return (
      <div className="flex justify-center my-1">
        <span className="inline-block px-2 py-0.5 rounded-full bg-elevated text-xs text-dim">
          {message.content}
        </span>
      </div>
    );
  }

  const isUser = message.role === "USER";

  const attachments = useMemo(
    () => (!isUser ? extractFileAttachments(message.content, project) : []),
    [isUser, message.content, project],
  );

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} my-1`}>
      <div className="max-w-[85%]">
        <div
          className={`rounded-xl px-3 py-2 ${
            isUser
              ? "bg-cyan-600 text-white rounded-br-sm"
              : "bg-inset text-body rounded-bl-sm"
          }`}
        >
          {isUser ? (
            <p className="text-xs whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="text-xs max-h-48 overflow-y-auto">
              {renderMarkdown(message.content, project)}
            </div>
          )}
          <div className={`text-[10px] mt-0.5 ${isUser ? "text-cyan-200" : "text-dim"}`}>
            {relativeTime(message.created_at)}
            {message.status === "FAILED" && (
              <span className="ml-1 text-red-400">Failed</span>
            )}
            {message.status === "TIMEOUT" && (
              <span className="ml-1 text-orange-400">Timed out</span>
            )}
          </div>
        </div>
        {attachments.length > 0 && <FileAttachments attachments={attachments} />}
      </div>
    </div>
  );
}

export default function TaskDetail({ taskId, agentId, project, status }) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchTask(taskId);
      setDetail(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    load();
    const active = ["PENDING", "PLANNING", "PLAN_REVIEW", "EXECUTING"];
    if (!active.includes(status)) return;
    const interval = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [load, status]);

  if (loading) {
    return (
      <div className="px-4 py-6 flex justify-center">
        <span className="text-dim text-sm animate-pulse">Loading details...</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-4 py-3">
        <p className="text-red-400 text-sm">Error loading details: {error}</p>
      </div>
    );
  }
  if (!detail) return null;

  const conversation = detail.conversation || [];

  return (
    <div className="rounded-xl bg-surface shadow-card border border-divider p-4 space-y-3">
      {/* Timing info */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-dim">
        <span>Created: {relativeTime(detail.created_at)}</span>
        {detail.completed_at && <span>Finished: {relativeTime(detail.completed_at)}</span>}
      </div>

      {/* Conversation */}
      {conversation.length > 0 && (
        <div className="bg-page rounded-lg p-3 max-h-96 overflow-y-auto">
          {conversation.map((msg) => (
            <MiniChatBubble key={msg.id} message={msg} project={project} />
          ))}
        </div>
      )}

      {/* Open in Chat */}
      <button
        type="button"
        onClick={() => navigate(`/agents/${agentId}`)}
        className="w-full min-h-[36px] rounded-lg bg-cyan-600/20 text-cyan-400 text-sm font-medium hover:bg-cyan-600/30 transition-colors flex items-center justify-center gap-2"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        Open in Agent Chat
      </button>
    </div>
  );
}
