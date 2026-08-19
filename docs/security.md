# Security posture

What this tool reads, writes and contacts, and what it deliberately will not do.

[← back to the README](../README.md)

## Security posture

Teams adopting this tool ask what it does with their internal pages. The short
answer is that it is a local CLI with no egress it was not told to make.

**No network access by default.** A profile with no sources and no
`allow_hosts` has an empty allowlist, and an empty allowlist blocks everything.

**Nothing is hardcoded.** There is not a single hostname or address literal in
any engine module, and a test enforces that. Every host the tool may contact
comes from the profile you write. Bundled templates may reference only RFC 2606
documentation domains, which cannot reach anything real, and that is enforced
too.

**A URL you type is its own authorisation.** Passing a URL directly on the
command line permits that one host for that run, since typing it is the
consent. Everything else still comes from the profile, and redirects from that
URL remain constrained, so an ad-hoc target cannot bounce the tool onward.

**Egress allowlist.** The tool contacts only hosts the profile names. This is
checked before every connection, including each redirect hop, so an allowed
host cannot bounce it to somewhere else. A `connect_to` override is a second
destination and requires its own entry. Suffix entries (`.example.com`) match
subdomains only, not lookalikes such as `example.com.attacker.net`.

**No phoning home.** No telemetry, no analytics, no auto-update, no license
check, no crash reporting. The only outbound requests are the ones your profile
configures. Verify with a packet capture, or by reading `sitediff/source.py`,
which is the single file that opens a socket.

**Local only.** It reads the files and URLs you name and writes the report path
you name. Nothing else is written and nothing is retained between runs.

**Redaction.** `[[redact]]` rules are applied last, after every other
transformation, to both page content and response metadata. Diff reports become
CI artifacts and get passed around, so anything sensitive should be redacted
before it can reach one.

**Small dependency tree.** All network I/O uses the Python standard library, so
reviewing what goes on the wire does not mean auditing an HTTP stack. The only
runtime dependencies are `beautifulsoup4`, `lxml` and `rich`.

### What it deliberately does not do

It does not detect whether you are on a particular network, and it will not
refuse to run based on your location. That check would be bypassable in a
one-line patch, redundant with targets that are already unreachable from
outside, and it would require the tool to inspect your network interfaces,
which is more invasive than the problem it claims to solve. If a target should
only be reachable from inside a perimeter, enforce that at the network, where
enforcement is real.
