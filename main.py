# main.py
# CLI entry point for the Oracle RAG Agent.

from __future__ import annotations

import subprocess
import sys

from agent import run_agent, DML_INTENTS
from database import test_connection, get_schema_text, invalidate_schema_cache
from logger import get_logger

logger = get_logger("main")

DIVIDER = "─" * 60


def _confirm_dml(intent: str, sql: str) -> bool:
    """Show generated SQL and ask user to confirm before executing DML."""
    print(f"\n⚠️  {intent} operation detected.")
    print(f"\n{DIVIDER}")
    print("Generated SQL:")
    print(f"  {sql}")
    print(DIVIDER)
    answer = input("Execute this operation? [yes/no]: ").strip().lower()
    approved = answer in ("yes", "y")
    logger.info(f"User {'approved' if approved else 'rejected'} {intent} operation")
    return approved


def _open_chart(path) -> None:
    """Open saved chart PNG with the system default viewer."""
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["start", "", str(path)], shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        logger.warning(f"Could not open chart automatically: {e}")


def _print_banner() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          Oracle 19c  —  RAG SQL Agent  (CLI)             ║")
    print("║  Commands: 'exit' · 'quit' · 'schema' · 'refresh'        ║")
    print("║  Intents : SELECT · INSERT · UPDATE · DELETE             ║")
    print("║            CHART · CONVERSATIONAL                        ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def main() -> None:
    from config import config

    logger.info("Oracle RAG Agent starting")
    _print_banner()

    if not test_connection():
        print("❌  Could not connect to Oracle DB. Check your .env settings.")
        sys.exit(1)

    print(f"✅  Connected to Oracle 19c")
    print(f"    DB User      : {config.DB_USER}")
    print(f"    Working Schema: {config.effective_schema_owner}")

    # Warm up schema cache on startup (loads from metadata file or fetches fresh)
    print("🔍  Loading schema metadata...")
    schema = get_schema_text()
    table_count = schema.count("Table:")
    print(f"✅  Schema ready — {table_count} table(s) available\n")

    while True:
        try:
            user_input = input("Ask: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print("\n👋  Goodbye!\n")
                logger.info("Agent stopped by user")
                break

            # Print the full discovered schema
            if user_input.lower() == "schema":
                print(f"\n{DIVIDER}")
                print(get_schema_text())
                print(f"{DIVIDER}\n")
                continue

            # Force a full schema re-fetch (use after adding/altering tables)
            if user_input.lower() == "refresh":
                print("🔄  Refreshing schema metadata from Oracle...")
                invalidate_schema_cache()
                schema = get_schema_text()
                table_count = schema.count("Table:")
                print(f"✅  Schema refreshed — {table_count} table(s)\n")
                continue

            print()
            result = run_agent(user_query=user_input, confirm_callback=_confirm_dml)

            # ── Conversational replies ────────────────────────────────────────
            if result.intent == "CONVERSATIONAL":
                print(f"🤖  {result.response}")
                print(f"\n{DIVIDER}\n")
                continue

            # ── Intent + SQL header ───────────────────────────────────────────
            print(f"[Intent: {result.intent}]", end="")
            if result.row_count:
                print(f"  [{result.row_count} row(s) returned]")
            else:
                print()
            print(f"SQL → {result.sql}")
            print(DIVIDER)

            # ── Natural language response ─────────────────────────────────────
            if result.cancelled:
                print("🚫  Operation cancelled.")
            else:
                print(f"🤖  {result.response}")

            # ── DML row count ─────────────────────────────────────────────────
            if result.intent in DML_INTENTS and result.success and not result.cancelled:
                print(f"\n✅  {result.rows_affected} row(s) affected.")

            # ── Chart ─────────────────────────────────────────────────────────
            if result.chart_path:
                print(f"\n📊  Chart saved: {result.chart_path}")
                _open_chart(result.chart_path)

            # ── Error ─────────────────────────────────────────────────────────
            if result.error:
                print(f"\n⚠️  {result.error}")

            print(f"\n{DIVIDER}\n")

        except KeyboardInterrupt:
            print("\n\n👋  Interrupted. Goodbye!\n")
            logger.warning("Agent interrupted by user (Ctrl+C)")
            break

        except Exception:
            logger.exception("Unexpected error in main loop")
            print("⚠️  An unexpected error occurred. Check the log for details.\n")


if __name__ == "__main__":
    main()
