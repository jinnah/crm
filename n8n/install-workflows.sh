#!/bin/sh
# Repeatable per-installation workflow install for the pinned n8n.
#
# Usage (from the repository root, stack running):
#     sh n8n/install-workflows.sh
#
# Behaviour, in order:
#   1. Lists the workflows already present in this n8n installation.
#   2. If any workflow NAME already exists more than once, STOPS with
#      instructions — a previous duplicate must be resolved deliberately in
#      the n8n editor first. Nothing is ever deleted automatically.
#   3. Imports only the workflow files whose names are not present yet, one
#      file at a time, then publishes each newly imported workflow.
#   4. Workflows whose name already exists exactly once are SKIPPED with a
#      note: to update one, delete or rename it deliberately in the n8n
#      editor (the 2.x CLI has no delete command) and re-run this script.
#
# The result is the same versioned workflow set on every installation with
# no silent duplicate active workflows.

set -eu

# Git Bash on Windows rewrites /workflows/... into a host path; disable that.
# Inert everywhere else.
export MSYS_NO_PATHCONV=1

COMPOSE="docker compose"
EXEC="$COMPOSE exec -T n8n"

echo "Reading existing workflows..."
EXISTING=$($EXEC n8n list:workflow 2>/dev/null | grep '|' || true)

# 1) Refuse to continue when duplicates already exist.
DUPES=$(printf '%s\n' "$EXISTING" | cut -d'|' -f2- | sort | uniq -d)
if [ -n "$DUPES" ]; then
    echo ""
    echo "ERROR: duplicate workflows already exist in this n8n installation:"
    printf '%s\n' "$DUPES" | while read -r NAME; do
        echo "  - $NAME"
        printf '%s\n' "$EXISTING" | grep "|$NAME\$" | cut -d'|' -f1 | sed 's/^/      id: /'
    done
    echo ""
    echo "Resolve them deliberately before installing: open the n8n editor and"
    echo "delete or rename the copies you do not want (the 2.x CLI has no"
    echo "delete command, so use the editor), then re-run this script."
    echo "Nothing was imported."
    exit 1
fi

# 2) Stage only the NEW workflow files into a temp directory inside the
#    container, then import them in one --separate pass (the n8n CLI's
#    per-file object format).
IMPORTED_NAMES=""
STAGE_DIR="/tmp/workflow-install"
$EXEC sh -c "rm -rf $STAGE_DIR && mkdir -p $STAGE_DIR"
for FILE in n8n/workflows/*.json; do
    BASENAME=$(basename "$FILE")
    # The workflow name is the first "name" key in each exported file.
    NAME=$(grep -m1 '"name"' "$FILE" | sed 's/.*"name"[^"]*"\([^"]*\)".*/\1/')
    if printf '%s\n' "$EXISTING" | grep -q "|$NAME\$"; then
        echo "SKIP   $BASENAME — '$NAME' already installed. To update it, delete"
        echo "       the existing copy deliberately in the editor and re-run."
        continue
    fi
    echo "IMPORT $BASENAME — '$NAME'"
    $EXEC sh -c "cp /workflows/$BASENAME $STAGE_DIR/"
    IMPORTED_NAMES="$IMPORTED_NAMES$NAME\n"
done

if [ -z "$IMPORTED_NAMES" ]; then
    echo "Nothing new to import."
    exit 0
fi

$EXEC n8n import:workflow --separate --input="$STAGE_DIR" >/dev/null
$EXEC sh -c "rm -rf $STAGE_DIR"

# 3) Publish what was just imported (n8n 2.x publishes per workflow).
echo "Publishing newly imported workflows..."
AFTER=$($EXEC n8n list:workflow 2>/dev/null | grep '|' || true)
printf "$IMPORTED_NAMES" | while read -r NAME; do
    [ -n "$NAME" ] || continue
    ID=$(printf '%s\n' "$AFTER" | grep "|$NAME\$" | head -1 | cut -d'|' -f1)
    if [ -n "$ID" ]; then
        echo "PUBLISH $NAME ($ID)"
        # </dev/null: docker exec -T must not swallow the name list on stdin.
        $EXEC n8n publish:workflow --id="$ID" >/dev/null </dev/null
    fi
done

echo ""
echo "Done. Restart n8n to activate published workflows:"
echo "    docker compose restart n8n"
echo "Reminder: set 'Inbound Error Handler' as the error workflow of each"
echo "sending workflow, and create the SMTP credential named"
echo "'Document Email SMTP' for the Document Email workflow (editor, once"
echo "per installation)."
