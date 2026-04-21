"""patrick CLI — init, start, setup, doctor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(name="patrick", help="Patrick memory server CLI")

# ── helpers ──────────────────────────────────────────────────────────────────

def _hooks_dir() -> Path:
    """Return the absolute path to the installed hooks directory.

    Works for both editable installs (src/patrick/hooks/) and wheel installs
    (site-packages/patrick/hooks/).
    """
    import importlib.resources as ir
    # importlib.resources.files() works in Python 3.9+ and handles both
    # editable installs and normal wheel installs correctly.
    pkg_root = ir.files("patrick")
    hooks = Path(str(pkg_root)) / "hooks"
    return hooks.resolve()


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _load_settings() -> dict:
    p = _settings_path()
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _save_settings(data: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def _hook_entry(script: Path, async_: bool = True) -> dict:
    return {
        "type": "command",
        "command": f"python3 {script}",
        "async": async_,
    }


def _desired_mcp_entry() -> dict:
    return {"type": "sse", "url": "http://127.0.0.1:3141/sse"}


def _desired_hooks(hooks_dir: Path) -> dict:
    return {
        "SessionStart": [{"matcher": "", "hooks": [_hook_entry(hooks_dir / "session_start.py")]}],
        "UserPromptSubmit": [{"matcher": "", "hooks": [_hook_entry(hooks_dir / "prompt_submit.py")]}],
        "PostToolUse": [{"matcher": "", "hooks": [_hook_entry(hooks_dir / "post_tool_use.py")]}],
        "Stop": [{"matcher": "", "hooks": [_hook_entry(hooks_dir / "stop.py")]}],
    }


# ── commands ─────────────────────────────────────────────────────────────────

@app.command()
def init() -> None:
    """Pre-download embedding model and run sanity check."""
    typer.echo("Patrick init — downloading embedding model...")

    try:
        from fastembed import TextEmbedding
        from tokenizers import Tokenizer
        from .config import EMBEDDING_MODEL_FASTEMBED
    except ImportError as e:
        typer.secho(f"✗ Import error: {e}", fg=typer.colors.RED)
        typer.secho(
            "  Run: pip install patrick-memory", fg=typer.colors.YELLOW
        )
        raise typer.Exit(1)

    # Pre-download model (fastembed caches to ~/.cache/fastembed/)
    typer.echo(f"  Model: {EMBEDDING_MODEL_FASTEMBED}")
    try:
        model = TextEmbedding(model_name=EMBEDDING_MODEL_FASTEMBED)
        typer.echo("  Embedding model: ✓ downloaded / cached")
    except Exception as e:
        typer.secho(f"✗ Failed to load embedding model: {e}", fg=typer.colors.RED)
        typer.secho(
            "  On M-series Mac, if you see onnxruntime errors:\n"
            "  pip install onnxruntime-silicon",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)

    # Tokenizer check
    try:
        tokenizer = Tokenizer.from_pretrained(EMBEDDING_MODEL_FASTEMBED)
        typer.echo("  Tokenizer: ✓ loaded")
    except Exception as e:
        typer.secho(f"✗ Tokenizer load failed: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    # Dummy embedding sanity check
    try:
        result = list(model.embed(["hello world"]))
        assert len(result) == 1
        assert len(result[0]) > 0
        typer.echo(f"  Sanity check: ✓ embedded 1 text → {len(result[0])}-dim vector")
    except Exception as e:
        typer.secho(f"✗ Embedding sanity check failed: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    # LanceDB check
    try:
        import lancedb
        from .config import DATA_DIR
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(DATA_DIR))
        typer.echo(f"  LanceDB: ✓ connected at {DATA_DIR}")
    except Exception as e:
        typer.secho(f"✗ LanceDB init failed: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(
        "\n✓ Patrick init complete. Run: patrick start",
        fg=typer.colors.GREEN,
    )


@app.command()
def start() -> None:
    """Start the Patrick memory server."""
    from .server import main
    main()


@app.command()
def hooks_path() -> None:
    """Print the absolute path to the installed hooks directory."""
    typer.echo(str(_hooks_dir()))


@app.command()
def setup(
    auto: bool = typer.Option(
        False, "--auto", help="Apply changes to ~/.claude/settings.json without prompting."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be changed but do not write anything."
    ),
) -> None:
    """Configure Claude Code hooks and MCP server for Patrick.

    Shows the exact settings.json changes needed, then optionally applies them.
    """
    hooks_dir = _hooks_dir()
    settings_file = _settings_path()

    typer.secho("\nPatrick setup", fg=typer.colors.BRIGHT_CYAN, bold=True)
    typer.echo("=" * 50)

    # ── Step 1: verify hooks exist ─────────────────────────────────────────
    typer.echo("\n[1/3] Hook scripts")
    expected_hooks = [
        "session_start.py",
        "prompt_submit.py",
        "post_tool_use.py",
        "stop.py",
    ]
    all_present = True
    for name in expected_hooks:
        p = hooks_dir / name
        if p.exists():
            typer.secho(f"  ✓ {p}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  ✗ MISSING: {p}", fg=typer.colors.RED)
            all_present = False

    if not all_present:
        typer.secho(
            "\n  Some hook scripts are missing. Re-install the package:\n"
            "  pip install -e .",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)

    # ── Step 2: compute the diff ──────────────────────────────────────────
    typer.echo(f"\n[2/3] Settings file: {settings_file}")
    current = _load_settings()
    desired_mcp = _desired_mcp_entry()
    desired_hooks = _desired_hooks(hooks_dir)

    changes: list[str] = []

    # MCP server
    existing_mcp = current.get("mcpServers", {}).get("patrick-memory")
    if existing_mcp == desired_mcp:
        typer.secho("  ✓ MCP server entry already correct", fg=typer.colors.GREEN)
    else:
        changes.append("mcpServers.patrick-memory")
        label = "update" if existing_mcp else "add"
        typer.secho(f"  ~ Will {label} mcpServers.patrick-memory:", fg=typer.colors.YELLOW)
        typer.echo(f"    {json.dumps(desired_mcp)}")

    # Hooks
    current_hooks = current.get("hooks", {})
    hook_map = {
        "SessionStart": hooks_dir / "session_start.py",
        "UserPromptSubmit": hooks_dir / "prompt_submit.py",
        "PostToolUse": hooks_dir / "post_tool_use.py",
        "Stop": hooks_dir / "stop.py",
    }

    for event, script in hook_map.items():
        existing_event = current_hooks.get(event)
        desired_event = desired_hooks[event]
        if existing_event == desired_event:
            typer.secho(f"  ✓ hooks.{event} already correct", fg=typer.colors.GREEN)
        else:
            changes.append(f"hooks.{event}")
            label = "update" if existing_event else "add"
            typer.secho(f"  ~ Will {label} hooks.{event}:", fg=typer.colors.YELLOW)
            typer.echo(f"    command: python3 {script}")

    # ── Step 3: apply (or not) ────────────────────────────────────────────
    typer.echo(f"\n[3/3] Apply changes")

    if not changes:
        typer.secho("  Nothing to do — settings.json is already up to date.", fg=typer.colors.GREEN)
        typer.echo("\nNext steps:")
        typer.echo("  patrick start    # run the memory server")
        return

    if dry_run:
        typer.secho(
            f"  Dry run — {len(changes)} change(s) would be applied. Re-run without --dry-run to apply.",
            fg=typer.colors.YELLOW,
        )
        _print_manual_snippet(hooks_dir)
        return

    if not auto:
        typer.echo(f"\n  {len(changes)} change(s) to make to {settings_file}:")
        for c in changes:
            typer.echo(f"    • {c}")
        confirmed = typer.confirm("\n  Apply now?")
        if not confirmed:
            typer.echo("\n  Skipped. You can apply manually — snippet below:")
            _print_manual_snippet(hooks_dir)
            return

    # Apply
    updated = json.loads(json.dumps(current))  # deep copy via json round-trip
    updated.setdefault("mcpServers", {})["patrick-memory"] = desired_mcp
    updated.setdefault("hooks", {}).update(desired_hooks)

    _save_settings(updated)
    typer.secho(f"\n  ✓ Written to {settings_file}", fg=typer.colors.GREEN)

    typer.echo("\nNext steps:")
    typer.echo("  patrick start    # run the memory server")
    typer.echo("  Restart Claude Code for hooks to take effect.")


def _print_manual_snippet(hooks_dir: Path) -> None:
    """Print the JSON snippet to add manually."""
    typer.echo("\n--- Add this to ~/.claude/settings.json ---")
    snippet = {
        "mcpServers": {
            "patrick-memory": _desired_mcp_entry()
        },
        "hooks": _desired_hooks(hooks_dir),
    }
    typer.echo(json.dumps(snippet, indent=2))
    typer.echo("-------------------------------------------")


@app.command()
def doctor() -> None:
    """Check Patrick health: server, hooks, MCP config, model cache."""
    import urllib.request
    import urllib.error

    hooks_dir = _hooks_dir()
    settings_file = _settings_path()
    ok = True

    typer.secho("\nPatrick doctor", fg=typer.colors.BRIGHT_CYAN, bold=True)
    typer.echo("=" * 50)

    # ── 1. Server reachable? ──────────────────────────────────────────────
    typer.echo("\n[Server]")
    server_up = False
    try:
        from .config import HOST, PORT
        url = f"http://{HOST}:{PORT}/sse"
        req = urllib.request.Request(url, method="HEAD")
        # SSE endpoint may return 200 or 405 — either means server is up
        try:
            urllib.request.urlopen(req, timeout=2)
            server_up = True
        except urllib.error.HTTPError as e:
            server_up = e.code in (200, 405, 400)
        except urllib.error.URLError:
            server_up = False
    except Exception:
        server_up = False

    if server_up:
        typer.secho(f"  ✓ Server is running at http://{HOST}:{PORT}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"  ✗ Server not reachable — run: patrick start", fg=typer.colors.RED)
        ok = False

    # ── 2. Hook scripts present? ──────────────────────────────────────────
    typer.echo("\n[Hook scripts]")
    expected_hooks = [
        "session_start.py",
        "prompt_submit.py",
        "post_tool_use.py",
        "stop.py",
    ]
    for name in expected_hooks:
        p = hooks_dir / name
        if p.exists():
            typer.secho(f"  ✓ {p}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  ✗ MISSING: {p}", fg=typer.colors.RED)
            ok = False

    # ── 3. settings.json configured? ─────────────────────────────────────
    typer.echo(f"\n[settings.json: {settings_file}]")
    if not settings_file.exists():
        typer.secho("  ✗ File does not exist — run: patrick setup", fg=typer.colors.RED)
        ok = False
    else:
        current = _load_settings()

        # MCP
        mcp_entry = current.get("mcpServers", {}).get("patrick-memory")
        desired_mcp = _desired_mcp_entry()
        if mcp_entry == desired_mcp:
            typer.secho("  ✓ MCP server configured", fg=typer.colors.GREEN)
        elif mcp_entry:
            typer.secho(f"  ~ MCP server present but differs: {mcp_entry}", fg=typer.colors.YELLOW)
        else:
            typer.secho("  ✗ MCP server not configured — run: patrick setup", fg=typer.colors.RED)
            ok = False

        # Hooks
        current_hooks = current.get("hooks", {})
        desired_hooks = _desired_hooks(hooks_dir)
        for event in ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"]:
            if current_hooks.get(event) == desired_hooks[event]:
                typer.secho(f"  ✓ hooks.{event}", fg=typer.colors.GREEN)
            elif event in current_hooks:
                typer.secho(f"  ~ hooks.{event} present but differs (run: patrick setup --auto)", fg=typer.colors.YELLOW)
            else:
                typer.secho(f"  ✗ hooks.{event} missing — run: patrick setup", fg=typer.colors.RED)
                ok = False

    # ── 4. Embedding model cached? ────────────────────────────────────────
    typer.echo("\n[Embedding model]")
    try:
        from .config import EMBEDDING_MODEL_FASTEMBED
        cache = Path.home() / ".cache" / "fastembed"
        # fastembed stores models in subdirs named after the model slug
        model_slug = EMBEDDING_MODEL_FASTEMBED.replace("/", "_")
        model_dirs = list(cache.glob(f"*{model_slug.split('_')[-1]}*")) if cache.exists() else []
        if model_dirs:
            typer.secho(f"  ✓ Model cached: {EMBEDDING_MODEL_FASTEMBED}", fg=typer.colors.GREEN)
        else:
            typer.secho(
                f"  ~ Model not found in cache — run: patrick init",
                fg=typer.colors.YELLOW,
            )
            # Not a hard failure — server will download on first start
    except Exception as e:
        typer.secho(f"  ? Could not check model cache: {e}", fg=typer.colors.YELLOW)

    # ── Summary ───────────────────────────────────────────────────────────
    typer.echo("")
    if ok:
        typer.secho("All checks passed.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho(
            "Some checks failed. Run 'patrick setup' to fix configuration issues.",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(1)


@app.command()
def reindex(
    wipe: bool = typer.Option(
        False,
        "--wipe",
        help="Drop and recreate LanceDB tables before reindexing.",
        is_flag=True,
    ),
) -> None:
    """Re-index all historical transcripts from ~/.claude/projects/.

    With --wipe: drops and recreates the LanceDB tables first (clean slate).
    Reads every transcript JSONL, re-chunks with the current chunking config
    (CHUNK_SIZE=400, CHUNK_OVERLAP=80), re-embeds, and stores all turns.
    """
    import asyncio
    import hashlib
    import json as _json
    import uuid as _uuid
    from datetime import datetime, timezone

    typer.secho("\nPatrick reindex", fg=typer.colors.BRIGHT_CYAN, bold=True)
    typer.echo("=" * 50)

    # ── Init storage + embedding provider ─────────────────────────────────────
    try:
        import lancedb
        import pyarrow as pa
        from .config import DATA_DIR
        from .storage import storage, _SESSION_SCHEMA, _CHUNK_SCHEMA
        from .embedding import provider
    except ImportError as e:
        typer.secho(f"✗ Import error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    # ── Optional wipe: drop and recreate tables ────────────────────────────────
    if wipe:
        typer.echo("\n[1/4] Wiping LanceDB tables...")
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db = lancedb.connect(str(DATA_DIR))
            for table_name in ("session_summaries", "turn_chunks"):
                if table_name in db.table_names():
                    db.drop_table(table_name)
                    typer.secho(f"  ✓ Dropped table: {table_name}", fg=typer.colors.YELLOW)
                db.create_table(table_name, schema=(_SESSION_SCHEMA if table_name == "session_summaries" else _CHUNK_SCHEMA))
                typer.secho(f"  ✓ Recreated table: {table_name}", fg=typer.colors.GREEN)
            # Force the singleton to re-initialize with fresh tables
            storage._initialized = False
            storage._bm25_cache.clear()
        except Exception as e:
            typer.secho(f"✗ Wipe failed: {e}", fg=typer.colors.RED)
            raise typer.Exit(1)
    else:
        typer.echo("\n[1/4] Skipping wipe (use --wipe to drop and recreate tables)")

    # ── Initialize storage and embedding provider ──────────────────────────────
    typer.echo("\n[2/4] Initializing storage and embedding model...")
    try:
        storage.initialize()
        provider.initialize()
        typer.secho("  ✓ Storage and embedding provider ready", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"✗ Initialization failed: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    # ── Discover transcript files ──────────────────────────────────────────────
    typer.echo("\n[3/4] Scanning ~/.claude/projects/ for transcripts...")
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        typer.secho(
            f"  ✗ Directory not found: {projects_dir}", fg=typer.colors.RED
        )
        raise typer.Exit(1)

    MAX_TEXT_CHARS = 8_000

    def _extract_turns_from_transcript(path: Path) -> list[tuple[str, str]]:
        """Parse a transcript JSONL and return [(role, text), ...] pairs.

        Handles both:
        - User turns: entry["message"]["role"] == "user" + content[].text blocks
        - Assistant turns: entry["message"]["role"] == "assistant" + content[].text blocks
        """
        try:
            lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except Exception:
            return []

        seen_ids: set[str] = set()
        turns: list[tuple[str, str]] = []

        for line in lines:
            try:
                entry = _json.loads(line)
            except Exception:
                continue

            msg = entry.get("message", {})
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue

            msg_id = msg.get("id", "")
            if msg_id and msg_id in seen_ids:
                continue

            content = msg.get("content", [])
            if isinstance(content, str):
                # Some user turns store content as a plain string
                text = content.strip()
                if text:
                    if msg_id:
                        seen_ids.add(msg_id)
                    turns.append((role, text[:MAX_TEXT_CHARS]))
                continue

            if not isinstance(content, list):
                continue

            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        parts.append(t)

            if parts:
                if msg_id:
                    seen_ids.add(msg_id)
                turns.append((role, "\n\n".join(parts)[:MAX_TEXT_CHARS]))

        return turns

    # Collect all transcript files
    transcript_files: list[Path] = sorted(projects_dir.rglob("*.jsonl"))
    if not transcript_files:
        typer.secho(
            f"  ~ No .jsonl transcript files found under {projects_dir}",
            fg=typer.colors.YELLOW,
        )
        typer.echo("  Nothing to reindex.")
        return

    typer.echo(f"  Found {len(transcript_files)} transcript file(s)")

    # ── Process each transcript ────────────────────────────────────────────────
    typer.echo("\n[4/4] Chunking, embedding, and storing...")

    total_files = 0
    total_turns = 0
    total_chunks = 0
    skipped_empty = 0

    async def _process_all() -> None:
        nonlocal total_files, total_turns, total_chunks, skipped_empty

        for transcript_path in transcript_files:
            # Derive a stable session_id from the transcript filename stem
            # (Claude stores one session per .jsonl file named by session UUID)
            stem = transcript_path.stem
            # Use the stem directly if it looks like a UUID, else hash it
            try:
                _uuid.UUID(stem)
                session_id = stem
            except ValueError:
                session_id = str(_uuid.UUID(
                    hashlib.md5(str(transcript_path).encode()).hexdigest()
                ))

            turns = _extract_turns_from_transcript(transcript_path)
            if not turns:
                skipped_empty += 1
                continue

            total_files += 1
            file_chunks_written = 0

            for role, text in turns:
                total_turns += 1
                chunks = provider.chunk_text(text)
                vectors = provider.embed_sync(chunks)
                records = storage.make_chunk_records(
                    texts=chunks,
                    vectors=vectors,
                    session_id=session_id,
                    role=role,
                    source="reindex",
                    source_file=str(transcript_path),
                )
                if records:
                    storage.add_chunks(records)
                    file_chunks_written += len(records)
                    total_chunks += len(records)

            # Compute centroid for this session after all its turns are stored
            if file_chunks_written > 0:
                storage.compute_and_upsert_centroid(session_id)

            typer.echo(
                f"  {transcript_path.name}: {len(turns)} turns → {file_chunks_written} chunks"
            )

    asyncio.run(_process_all())

    typer.echo("")
    typer.secho("Reindex complete.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Transcripts processed : {total_files}")
    typer.echo(f"  Transcripts skipped   : {skipped_empty} (empty/unreadable)")
    typer.echo(f"  Turns processed       : {total_turns}")
    typer.echo(f"  Chunks written        : {total_chunks}")


if __name__ == "__main__":
    app()
