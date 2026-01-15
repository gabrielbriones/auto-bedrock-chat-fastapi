#!/usr/bin/env python3
"""
Backup script for knowledge base SQLite database.
Run manually or via cron for automated backups.
"""

import shutil
import argparse
from datetime import datetime
from pathlib import Path


def backup_database(
    source_db: str = "knowledge_base.db",
    backup_dir: str = "./backups",
    keep_last_n: int = 7
) -> None:
    """
    Create a timestamped backup of the knowledge base database.

    Args:
        source_db: Path to source database file
        backup_dir: Directory to store backups
        keep_last_n: Number of recent backups to keep (older ones deleted)
    """
    # Create backup directory if it doesn't exist
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    # Check if source database exists
    source_path = Path(source_db)
    if not source_path.exists():
        print(f"❌ Source database not found: {source_db}")
        return

    # Create timestamped backup filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_path / f"kb_backup_{timestamp}.db"

    # Perform backup
    try:
        shutil.copy2(source_path, backup_file)
        file_size = backup_file.stat().st_size / (1024 * 1024)  # MB
        print(f"✅ Backup created: {backup_file}")
        print(f"   Size: {file_size:.2f} MB")
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return

    # Clean up old backups
    try:
        cleanup_old_backups(backup_path, keep_last_n)
    except Exception as e:
        print(f"⚠️  Cleanup warning: {e}")


def cleanup_old_backups(backup_dir: Path, keep_last_n: int) -> None:
    """Remove old backup files, keeping only the most recent N backups."""
    # Find all backup files
    backup_files = sorted(
        backup_dir.glob("kb_backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    # Delete older backups
    for old_backup in backup_files[keep_last_n:]:
        old_backup.unlink()
        print(f"🗑️  Deleted old backup: {old_backup.name}")

    if len(backup_files) > keep_last_n:
        print(f"   Kept {keep_last_n} most recent backups")


def main():
    parser = argparse.ArgumentParser(
        description="Backup knowledge base SQLite database"
    )
    parser.add_argument(
        "--source",
        default="knowledge_base.db",
        help="Source database file path (default: knowledge_base.db)"
    )
    parser.add_argument(
        "--backup-dir",
        default="./backups",
        help="Backup directory (default: ./backups)"
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=7,
        help="Number of recent backups to keep (default: 7)"
    )

    args = parser.parse_args()

    print("🗄️  Knowledge Base Backup")
    print("=" * 50)
    backup_database(args.source, args.backup_dir, args.keep)
    print("=" * 50)


if __name__ == "__main__":
    main()
