/**
 * POST-based SSE consumer. EventSource only supports GET; we stream via fetch
 * because review requests carry a JSON body (PR URL, optional API key).
 */
export class ReviewStreamError extends Error {
  constructor(message, status = 500) {
    super(message);
    this.name = "ReviewStreamError";
    this.status = status;
  }
}

function parseSsePayload(chunk) {
  const line = chunk
    .split("\n")
    .map((part) => part.trim())
    .find((part) => part.startsWith("data:"));
  if (!line) return null;

  const raw = line.slice(5).trim();
  if (!raw) return null;

  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new ReviewStreamError(
      `Invalid stream data from server (${err instanceof Error ? err.message : "parse error"}).`,
      500
    );
  }
}

export async function consumeReviewStream(response, onEvent) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ReviewStreamError(
      data.detail || `Request failed (${response.status})`,
      response.status
    );
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new ReviewStreamError("Streaming is not supported in this browser.");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let sawDone = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";

      for (const chunk of chunks) {
        const payload = parseSsePayload(chunk);
        if (!payload) continue;
        if (payload.type === "ping") continue;
        if (payload.type === "done") {
          sawDone = true;
          continue;
        }
        onEvent(payload);
      }
    }

    const trailing = parseSsePayload(buffer);
    if (trailing) {
      if (trailing.type === "done") {
        sawDone = true;
      } else if (trailing.type !== "ping") {
        onEvent(trailing);
      }
    }
  } catch (err) {
    if (err instanceof ReviewStreamError) throw err;
    throw new ReviewStreamError(
      err instanceof Error ? err.message : "Stream read failed.",
      500
    );
  }

  return sawDone;
}
