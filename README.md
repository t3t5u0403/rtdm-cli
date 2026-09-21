# rtdm

read the damn manual.

An AI shell helper that gives you the command without the commentary.

```
$ rtdm find files over 100mb
find . -size +100M
```

## Install

```
pipx install rtdm
rtdm config init
```

`rtdm config init` walks you through a one-time setup. Pick `local`
for free, self-hosted use, or `remote` if you have an API key from
[rtdm.sh](https://rtdm.sh).

## Usage

### Three modes

```
rtdm <query>           # get a shell command
rtdm -q <question>     # ask a general terminal question
rtdm -e <command>      # explain a command
```

Multi-word queries don't need quotes — words after the flags are
joined with spaces:

```
rtdm find files over 100mb     # works
rtdm "find files over 100mb"   # also works
```

The exception is when your input contains characters the shell would
interpret — globs (`*`, `?`, `[]`), control operators (`;`, `|`, `&`,
`<`, `>`), or arguments that look like flags (e.g. `--recursive`).
Quote those:

```
rtdm -e "find . -mtime -7 -exec rm {} \;"
```

Plain dashes are fine; `rtdm find files -size 100mb` works without
quoting.

### Flags

| Flag | Effect                                                         |
|------|----------------------------------------------------------------|
| `-c` | Copy the output to the clipboard                               |
| `-x` | Execute the returned command (cmd mode only, with confirmation)|

### Styled output

When stdout is a terminal, `rtdm` styles its output: commands get a colored
gutter bar, and `-e`/`-q` answers render in a bordered panel. The moment output
is piped or redirected (`rtdm ... | bash`, `-x`, `-c`), the raw text is used
instead — so styling never touches a command you execute, copy, or pipe onward.

### Subcommands

Account self-service for hosted-mode users. All require remote mode
(except `whoami`, which works in either mode).

| Subcommand     | Effect                                                          |
|----------------|-----------------------------------------------------------------|
| `rtdm portal`  | Open the Stripe billing portal (manage subscription, invoices)  |
| `rtdm rotate`  | Issue a new API key, revoke the old one, update local config    |
| `rtdm usage`   | Show this month's usage and remaining quota                     |
| `rtdm whoami`  | Show which key/host this CLI is configured against (no network) |

### Examples

```
$ rtdm extract a tar.gz file
tar -xzf archive.tar.gz

$ rtdm -q what does grep -E do
grep -E enables extended regular expressions, allowing operators like
+, ?, |, (), and {} without backslash escaping.

$ rtdm -e "find . -mtime -7 -exec rm {} \;"
This finds every file under the current directory modified in the last
7 days and deletes each one. -mtime -7 selects "less than 7 days ago";
-exec rm {} \; runs rm on each match.

$ rtdm -c list all my docker containers
docker ps -a
(copied to clipboard)
```

## Local mode (free, self-hosted)

Run your own [Ollama](https://ollama.com), point rtdm at it. No API
key needed, no network call to anything but your own machine. Free
forever.

See [LOCAL.md](LOCAL.md) for setup.

## Hosted mode ($3/month)

We run rtdm at [rtdm.sh](https://rtdm.sh) on dedicated GPU hardware.
No setup, no Ollama install, no model downloads — just one HTTP call
per query. 500 queries per month, cancel anytime.

Sign up at [rtdm.sh](https://rtdm.sh).

## Privacy

We don't log your queries on our servers. We don't train models on
them. We don't sell anything to anyone.

We do use Cloudflare in front for DDoS protection, which means
Cloudflare technically sees TLS-decrypted requests as they pass
through. Most modern services work this way and we think the
protection is worth it, but we know some of you will disagree. See
[rtdm.sh](https://rtdm.sh) for the full note.

If you want zero third parties in the path, use local mode.

## Configuration

The config file lives at `~/.config/rtdm/config.toml` (or
`$XDG_CONFIG_HOME/rtdm/config.toml`):

```toml
mode = "remote"  # or "local"

[remote]
api_key = "rtdm_live_..."
endpoint = "https://rtdm.sh"

[local]
ollama_url = "http://localhost:11434"
model = "qwen2.5-coder:7b-instruct-q4_K_M"
```

Useful subcommands:

```
rtdm config init     # interactive setup
rtdm config show     # print effective configuration
rtdm config path     # print the config file location
```

## Development

```
git clone https://github.com/t3t5u0403/rtdm-cli
cd rtdm-cli
uv sync --extra dev
uv run pytest
```

## License

MIT. See [LICENSE](LICENSE).
