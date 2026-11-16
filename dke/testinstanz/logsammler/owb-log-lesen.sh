#!/bin/bash
# Read command for the log collector (forced command on the openWB VM, installed in T2).
# Evaluates neither arguments nor SSH_ORIGINAL_COMMAND. Reads only: no writes, no network.
# Output: main.log.1 (if present), then main.log, from the openWB ramdisk directory.
# OWB_LOG_VERZEICHNIS exists for tests only; sshd does not pass it through (do not extend AcceptEnv).
verzeichnis="${OWB_LOG_VERZEICHNIS:-/var/www/html/openWB/ramdisk}"
aktuell="${verzeichnis}/main.log"
vorher="${verzeichnis}/main.log.1"
if [ ! -f "$aktuell" ] || [ ! -r "$aktuell" ]; then
	echo "owb-log-lesen: ${aktuell} missing or not readable" >&2
	exit 2
fi
if [ -e "$vorher" ]; then
	exec cat -- "$vorher" "$aktuell"
fi
exec cat -- "$aktuell"
