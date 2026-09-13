"""
cleanup_snapshots.py
Standalone script to delete ANPR snapshot images older than a retention
period. Deliberately NOT imported by main.py or run automatically — this
is a maintenance task you run manually or schedule via cron/Task
Scheduler, kept separate so it can never accidentally run mid-demo and
delete something you're about to show a judge.

Usage:
    python cleanup_snapshots.py                 # dry run, shows what WOULD be deleted
    python cleanup_snapshots.py --confirm        # actually deletes
    python cleanup_snapshots.py --days 7         # override retention (default 30)
    python cleanup_snapshots.py --days 7 --confirm

Deletes whole date-folders (media/anpr_snapshots/<camera_id>/<date>/) once
every file in them is past the retention window — matches the storage
layout documented in storage.py, and avoids leaving empty folders behind.
"""
import argparse
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from storage import STORAGE_ROOT

SNAPSHOT_ROOT = STORAGE_ROOT / "anpr_snapshots"


def find_old_date_folders(cutoff_date: date):
    """Yields (camera_folder, date_folder_path, date) for every date-folder
    older than cutoff_date. Folders that aren't valid YYYY-MM-DD are
    skipped, not deleted — better to leave unexpected content alone than
    guess wrong and delete something unrelated."""
    if not SNAPSHOT_ROOT.exists():
        return

    for camera_dir in SNAPSHOT_ROOT.iterdir():
        if not camera_dir.is_dir():
            continue
        for date_dir in camera_dir.iterdir():
            if not date_dir.is_dir():
                continue
            try:
                folder_date = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue  # not a date-shaped folder name - leave it alone
            if folder_date < cutoff_date:
                yield camera_dir.name, date_dir, folder_date


def main():
    parser = argparse.ArgumentParser(description="Delete ANPR snapshots older than the retention window.")
    parser.add_argument("--days", type=int, default=30, help="Retention period in days (default: 30)")
    parser.add_argument("--confirm", action="store_true", help="Actually delete (default is dry-run)")
    args = parser.parse_args()

    cutoff = date.today() - timedelta(days=args.days)
    print(f"Storage root: {STORAGE_ROOT}")
    print(f"Retention: {args.days} days (deleting anything before {cutoff.isoformat()})")
    print(f"Mode: {'DELETING' if args.confirm else 'DRY RUN (pass --confirm to actually delete)'}")
    print()

    total_folders = 0
    total_bytes = 0

    for camera_id, folder_path, folder_date in find_old_date_folders(cutoff):
        folder_bytes = sum(f.stat().st_size for f in folder_path.rglob("*") if f.is_file())
        total_folders += 1
        total_bytes += folder_bytes
        action = "Deleting" if args.confirm else "Would delete"
        print(f"  {action}: {camera_id}/{folder_date.isoformat()}  ({folder_bytes / 1024:.1f} KB)")
        if args.confirm:
            shutil.rmtree(folder_path)

    print()
    print(f"{'Deleted' if args.confirm else 'Would delete'}: {total_folders} date-folders, "
          f"{total_bytes / (1024 * 1024):.2f} MB total")

    if not args.confirm and total_folders > 0:
        print("\nRe-run with --confirm to actually delete these.")


if __name__ == "__main__":
    main()
