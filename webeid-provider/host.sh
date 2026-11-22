#!/bin/sh
# Entry point Firefox starts for the RefineID Web eID native messaging host.
set -eu
exec python3 "$(dirname "$0")/host.py"
