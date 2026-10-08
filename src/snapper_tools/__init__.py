import argparse
import btrfsutil
import configparser
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_CONFIG = "/etc/snapper-scripts.conf"
CONFIRM = "CONFIRM"


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="just print the actions")
    common.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=f"config file to use (default: {DEFAULT_CONFIG})",
    )

    parser = argparse.ArgumentParser(
        prog="snapper-scripts",
        description="Utilities for Snapper on Arch-like Btrfs flat layouts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rollback = sub.add_parser(
        "rollback", parents=[common], help="rollback to a snapper snapshot"
    )
    rollback.add_argument("snap_id", metavar="SNAPID", help="ID of snapper snapshot")
    rollback.set_defaults(func=cmd_rollback)

    clean = sub.add_parser(
        "clean", parents=[common], help="remove stale snapshot folders"
    )
    clean.set_defaults(func=cmd_clean)

    delete = sub.add_parser(
        "delete",
        parents=[common],
        help="run 'snapper delete ...' then clean stale folders",
    )
    delete.add_argument(
        "snapper_args",
        nargs=argparse.REMAINDER,
        metavar="...",
        help="arguments passed straight to 'snapper delete'",
    )
    delete.set_defaults(func=cmd_delete)

    return parser


# config


def mountpoint(config):
    return Path(config.get("root", "mountpoint"))


def main_subvol(config):
    return mountpoint(config) / config.get("root", "subvol_main")


def snapshots_dir(config):
    return mountpoint(config) / config.get("root", "subvol_snapshots")


def device(config):
    return config.get("root", "dev", fallback=None)


# shared


def do(dry, text, fn, *args, **kwargs):
    print(f"{'[DRY-RUN] ' if dry else ''}{text}")
    if not dry:
        return fn(*args, **kwargs)


def ensure_dir(path, dry):
    if not path.is_dir():
        do(dry, f"mkdir -p {path}", os.makedirs, path)


def mount_root(config, dry):
    target = mountpoint(config)
    ensure_dir(target, dry)
    if os.path.ismount(target):
        return True

    dev = device(config)
    cmd = ["mount", "-o", "subvolid=5"]
    if dev:
        cmd.append(dev)
    cmd.append(str(target))

    try:
        do(dry, " ".join(cmd), subprocess.run, cmd, check=True)
    except subprocess.CalledProcessError:
        print(f"unable to mount {target}")
        return False
    return True


# rollback


def confirm():
    try:
        answer = input(
            f"Are you SURE you want to rollback? Type '{CONFIRM}' to continue: "
        )
    except KeyboardInterrupt:
        return False
    return answer == CONFIRM


def restore_main(subvol, old, dry):
    if subvol.exists() or not old.exists():
        return
    do(dry, f"mv {old} {subvol}", os.rename, old, subvol)


def rollback(snap_id, config, dry):
    subvol = main_subvol(config)
    src = snapshots_dir(config) / snap_id / "snapshot"
    old = Path(f"{subvol}{datetime.now():%Y-%m-%dT%H:%M}")

    try:
        do(dry, f"mv {subvol} {old}", os.rename, subvol, old)
        do(
            dry,
            f"btrfs subvolume snapshot {src} {subvol}",
            btrfsutil.create_snapshot,
            src,
            subvol,
        )
        do(
            dry,
            f"btrfs subvolume set-default {subvol}",
            btrfsutil.set_default_subvolume,
            subvol,
        )
    except (FileNotFoundError, btrfsutil.BtrfsUtilError) as e:
        print(f"rollback failed: {e}")
        restore_main(subvol, old, dry)
        return False

    print(f"{'[DRY-RUN] ' if dry else ''}Rollback to {src} complete. Reboot to finish")
    print(f"Previous root kept as {old}. After rebooting, delete it with:")
    print(f"  btrfs subvolume delete {old}")
    return True


def cmd_rollback(args, config):
    if not confirm():
        print("Bad confirmation, exiting...")
        return 1
    if not mount_root(config, args.dry_run):
        return 1
    done = rollback(args.snap_id, config, args.dry_run)
    return 0 if done else 1


# clean


def snapshot_folders(path):
    folders = [p for p in path.iterdir() if p.is_dir() and p.name.isdigit()]
    return sorted(folders, key=lambda p: int(p.name))


def is_empty(folder):
    return not any(folder.iterdir())


def find_stale(path):
    return [f for f in snapshot_folders(path) if is_empty(f)]


def snapper_ids():
    cmd = ["snapper", "list", "--columns", "number"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return {w for w in out.split() if w.isdigit()}


def snapper_delete(num, dry):
    try:
        do(
            dry,
            f"snapper delete {num}",
            subprocess.run,
            ["snapper", "delete", num],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"snapper failed to delete {num}: {e}")


def has_subvol(folder):
    snap = folder / "snapshot"
    return snap.exists() and btrfsutil.is_subvolume(snap)


def delete_subvol(folder, dry):
    snap = folder / "snapshot"
    try:
        do(dry, f"btrfs subvolume delete {snap}", btrfsutil.delete_subvolume, snap)
    except btrfsutil.BtrfsUtilError as e:
        print(f"failed to delete subvolume {snap}: {e}")


def remove_folder(folder, dry):
    try:
        do(dry, f"rmdir {folder}", os.rmdir, folder)
    except OSError as e:
        print(f"failed to remove {folder}: {e}")


def delete(folder, dry):
    if folder.name in snapper_ids():
        snapper_delete(folder.name, dry)
    if has_subvol(folder):
        delete_subvol(folder, dry)
    if folder.exists():
        remove_folder(folder, dry)
    return dry or not folder.exists()


def cmd_clean(args, config):
    if not mount_root(config, args.dry_run):
        return 1

    snaps = snapshots_dir(config)
    if not snaps.is_dir():
        print(f"{snaps} not found, is your btrfs root mounted?")
        return 1

    stale = find_stale(snaps)
    if not stale:
        print("No stale folders found")
        return 0

    print(f"Found {len(stale)} stale folder(s): {', '.join(f.name for f in stale)}")
    failed = [f for f in stale if not delete(f, args.dry_run)]
    for f in failed:
        print(f"still exists: {f}")
    return 1 if failed else 0


# delete


def cmd_delete(args, config):
    snapper_args = args.snapper_args
    if snapper_args and snapper_args[0] == "--":
        snapper_args = snapper_args[1:]
    if not snapper_args:
        print("nothing to delete, pass snapper delete arguments")
        return 1

    ok = True
    try:
        do(
            args.dry_run,
            f"snapper delete {' '.join(snapper_args)}",
            subprocess.run,
            ["snapper", "delete", *snapper_args],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"snapper delete failed: {e}")
        ok = False

    # run clean even if snapper failed, it may have left stale folders behind
    cleaned = cmd_clean(args, config)
    return 0 if ok and cleaned == 0 else 1


def main():
    if os.geteuid() != 0:
        print(
            "Error: snapper-scripts must be run as root.",
            file=sys.stderr,
        )
        return 1

    args = build_parser().parse_args()
    config = configparser.ConfigParser()
    if not config.read(args.config):
        print(f"config not found: {args.config}")
        return 1

    try:
        return args.func(args, config)
    except (OSError, configparser.Error, subprocess.CalledProcessError) as e:
        print(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
