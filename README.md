snapper-tools is a set of utilities for [Snapper](http://snapper.io), a rollback tool for Btrfs filesystems, aimed at Arch-like systems with a [flat filesystem layout](https://wiki.archlinux.org/title/Snapper#Suggested_filesystem_layout).

The reason this exists is that Snapper only supports openSUSE's layout, while the flat layout recommended by Arch (and used by most Arch-based systems) only lets you properly rollback via a live USB.

As for deleting snapshots, Snapper often fails to delete the actual folder contents within your snapper directory (e.g. `/.snapshots`), leaving at least one stale folder (e.g. `/.snapshots/418`) despite `snapper list` showing zero snapshots. This matters because your "first" snapshot will now be no. 419 instead of 1.

There's more this tool will do as I go.

[this](https://github.com/jrabinow/snapper-rollback) inspired me but i think it's dead