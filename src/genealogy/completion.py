"""Zero-dependency shell tab-completion script generator for the `genealogy` CLI."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field


@dataclass
class _ParserSpec:
    flags: list[str] = field(default_factory=list)
    flag_choices: dict[str, list[str]] = field(default_factory=dict)
    subcommands: dict[str, _ParserSpec] = field(default_factory=dict)


def _extract_spec(parser: argparse.ArgumentParser) -> _ParserSpec:
    spec = _ParserSpec()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action._name_parser_map.items():
                spec.subcommands[name] = _extract_spec(subparser)
            continue
        if action.option_strings:
            for opt in action.option_strings:
                if opt not in spec.flags:
                    spec.flags.append(opt)
                if action.choices:
                    spec.flag_choices[opt] = [str(c) for c in action.choices]
    return spec


def _bash_case_body(spec: _ParserSpec, depth: int = 1) -> list[str]:
    lines: list[str] = []
    sub_names = " ".join(sorted(spec.subcommands.keys()))
    flags = " ".join(spec.flags)

    if spec.subcommands:
        lines.append(f'    if [[ $cword -eq {depth} ]]; then')
        lines.append('        if [[ "$cur" == -* ]]; then')
        lines.append(f'            COMPREPLY=( $(compgen -W "{flags}" -- "$cur") )')
        lines.append("        else")
        lines.append(f'            COMPREPLY=( $(compgen -W "{sub_names}" -- "$cur") )')
        lines.append("        fi")
        lines.append("        return 0")
        lines.append("    fi")
        lines.append(f'    case "${{words[{depth}]}}" in')
        # Group identical subparser specs (aliases) into one case arm
        grouped: dict[int, tuple[list[str], _ParserSpec]] = {}
        for name, sub in sorted(spec.subcommands.items()):
            sig = (tuple(sub.flags), tuple(sorted((k, tuple(v)) for k, v in sub.flag_choices.items())), tuple(sorted(sub.subcommands.keys())))
            h = hash(sig)
            if h not in grouped:
                grouped[h] = ([], sub)
            grouped[h][0].append(name)
        for names, sub in grouped.values():
            pattern = "|".join(names)
            lines.append(f"        {pattern})")
            if sub.subcommands:
                nested = _bash_case_body(sub, depth=depth + 1)
                for nline in nested:
                    lines.append(f"        {nline}")
            else:
                if sub.flag_choices:
                    lines.append('            case "$prev" in')
                    for opt, choices in sorted(sub.flag_choices.items()):
                        c_str = " ".join(choices)
                        lines.append(f"                {opt})")
                        lines.append(f'                    COMPREPLY=( $(compgen -W "{c_str}" -- "$cur") )')
                        lines.append("                    return 0")
                        lines.append("                    ;;")
                    lines.append("            esac")
                lines.append('            if [[ "$cur" == -* ]]; then')
                sub_flags = " ".join(sub.flags)
                lines.append(f'                COMPREPLY=( $(compgen -W "{sub_flags}" -- "$cur") )')
                lines.append("                return 0")
                lines.append("            fi")
            lines.append("            ;;")
        lines.append("    esac")
    else:
        if spec.flag_choices:
            lines.append('    case "$prev" in')
            for opt, choices in sorted(spec.flag_choices.items()):
                c_str = " ".join(choices)
                lines.append(f"        {opt})")
                lines.append(f'            COMPREPLY=( $(compgen -W "{c_str}" -- "$cur") )')
                lines.append("            return 0")
                lines.append("            ;;")
            lines.append("    esac")
        lines.append('    if [[ "$cur" == -* ]]; then')
        lines.append(f'        COMPREPLY=( $(compgen -W "{flags}" -- "$cur") )')
        lines.append("        return 0")
        lines.append("    fi")
    return lines


def generate_bash_completion(parser: argparse.ArgumentParser) -> str:
    spec = _extract_spec(parser)
    body = "\n".join(_bash_case_body(spec, depth=1))
    return f"""# bash completion for genealogy
# Enable with: eval "$(genealogy completion bash)"
_genealogy_completion() {{
    local cur prev words cword
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev=""
    if [[ $COMP_CWORD -gt 0 ]]; then
        prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    fi
    words=("${{COMP_WORDS[@]}}")
    cword=$COMP_CWORD

{body}
    return 0
}}
complete -o default -F _genealogy_completion genealogy
"""


def generate_zsh_completion(parser: argparse.ArgumentParser) -> str:
    spec = _extract_spec(parser)
    sub_list = " ".join(f"'{name}'" for name in sorted(spec.subcommands.keys()))
    top_flags = " ".join(f"'{opt}'" for opt in spec.flags)

    case_arms: list[str] = []
    for name, sub in sorted(spec.subcommands.items()):
        if sub.subcommands:
            nested_subs = " ".join(f"'{n}'" for n in sorted(sub.subcommands.keys()))
            nested_arms: list[str] = []
            for n_name, n_sub in sorted(sub.subcommands.items()):
                n_flags = " ".join(f"'{opt}'" for opt in n_sub.flags)
                nested_arms.append(
                    f"                {n_name})\n"
                    f"                    local -a nflags=({n_flags})\n"
                    f'                    if [[ "$PREFIX" == -* ]]; then\n'
                    f"                        _describe 'option' nflags\n"
                    f"                    else\n"
                    f"                        _files\n"
                    f"                    fi\n"
                    f"                    ;;"
                )
            nested_str = "\n".join(nested_arms)
            case_arms.append(
                f"        {name})\n"
                f"            if (( CURRENT == 3 )); then\n"
                f"                local -a subcmds=({nested_subs})\n"
                f"                _describe 'subcommand' subcmds\n"
                f"            else\n"
                f'                case "$words[3]" in\n'
                f"{nested_str}\n"
                f"                esac\n"
                f"            fi\n"
                f"            ;;"
            )
        else:
            s_flags = " ".join(f"'{opt}'" for opt in sub.flags)
            choice_cases: list[str] = []
            for opt, choices in sorted(sub.flag_choices.items()):
                c_str = " ".join(f"'{c}'" for c in choices)
                choice_cases.append(
                    f"                {opt}) compadd -- {c_str}; return 0 ;;"
                )
            choice_block = ""
            if choice_cases:
                joined = "\n".join(choice_cases)
                choice_block = (
                    '            case "$words[CURRENT-1]" in\n'
                    f"{joined}\n"
                    "            esac\n"
                )
            case_arms.append(
                f"        {name})\n"
                f"{choice_block}"
                f"            local -a sflags=({s_flags})\n"
                f'            if [[ "$PREFIX" == -* ]]; then\n'
                f"                _describe 'option' sflags\n"
                f"            else\n"
                f"                _files\n"
                f"            fi\n"
                f"            ;;"
            )

    arms_str = "\n".join(case_arms)
    return f"""#compdef genealogy
# zsh completion for genealogy
# Enable with: eval "$(genealogy completion zsh)"
_genealogy() {{
    if (( CURRENT == 2 )); then
        local -a cmds=({sub_list})
        local -a tflags=({top_flags})
        if [[ "$PREFIX" == -* ]]; then
            _describe 'option' tflags
        else
            _describe 'command' cmds
        fi
        return
    fi

    case "$words[2]" in
{arms_str}
    esac
}}
compdef _genealogy genealogy
"""


def generate_fish_completion(parser: argparse.ArgumentParser) -> str:
    spec = _extract_spec(parser)
    lines: list[str] = [
        "# fish completion for genealogy",
        "# Enable with: genealogy completion fish | source",
    ]
    for opt in spec.flags:
        if opt.startswith("--"):
            lines.append(f"complete -c genealogy -n '__fish_use_subcommand' -l '{opt[2:]}'")
        elif opt.startswith("-") and len(opt) == 2:
            lines.append(f"complete -c genealogy -n '__fish_use_subcommand' -s '{opt[1:]}'")

    for name, sub in sorted(spec.subcommands.items()):
        lines.append(f"complete -c genealogy -f -n '__fish_use_subcommand' -a '{name}'")
        if sub.subcommands:
            sub_names = " ".join(sorted(sub.subcommands.keys()))
            for n_name, n_sub in sorted(sub.subcommands.items()):
                lines.append(
                    f"complete -c genealogy -f -n '__fish_seen_subcommand_from {name}; and not __fish_seen_subcommand_from {sub_names}' -a '{n_name}'"
                )
                for opt in n_sub.flags:
                    if opt.startswith("--"):
                        lines.append(
                            f"complete -c genealogy -n '__fish_seen_subcommand_from {name}; and __fish_seen_subcommand_from {n_name}' -l '{opt[2:]}'"
                        )
        else:
            for opt in sub.flags:
                if opt.startswith("--"):
                    choices = sub.flag_choices.get(opt)
                    extra = f" -r -a '{' '.join(choices)}'" if choices else ""
                    lines.append(
                        f"complete -c genealogy -n '__fish_seen_subcommand_from {name}' -l '{opt[2:]}'{extra}"
                    )
                elif opt.startswith("-") and len(opt) == 2:
                    lines.append(
                        f"complete -c genealogy -n '__fish_seen_subcommand_from {name}' -s '{opt[1:]}'"
                    )
    return "\n".join(lines) + "\n"


def generate_completion(parser: argparse.ArgumentParser, shell: str) -> str:
    """Return a shell completion script for bash, zsh, or fish."""
    if shell == "bash":
        return generate_bash_completion(parser)
    if shell == "zsh":
        return generate_zsh_completion(parser)
    if shell == "fish":
        return generate_fish_completion(parser)
    raise ValueError(f"Unsupported shell: {shell!r}")
