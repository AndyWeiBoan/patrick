#!/usr/bin/env python3
"""Hook: PostToolUse — capture tool results and send to Patrick server."""
import json
import sys
import urllib.request

SERVER_URL = "http://127.0.0.1:3141/observe"
TIMEOUT = 3
MAX_OUTPUT_BYTES = 8_192  # truncate large tool outputs
MAX_SEMANTIC_CHARS = 500  # max chars for semantic output snippet


def format_tool_text(tool_name: str, tool_input: dict, tool_response: dict) -> str:
    """Convert raw tool data into natural-language text for better embedding quality."""

    def extract_output_text(response: dict) -> str:
        """Pull plain text from tool_response, strip JSON wrapper noise."""
        if isinstance(response, str):
            return response
        # Claude tool responses are often {"type": "tool_result", "content": [...]}
        content = response.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts).strip()
        if isinstance(content, str):
            return content
        return json.dumps(response, ensure_ascii=False)

    name_lower = tool_name.lower()

    # --- Bash ---
    if name_lower == "bash":
        command = tool_input.get("command", "").strip()
        output = extract_output_text(tool_response)
        snippet = output[:MAX_SEMANTIC_CHARS].strip()
        if len(output) > MAX_SEMANTIC_CHARS:
            snippet += "...[truncated]"
        if snippet:
            return f"執行了指令：{command}\n結果：{snippet}"
        return f"執行了指令：{command}"

    # --- Read ---
    if name_lower == "read":
        file_path = tool_input.get("file_path", tool_input.get("path", ""))
        return f"讀取了檔案：{file_path}"

    # --- Write ---
    if name_lower == "write":
        file_path = tool_input.get("file_path", tool_input.get("path", ""))
        return f"寫入了檔案：{file_path}"

    # --- Edit ---
    if name_lower in ("edit", "multiedit"):
        file_path = tool_input.get("file_path", tool_input.get("path", ""))
        old = tool_input.get("old_string", "")[:80]
        new = tool_input.get("new_string", "")[:80]
        return f"修改了檔案：{file_path}，舊內容：{old!r}，新內容：{new!r}"

    # --- Glob ---
    if name_lower == "glob":
        pattern = tool_input.get("pattern", "")
        return f"搜尋了檔案 glob：{pattern}"

    # --- Grep ---
    if name_lower == "grep":
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        return f"搜尋了程式碼，pattern：{pattern}，路徑：{path}"

    # --- WebFetch / WebSearch ---
    if name_lower in ("webfetch", "websearch"):
        query = tool_input.get("query", tool_input.get("url", ""))
        return f"網路查詢：{query}"

    # --- Default fallback: tool name + compact input, no raw output ---
    input_summary = json.dumps(tool_input, ensure_ascii=False)[:200]
    return f"使用了工具 {tool_name}，輸入：{input_summary}"


def main() -> None:
    try:
        stdin_raw = sys.stdin.read()
        data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except Exception:
        return

    session_id = data.get("session_id") or data.get("sessionId", "")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})

    if not session_id:
        return

    # Guard: truncate tool_response bytes before processing
    response_str = json.dumps(tool_response, ensure_ascii=False)
    if len(response_str.encode()) > MAX_OUTPUT_BYTES:
        tool_response = {"content": response_str[:MAX_OUTPUT_BYTES] + "...[truncated]"}

    text = format_tool_text(tool_name, tool_input, tool_response)

    payload = json.dumps({
        "hook": "post-tool-use",
        "session_id": session_id,
        "text": text,
        "role": "assistant",
        "tool_name": tool_name,
    }).encode()

    try:
        req = urllib.request.Request(
            SERVER_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT)
    except Exception:
        pass  # fire-and-forget


if __name__ == "__main__":
    main()
