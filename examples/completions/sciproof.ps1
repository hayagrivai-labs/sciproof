# PowerShell tab completion for sciproof (Windows PowerShell 5.1 and PowerShell 7+).
#
# Completes the subcommands, the options of each subcommand and the fixed values
# of --format. Anything else (manifest paths, files, directories) falls back to
# PowerShell's normal file-name completion.
#
# Try it in the current session:
#
#     . .\examples\completions\sciproof.ps1
#
# To load it in every session, add the same dot-sourcing line (with the full path
# to this file) to your profile: notepad $PROFILE
#
# Windows PowerShell 5.1 does not call completers for a bare "-" or "--"; type the
# first letter of the option (for example "--f") and press Tab.
#
# The tables below mirror the argument parser in src/sciproof/cli.py;
# tests/test_completion.py checks that they stay in sync.

# Subcommand -> tooltip shown by PowerShell.
$script:SciproofSubcommands = [ordered]@{
    'capture'        = 'run a command once and record evidence'
    'verify'         = 'check a manifest (integrity by default, re-execution only with --rerun)'
    'suggest-inputs' = 'run a Python command once and list files it read that are not declared'
    'sign'           = 'sign a manifest with an OpenSSH private key'
    'badge'          = 'verify a manifest and write a static SVG badge with the result'
    'record'         = 'capture an experiment without declaring files'
    'update'         = 'capture the recorded experiment again (re-baseline) and show what changed'
    'calibrate'      = 'run the recorded command several times and measure how much its outputs vary'
    'diff'           = 'compare two manifests'
    'list'           = 'list the manifests in a directory'
    'export'         = 'write a manifest in another format'
    'pipeline'       = 'verify multi-step pipeline DAG and chain of custody'
    'check-policy'   = 'validate a policy file and list its rules'
    'inspect'        = 'show a readable summary'
}

# Options before the subcommand.
$script:SciproofGlobalOptions = @{
    '-h' = 0; '--help' = 0; '--version' = 0; '-q' = 0; '--quiet' = 0
}

# Subcommand -> option -> number of values the option takes (0 = a flag).
# Options of these commands stop at the first positional argument, because the
# rest of the line belongs to the command that is being run.
$script:SciproofCommandsWithCommandLine = @('capture', 'suggest-inputs', 'record')

$script:SciproofOptions = @{
    'capture'        = @{
        '-h' = 0; '--help' = 0; '--name' = 1; '--input' = 1; '--inputs' = 1; '--output' = 1; '--outputs' = 1
        '--output-numeric' = 1; '--rtol' = 1; '--atol' = 1; '--tolerance' = 2; '--timeout' = 1
        '--max-log-bytes' = 1; '--claim' = 2; '--note' = 1; '--record-hardware' = 0; '--record-env' = 1
        '--force' = 0; '--allow-nonzero' = 0
    }
    'verify'         = @{
        '-h' = 0; '--help' = 0; '--rerun' = 0; '--yes' = 0; '--strict-env' = 0; '--clean-env' = 0
        '--pass-env' = 1; '--allowed-signers' = 1; '--timeout' = 1; '--json' = 0; '--format' = 1
        '--policy' = 1; '--save-json' = 1; '-q' = 0; '--quiet' = 0
    }
    'suggest-inputs' = @{ '-h' = 0; '--help' = 0; '--input' = 1; '--output' = 1; '--timeout' = 1 }
    'sign'           = @{ '-h' = 0; '--help' = 0; '--key' = 1 }
    'badge'          = @{
        '-h' = 0; '--help' = 0; '-o' = 1; '--output' = 1; '--rerun' = 0; '--yes' = 0; '--clean-env' = 0
        '--pass-env' = 1; '--strict-env' = 0; '--allowed-signers' = 1; '--policy' = 1; '--timeout' = 1
    }
    'record'         = @{
        '-h' = 0; '--help' = 0; '--name' = 1; '--input' = 1; '--inputs' = 1; '--exclude' = 1; '--fast' = 0
        '--timeout' = 1; '--max-log-bytes' = 1; '--note' = 1; '--record-hardware' = 0; '--record-env' = 1
        '--force' = 0; '-q' = 0; '--quiet' = 0
    }
    'update'         = @{ '-h' = 0; '--help' = 0; '--yes' = 0; '--dry-run' = 0; '--note' = 1; '--timeout' = 1 }
    'calibrate'      = @{
        '-h' = 0; '--help' = 0; '--runs' = 1; '--seeds' = 1; '--threads' = 1; '--yes' = 0; '--timeout' = 1
        '--format' = 1; '--json' = 0; '-q' = 0; '--quiet' = 0
    }
    'diff'           = @{ '-h' = 0; '--help' = 0; '--json' = 0; '--format' = 1; '-q' = 0; '--quiet' = 0 }
    'list'           = @{ '-h' = 0; '--help' = 0; '-r' = 0; '--recursive' = 0; '--check' = 0; '--json' = 0; '--format' = 1 }
    'export'         = @{ '-h' = 0; '--help' = 0; '--format' = 1; '--value' = 3; '-o' = 1; '--output' = 1 }
    'pipeline'       = @{ '-h' = 0; '--help' = 0; '-q' = 0; '--quiet' = 0 }
    'check-policy'   = @{ '-h' = 0; '--help' = 0 }
    'inspect'        = @{ '-h' = 0; '--help' = 0 }
}

# Subcommand -> option -> allowed values.
$script:SciproofChoices = @{
    'verify'    = @{ '--format' = @('text', 'json', 'markdown', 'junit', 'sarif', 'html') }
    'calibrate' = @{ '--format' = @('text', 'json') }
    'diff'      = @{ '--format' = @('text', 'json', 'markdown') }
    'list'      = @{ '--format' = @('text', 'json', 'markdown', 'junit') }
    'export'    = @{ '--format' = @('in-toto', 'dsse', 'openlineage', 'latex', 'typst', 'ro-crate') }
}

# Fixed first positional argument of a subcommand.
$script:SciproofActions = @{ 'pipeline' = @('verify') }

function script:Get-SciproofCompletions {
    param(
        [string[]]$Words,
        [string]$WordToComplete
    )

    $command = $null
    $positionals = 0
    $pending = 0
    $activeOption = $null

    foreach ($word in $Words) {
        if ($pending -gt 0) {
            $pending--
            if ($pending -eq 0) { $activeOption = $null }
            continue
        }
        if ($word -eq '--') { return @() }
        if ($word.StartsWith('-')) {
            $table = if ($command) { $script:SciproofOptions[$command] } else { $script:SciproofGlobalOptions }
            if ($table.ContainsKey($word) -and $table[$word] -gt 0) {
                $pending = $table[$word]
                $activeOption = $word
            }
            continue
        }
        if (-not $command) {
            if ($script:SciproofSubcommands.Contains($word)) { $command = $word }
            continue
        }
        $positionals++
        if ($script:SciproofCommandsWithCommandLine -contains $command) { return @() }
    }

    $result = [System.Collections.Generic.List[object]]::new()
    $add = {
        param($Text, $Type, $Tooltip)
        $result.Add([System.Management.Automation.CompletionResult]::new($Text, $Text, $Type, $Tooltip))
    }

    # "--format=js" style: complete the value part.
    if ($command -and $WordToComplete -match '^(--[^=]+)=(.*)$') {
        $option = $Matches[1]
        $prefix = $Matches[2]
        $choices = $script:SciproofChoices[$command]
        if ($choices -and $choices.ContainsKey($option)) {
            foreach ($choice in $choices[$option]) {
                if ($choice -like "$prefix*") { & $add "$option=$choice" 'ParameterValue' $choice }
            }
        }
        return $result
    }

    if ($pending -gt 0) {
        # Completing a value of $activeOption.
        $choices = if ($command) { $script:SciproofChoices[$command] } else { $null }
        if ($choices -and $choices.ContainsKey($activeOption)) {
            foreach ($choice in $choices[$activeOption]) {
                if ($choice -like "$WordToComplete*") { & $add $choice 'ParameterValue' $choice }
            }
        }
        return $result
    }

    if ($WordToComplete.StartsWith('-')) {
        $table = if ($command) { $script:SciproofOptions[$command] } else { $script:SciproofGlobalOptions }
        foreach ($option in ($table.Keys | Sort-Object)) {
            if ($option -like "$WordToComplete*") { & $add $option 'ParameterName' $option }
        }
        return $result
    }

    if (-not $command) {
        foreach ($name in $script:SciproofSubcommands.Keys) {
            if ($name -like "$WordToComplete*") { & $add $name 'ParameterValue' $script:SciproofSubcommands[$name] }
        }
    }
    elseif ($positionals -eq 0 -and $script:SciproofActions.ContainsKey($command)) {
        foreach ($action in $script:SciproofActions[$command]) {
            if ($action -like "$WordToComplete*") { & $add $action 'ParameterValue' $action }
        }
    }
    # Manifests, directories and other paths: no results, so PowerShell completes file names.
    return $result
}

Register-ArgumentCompleter -Native -CommandName sciproof -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    # Every element before the word being completed (the first one is "sciproof").
    $words = @(
        $commandAst.CommandElements |
            Select-Object -Skip 1 |
            Where-Object { $_.Extent.EndOffset -lt $cursorPosition } |
            ForEach-Object { $_.Extent.Text }
    )
    Get-SciproofCompletions -Words $words -WordToComplete $wordToComplete
}
