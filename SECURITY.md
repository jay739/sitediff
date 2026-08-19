# Security

## Reporting a vulnerability

Email **contact@jay739.dev**, or open a
[private security advisory](https://github.com/jay739/sitediff/security/advisories/new).
Please do not open a public issue for a vulnerability.

I will acknowledge within a week. This is a solo-maintained project, so there
is no formal SLA beyond that.

## What this tool is, in security terms

A local command line program. There is no server, no daemon, no listening
socket, and no hosted component. It reads files you name, contacts hosts you
configure, and writes files you name.

## What it does with your data

**Network.** It contacts only hosts named in the profile's egress allowlist.
That is checked before every connection, including each redirect hop, so an
allowed host cannot bounce it elsewhere. A `connect_to` override is a second
destination and needs its own entry. The allowlist is empty by default, which
blocks everything, so a profile with no sources reaches nothing.

A URL typed directly on the command line authorises that one host for that run,
on the reasoning that typing it is the consent. Redirects from it remain
constrained.

**No telemetry.** No analytics, no auto-update, no licence check, no crash
reporting. The only outbound requests are the ones your profile configures.
`sitediff/source.py` is the single module that opens a socket; everything else
is local. Verify with a packet capture if you like.

**TLS.** Certificates are verified by default. `verify_tls = false` exists for
endpoints that legitimately present an untrusted certificate, and it is opt-in
per source.

**Reports.** Page content reaching an HTML report is HTML-escaped, so a page
containing a script tag cannot execute when the report is opened. Credential
response headers (`set-cookie`, `authorization`, `x-api-key` and similar) are
withheld from reports even if a profile names them in `compare.headers`.

**Redaction.** `[[redact]]` rules run last, after every other transformation, on
both page content and response metadata. **If your pages contain tokens,
session identifiers or personal data, add redaction rules.** There are no
default content redaction rules, because a wrong guess would silently hide real
findings. Reports become CI artifacts and get passed around.

## Things you are responsible for

- **Credentials in profiles.** A profile may carry an `Authorization` header.
  Profiles live in your repository, so treat one containing a credential the way
  you would treat any other secret: keep it out of version control, or use a
  placeholder your CI fills in.
- **Where reports go.** They contain page content by design.
- **Permission to fetch.** The tool will contact whatever you configure. That
  you are allowed to is your call, not its.

## What is deliberately absent

It does not detect whether you are on a particular network and will not refuse
to run based on your location. Such a check is bypassable in a one-line patch,
redundant with targets already unreachable from outside, and would require
inspecting your network interfaces, which is more invasive than the problem it
claims to solve. Enforce network boundaries at the network.

## Supported versions

The latest release only. This is a young project; there is no backport branch.
