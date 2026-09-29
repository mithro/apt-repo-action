#!/bin/sh
# Where apt took go-tmux-saver from (the bundled local source), and that the
# bundled build runs on this suite's system.
set -eu
apt-cache policy go-tmux-saver
dpkg -s go-tmux-saver | grep -E '^(Version|Architecture):'
apt-repo-selftest-bundle --version
