#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Sourcing eval kit — your own Anthropic key.   cp set_env.example.sh set_env.sh
#  Each teammate uses their OWN key. set_env.sh is gitignored — never commit it.
# ─────────────────────────────────────────────────────────────────────────────
export ANTHROPIC_API_KEY=""

# Judge model — default is claude-sonnet-5. Uncomment to use Opus 5 instead:
# export ACCURACY_MODEL="claude-opus-5"
