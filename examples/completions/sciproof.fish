# Standalone Fish completion for SciProof; no optional Python package is needed.
# Source this file, or copy it to ~/.config/fish/completions/sciproof.fish.
# Keep the command/option definitions in sync with src/sciproof/cli.py.

# Number of values consumed by a completed option token.
function __fish_sciproof_arity
    switch $argv[1]
        case --claim --tolerance
            echo 2
        case --value
            echo 3
        case --allowed-signers --atol --exclude --format --input --inputs --key --max-log-bytes \
            --name --note --output --output-numeric --outputs --pass-env --policy --record-env \
            --rtol --runs --save-json --seeds --threads --timeout -o
            echo 1
        case "*"
            echo 0
    end
end

# State: subcommand, values still pending, option arity, option name,
# positional count, recorded command's token index, and end-of-options flag.
function __fish_sciproof_state
    set -l words (commandline -opc)
    set -l command __root__
    set -l pending 0
    set -l arity 0
    set -l active -
    set -l positional 0
    set -l delegated 0
    set -l ended 0
    set -l index 0
    for word in $words
        set index (math $index + 1)
        test $index -eq 1; and continue
        if test $pending -gt 0
            set pending (math $pending - 1)
            continue
        end
        if test "$command" = __root__
            string match -q -- '-*' $word; and continue
            set command $word
            continue
        end
        if test "$word" = --; and test $ended -eq 0
            if contains -- $command capture record suggest-inputs
                set delegated (math $index + 1)
                break
            end
            set ended 1
            continue
        end
        if test $ended -eq 0; and string match -q -- '-*' $word
            set -l parts (string split -m 1 = -- $word)
            set active $parts[1]
            set arity (__fish_sciproof_arity $active)
            set pending $arity
            if test (count $parts) -eq 2; and test $pending -gt 0
                set pending (math $pending - 1)
            end
            continue
        end
        if contains -- $command capture record suggest-inputs
            set delegated $index
            break
        end
        set positional (math $positional + 1)
    end
    printf '%s\n' $command $pending $arity $active $positional $delegated $ended
end

function __fish_sciproof_using_command
    set -l state (__fish_sciproof_state)
    test "$state[1]" = "$argv[1]"; or return 1
    test $state[6] -eq 0; and test $state[7] -eq 0; or return 1
    # Fish handles the first argument with -r; suppress options inside extra arguments.
    test $state[2] -eq 0; or test $state[2] -eq $state[3]
end

function __fish_sciproof_positional
    set -l state (__fish_sciproof_state)
    test "$state[1]" = "$argv[1]"; and test $state[2] -eq 0
    and test $state[6] -eq 0; and test $state[5] -eq $argv[2]
end

function __fish_sciproof_value_output
    set -l state (__fish_sciproof_state)
    test "$state[1]" = export; and test "$state[4]" = --value
    and test $state[2] -eq 2; and test $state[6] -eq 0
end

function __fish_sciproof_complete_command
    set -l state (__fish_sciproof_state)
    contains -- $state[1] capture record suggest-inputs; or return
    set -l index $state[6]
    if test $index -eq 0
        test $state[2] -eq 0; or return
        string match -q -- '-*' (commandline -ct); and return
        set index (math (count (commandline -opc)) + 1)
    end
    set -l words (commandline -opc)
    set -l rest $words[$index..]
    set -l line (string join ' ' -- (string escape -- $rest))
    if test (count $rest) -gt 0
        set line "$line "
    end
    complete -C "$line"(commandline -ct)
end

complete -c sciproof -f
complete -c sciproof -a '(__fish_sciproof_complete_command)'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a capture -d 'run a command once and record evidence'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a verify -d 'check a manifest (integrity by default, re-execution only with --rerun)'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a suggest-inputs -d 'run a Python command once and list files it read that are not declared'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a sign -d 'sign a manifest with an OpenSSH private key (writes <manifest>.sig)'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a badge -d 'verify a manifest and write a static SVG badge with the result'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a record -d 'capture an experiment without declaring files: inputs and outputs are detected'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a update -d 'capture the recorded experiment again (re-baseline) and show what changed'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a calibrate -d 'run the recorded command several times and measure how much its outputs vary'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a diff -d 'compare two manifests: what changed between two recorded runs? (executes nothing)'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a list -d 'list the manifests in a directory; with --check, verify all of them (no rerun)'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a export -d 'write a manifest in another format (in-toto Statement v1, OpenLineage events)'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a pipeline -d 'verify multi-step pipeline DAG and chain of custody'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a check-policy -d 'validate a policy file and list its rules (nothing is verified)'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -a inspect -d 'show a readable summary; never executes anything'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -l version -d 'show program\'s version number and exit'
complete -c sciproof -n '__fish_sciproof_using_command __root__' -s q -l quiet -d 'minimal terminal output: suppress banners and decorative headers'
complete -c sciproof -n '__fish_sciproof_using_command capture' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command capture' -l name -d 'experiment name' -x
complete -c sciproof -n '__fish_sciproof_using_command capture' -l input -l inputs -d 'declare an input file or directory' -r -F
complete -c sciproof -n '__fish_sciproof_using_command capture' -l output -l outputs -d 'declare an output file or directory compared by exact hash' -r -F
complete -c sciproof -n '__fish_sciproof_using_command capture' -l output-numeric -d 'declare a .json/.csv/.tsv output compared with a numeric tolerance' -r -F
complete -c sciproof -n '__fish_sciproof_using_command capture' -l rtol -d 'relative tolerance for numeric outputs' -x
complete -c sciproof -n '__fish_sciproof_using_command capture' -l atol -d 'absolute tolerance for numeric outputs' -x
complete -c sciproof -n '__fish_sciproof_using_command capture' -l tolerance -d tolerance -r -F
complete -c sciproof -n '__fish_sciproof_using_command capture' -l timeout -d 'stop the command after this many seconds' -x
complete -c sciproof -n '__fish_sciproof_using_command capture' -l max-log-bytes -d 'keep at most this many bytes of stdout and of stderr in the manifest' -x
complete -c sciproof -n '__fish_sciproof_using_command capture' -l claim -d claim -r -F
complete -c sciproof -n '__fish_sciproof_using_command capture' -l note -d 'free text stored in the manifest' -x
complete -c sciproof -n '__fish_sciproof_using_command capture' -l record-hardware -d 'also record the CPU count, processor and NVIDIA GPUs'
complete -c sciproof -n '__fish_sciproof_using_command capture' -l record-env -d 'also record this environment variable' -x
complete -c sciproof -n '__fish_sciproof_using_command capture' -l force -d 'overwrite an existing manifest'
complete -c sciproof -n '__fish_sciproof_using_command capture' -l allow-nonzero -d 'exit with status 0 even if the command failed'
complete -c sciproof -n '__fish_sciproof_using_command verify' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command verify' -l rerun -d 'EXECUTE the recorded command again and compare results'
complete -c sciproof -n '__fish_sciproof_using_command verify' -l yes -d 'do not ask for confirmation before --rerun'
complete -c sciproof -n '__fish_sciproof_using_command verify' -l strict-env -d 'with --rerun: report INCONCLUSIVE if the environment differs'
complete -c sciproof -n '__fish_sciproof_using_command verify' -l clean-env -d 'with --rerun: give the command only a minimal environment'
complete -c sciproof -n '__fish_sciproof_using_command verify' -l pass-env -d 'with --clean-env: also pass this environment variable' -x
complete -c sciproof -n '__fish_sciproof_using_command verify' -l allowed-signers -d 'allowed signers' -r -F
complete -c sciproof -n '__fish_sciproof_using_command verify' -l timeout -d 'stop the rerun after this many seconds' -x
complete -c sciproof -n '__fish_sciproof_using_command verify' -l json -d 'print the report as one JSON object'
complete -c sciproof -n '__fish_sciproof_using_command verify' -l format -d 'report format: text' -x -a 'text json markdown junit sarif html'
complete -c sciproof -n '__fish_sciproof_using_command verify' -l policy -d 'also check the rules of this policy file' -r -F
complete -c sciproof -n '__fish_sciproof_using_command verify' -l save-json -d 'also write the JSON report to this file' -r -F
complete -c sciproof -n '__fish_sciproof_using_command verify' -s q -l quiet -d 'minimal terminal output: suppress banners and decorative headers'
complete -c sciproof -n '__fish_sciproof_positional verify 0' -F
complete -c sciproof -n '__fish_sciproof_using_command suggest-inputs' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command suggest-inputs' -l input -d input -r -F
complete -c sciproof -n '__fish_sciproof_using_command suggest-inputs' -l output -d output -r -F
complete -c sciproof -n '__fish_sciproof_using_command suggest-inputs' -l timeout -d timeout -x
complete -c sciproof -n '__fish_sciproof_using_command sign' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command sign' -l key -d 'private key file' -r -F
complete -c sciproof -n '__fish_sciproof_positional sign 0' -F
complete -c sciproof -n '__fish_sciproof_using_command badge' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command badge' -s o -l output -d output -r -F
complete -c sciproof -n '__fish_sciproof_using_command badge' -l rerun -d 'EXECUTE the recorded command again'
complete -c sciproof -n '__fish_sciproof_using_command badge' -l yes -d 'do not ask for confirmation'
complete -c sciproof -n '__fish_sciproof_using_command badge' -l clean-env -d 'with --rerun: minimal environment'
complete -c sciproof -n '__fish_sciproof_using_command badge' -l pass-env -d 'with --clean-env: also pass this environment variable' -x
complete -c sciproof -n '__fish_sciproof_using_command badge' -l strict-env -d 'fail or report inconclusive if the environment differs on rerun'
complete -c sciproof -n '__fish_sciproof_using_command badge' -l allowed-signers -d 'allowed signers' -r -F
complete -c sciproof -n '__fish_sciproof_using_command badge' -l policy -d 'also check the rules of this policy file' -r -F
complete -c sciproof -n '__fish_sciproof_using_command badge' -l timeout -d timeout -x
complete -c sciproof -n '__fish_sciproof_positional badge 0' -F
complete -c sciproof -n '__fish_sciproof_using_command record' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command record' -l name -d 'experiment name' -x
complete -c sciproof -n '__fish_sciproof_using_command record' -l input -l inputs -d 'an input to add by hand' -r -F
complete -c sciproof -n '__fish_sciproof_using_command record' -l exclude -d 'a file or directory that is neither input nor output' -r -F
complete -c sciproof -n '__fish_sciproof_using_command record' -l fast -d 'skip the second run'
complete -c sciproof -n '__fish_sciproof_using_command record' -l timeout -d 'per run' -x
complete -c sciproof -n '__fish_sciproof_using_command record' -l max-log-bytes -d 'max log bytes' -x
complete -c sciproof -n '__fish_sciproof_using_command record' -l note -d 'free text stored in the manifest' -x
complete -c sciproof -n '__fish_sciproof_using_command record' -l record-hardware -d 'record hardware'
complete -c sciproof -n '__fish_sciproof_using_command record' -l record-env -d 'also record this environment variable' -x
complete -c sciproof -n '__fish_sciproof_using_command record' -l force -d 'overwrite an existing manifest'
complete -c sciproof -n '__fish_sciproof_using_command record' -s q -l quiet -d 'minimal terminal output: suppress banners and decorative headers'
complete -c sciproof -n '__fish_sciproof_using_command update' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command update' -l yes -d 'do not ask for confirmation'
complete -c sciproof -n '__fish_sciproof_using_command update' -l dry-run -d 'show what would happen'
complete -c sciproof -n '__fish_sciproof_using_command update' -l note -d 'replace the note of the manifest' -x
complete -c sciproof -n '__fish_sciproof_using_command update' -l timeout -d timeout -x
complete -c sciproof -n '__fish_sciproof_positional update 0' -F
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -l runs -d '2 to 50' -x
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -l seeds -d 'comma-separated random seeds for Monte Carlo sampling' -x
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -l threads -d 'comma-separated thread counts for concurrency drift profiling' -x
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -l yes -d 'do not ask for confirmation'
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -l timeout -d 'per run' -x
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -l format -d 'report format' -x -a 'text json'
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -l json -d 'same as --format json'
complete -c sciproof -n '__fish_sciproof_using_command calibrate' -s q -l quiet -d 'minimal terminal output: suppress banners and decorative headers'
complete -c sciproof -n '__fish_sciproof_positional calibrate 0' -F
complete -c sciproof -n '__fish_sciproof_using_command diff' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command diff' -l json -d 'same as --format json'
complete -c sciproof -n '__fish_sciproof_using_command diff' -l format -d format -x -a 'text json markdown'
complete -c sciproof -n '__fish_sciproof_using_command diff' -s q -l quiet -d 'minimal terminal output: suppress banners and decorative headers'
complete -c sciproof -n '__fish_sciproof_positional diff 0' -F
complete -c sciproof -n '__fish_sciproof_positional diff 1' -F
complete -c sciproof -n '__fish_sciproof_using_command list' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command list' -s r -l recursive -d 'also look in subdirectories'
complete -c sciproof -n '__fish_sciproof_using_command list' -l check -d 'verify every manifest'
complete -c sciproof -n '__fish_sciproof_using_command list' -l json -d 'same as --format json'
complete -c sciproof -n '__fish_sciproof_using_command list' -l format -d format -x -a 'text json markdown junit'
complete -c sciproof -n '__fish_sciproof_positional list 0' -a '(__fish_complete_directories)'
complete -c sciproof -n '__fish_sciproof_using_command export' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command export' -l format -d 'in-toto: an unsigned Statement v1 with hashes' -x -a 'in-toto dsse openlineage latex typst ro-crate'
complete -c sciproof -n '__fish_sciproof_using_command export' -l value -d value -x
complete -c sciproof -n '__fish_sciproof_using_command export' -s o -l output -d 'write here' -r -F
complete -c sciproof -n '__fish_sciproof_positional export 0' -F
complete -c sciproof -n '__fish_sciproof_using_command pipeline' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_using_command pipeline' -s q -l quiet -d 'minimal terminal output: suppress banners and step details'
complete -c sciproof -n '__fish_sciproof_positional pipeline 0' -a verify
complete -c sciproof -n '__fish_sciproof_positional pipeline 1' -F
complete -c sciproof -n '__fish_sciproof_using_command check-policy' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_positional check-policy 0' -F
complete -c sciproof -n '__fish_sciproof_using_command inspect' -s h -l help -d 'show this help message and exit'
complete -c sciproof -n '__fish_sciproof_positional inspect 0' -F
complete -c sciproof -n __fish_sciproof_value_output -F
