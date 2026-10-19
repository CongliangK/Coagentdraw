#!/usr/bin/env bash
# Install this design directory as a skill; never replace an existing entry.
set -euo pipefail

usage() {
    printf 'Usage: bash %s [skills-directory]\n' "$0"
    printf 'Default: $HOME/.agents/skills; only creates the coagentdraw-design symlink.\n'
}

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
skill_root="$(CDPATH= cd -- "$script_dir/.." && pwd -P)"
if [[ ! -f "$skill_root/SKILL.md" ]]; then
    printf 'Missing skill manifest: %s/SKILL.md\n' "$skill_root" >&2
    exit 1
fi

skills_dir="${1:-${HOME:?HOME must be set}/.agents/skills}"
mkdir -p -- "$skills_dir"
skills_dir="$(CDPATH= cd -- "$skills_dir" && pwd -P)"
target="$skills_dir/coagentdraw-design"

if [[ -L "$target" ]]; then
    existing="$(readlink -f -- "$target" 2>/dev/null || true)"
    if [[ "$existing" == "$skill_root" ]]; then
        printf 'Already linked: %s -> %s\n' "$target" "$skill_root"
        exit 0
    fi
fi
if [[ -e "$target" || -L "$target" ]]; then
    printf 'Refusing to replace existing entry: %s\n' "$target" >&2
    printf 'Inspect it and choose another skills directory; no files were replaced.\n' >&2
    exit 1
fi

ln -s -- "$skill_root" "$target"
printf 'Linked: %s -> %s\n' "$target" "$skill_root"
printf 'Skill discovery depends on the host; rescan or open a new session if needed.\n'
