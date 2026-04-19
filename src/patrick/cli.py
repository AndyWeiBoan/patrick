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


if __name__ == "__main__":
    app()
