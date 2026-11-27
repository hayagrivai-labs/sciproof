
# Tcsh tab completion for sciproof.
#
# Completes subcommands, options and fixed values of --format. Other values
# (manifest paths, files and directories) use Tcsh's normal file completion.
#
# Load in the current shell:
#     source examples/completions/sciproof.tcsh
#
# To load in every session, add the source command with this file's full path
# to ~/.tcshrc (~/.cshrc is read when ~/.tcshrc does not exist).
#
# Rule reference: tcsh(1), "complete".
#   p/1/...   first word: subcommands and global options
#   C/P/...   current word beginning with P; candidates include the full word
#   n/P/...   next word when the previous word begins with P
#
# Tcsh keeps P in the completed word for C rules. The --format= rule must
# precede the general long-option rule so both value and option completion work.
#
# Limitations:
# - After a subcommand, options from all subcommands are offered because
#   static rules cannot identify the active subcommand.
# - capture, record and suggest-inputs take a command after "--". Static rules
#   cannot distinguish that separator from an option, so command-name
#   completion is unavailable there.
# - n/--format/ also matches --format=json, so the file-completion rule for
#   --format= must come first.
# - n/pipeline/ matches any previous word beginning with "pipeline", including
#   a filename such as pipeline.toml.
# - A global option before the subcommand is not followed by subcommand
#   completion (for example, "sciproof -q ver").
#
# These lists mirror src/sciproof/cli.py. tests/test_tcsh_completion.py
# checks that they stay in sync.
complete sciproof \
    'p/1/(capture verify suggest-inputs sign badge record update calibrate diff list export pipeline check-policy inspect -h --help --version -q --quiet)/' \
    'C/--format=/(--format=text --format=json --format=markdown --format=junit --format=sarif --format=html --format=in-toto --format=dsse --format=openlineage --format=latex --format=typst --format=ro-crate)/' \
    'C/--/(--allow-nonzero --allowed-signers --atol --check --clean-env --claim --dry-run --exclude --fast --force --format --help --input --inputs --json --key --max-log-bytes --name --note --output --output-numeric --outputs --pass-env --policy --quiet --record-env --record-hardware --recursive --rerun --rtol --runs --save-json --seeds --strict-env --threads --timeout --tolerance --value --yes)/' \
    'C/-/(-h -q -r -o --)/' \
    'n/--format=/f/' \
    'n/--format/(text json markdown junit sarif html in-toto dsse openlineage latex typst ro-crate)/' \
    'n/pipeline/(verify)/'
