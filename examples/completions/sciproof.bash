# Bash tab completion for sciproof.
#
# Completes the subcommands, the options of each subcommand and the fixed values
# of --format. Anything else (manifest paths, files, directories) uses Bash's
# normal file-name completion. It needs no extra package and works without the
# bash-completion project; it also works with the Bash 3.2 that ships with macOS.
#
# Try it in the current shell:
#
#     source examples/completions/sciproof.bash
#
# To load it in every session, add the same line (with the full path to this
# file) to ~/.bashrc.
#
# The lists below mirror the argument parser in src/sciproof/cli.py;
# tests/test_bash_completion.py checks that they stay in sync.

_sciproof_subcommands() {
    echo "capture verify suggest-inputs sign badge record update calibrate diff list export pipeline check-policy inspect"
}

# Options that take no value. An empty first argument means "before the subcommand".
_sciproof_flags() {
    case "$1" in
        "") echo "-h --help --version -q --quiet" ;;
        capture) echo "-h --help --record-hardware --force --allow-nonzero" ;;
        verify) echo "-h --help --rerun --yes --strict-env --clean-env --json -q --quiet" ;;
        suggest-inputs) echo "-h --help" ;;
        sign) echo "-h --help" ;;
        badge) echo "-h --help --rerun --yes --clean-env --strict-env" ;;
        record) echo "-h --help --fast --record-hardware --force -q --quiet" ;;
        update) echo "-h --help --yes --dry-run" ;;
        calibrate) echo "-h --help --yes --json -q --quiet" ;;
        diff) echo "-h --help --json -q --quiet" ;;
        list) echo "-h --help -r --recursive --check --json" ;;
        export) echo "-h --help" ;;
        pipeline) echo "-h --help -q --quiet" ;;
        check-policy | inspect) echo "-h --help" ;;
    esac
}

# Options that take one or more values (see _sciproof_arity for how many).
_sciproof_valued() {
    case "$1" in
        capture) echo "--name --input --inputs --output --outputs --output-numeric --rtol --atol --tolerance --timeout --max-log-bytes --claim --note --record-env" ;;
        verify) echo "--pass-env --allowed-signers --timeout --format --policy --save-json" ;;
        suggest-inputs) echo "--input --output --timeout" ;;
        sign) echo "--key" ;;
        badge) echo "-o --output --pass-env --allowed-signers --policy --timeout" ;;
        record) echo "--name --input --inputs --exclude --timeout --max-log-bytes --note --record-env" ;;
        update) echo "--note --timeout" ;;
        calibrate) echo "--runs --seeds --threads --timeout --format" ;;
        diff) echo "--format" ;;
        list) echo "--format" ;;
        export) echo "--format --value -o --output" ;;
    esac
}

# Number of values an option takes: 1 unless listed here.
_sciproof_arity() {
    case "$1:$2" in
        capture:--tolerance | capture:--claim) echo 2 ;;
        export:--value) echo 3 ;;
        *) echo 1 ;;
    esac
}

# Fixed values of an option.
_sciproof_choices() {
    case "$1:$2" in
        verify:--format) echo "text json markdown junit sarif html" ;;
        calibrate:--format) echo "text json" ;;
        diff:--format) echo "text json markdown" ;;
        list:--format) echo "text json markdown junit" ;;
        export:--format) echo "in-toto dsse openlineage latex typst ro-crate" ;;
    esac
}

_sciproof() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local command="" pending=0 active="" positionals=0 i word opts
    COMPREPLY=()

    # "--format=js": Bash splits at "=", so the word being completed is the part after it.
    if [[ "$cur" == "=" ]]; then
        cur=""
    fi

    for ((i = 1; i < COMP_CWORD; i++)); do
        word="${COMP_WORDS[i]}"
        [[ "$word" == "=" ]] && continue
        if ((pending > 0)); then
            ((pending--))
            ((pending == 0)) && active=""
            continue
        fi
        [[ "$word" == "--" ]] && return 0
        if [[ "$word" == -* ]]; then
            if [[ " $(_sciproof_valued "$command") " == *" $word "* ]]; then
                pending=$(_sciproof_arity "$command" "$word")
                active="$word"
            fi
            continue
        fi
        if [[ -z "$command" ]]; then
            [[ " $(_sciproof_subcommands) " == *" $word "* ]] && command="$word"
            continue
        fi
        ((positionals++))
        # The rest of the line belongs to the command that is being run.
        case "$command" in
            capture | suggest-inputs | record) return 0 ;;
        esac
    done

    if ((pending > 0)); then
        opts="$(_sciproof_choices "$command" "$active")"
        COMPREPLY=($(compgen -W "$opts" -- "$cur"))
        return 0
    fi

    if [[ "$cur" == -* ]]; then
        opts="$(_sciproof_flags "$command") $(_sciproof_valued "$command")"
        COMPREPLY=($(compgen -W "$opts" -- "$cur"))
        return 0
    fi

    if [[ -z "$command" ]]; then
        COMPREPLY=($(compgen -W "$(_sciproof_subcommands)" -- "$cur"))
    elif [[ "$command" == "pipeline" && $positionals -eq 0 ]]; then
        COMPREPLY=($(compgen -W "verify" -- "$cur"))
    fi
    # Manifests, directories and other paths: no results, so -o default completes file names.
    return 0
}

complete -o default -F _sciproof sciproof
