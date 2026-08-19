"""Typed exception hierarchy for sitediff.

Every error carries a message that names the failure and the fix. Callers can
catch SiteDiffError to handle anything this package raises, or a narrower type
to react to one category.
"""


class SiteDiffError(Exception):
    """Base for every error raised by sitediff."""


class ConfigError(SiteDiffError):
    """Base for profile loading and validation failures."""


class ProfileNotFoundError(ConfigError):
    def __init__(self, name: str, searched: list[str]):
        self.name = name
        self.searched = searched
        locations = "\n  ".join(searched)
        super().__init__(
            f"No profile named {name!r}.\nLooked in:\n  {locations}\n"
            f"Pass a path to a .toml file, or run 'sitediff profiles' to list "
            f"the built-in ones."
        )


class InvalidProfileError(ConfigError):
    def __init__(self, path: str, problem: str):
        self.path = path
        self.problem = problem
        super().__init__(f"Profile {path} is not usable: {problem}")


class InputError(SiteDiffError):
    """Base for problems with the HTML inputs."""


class PageNotFoundError(InputError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(
            f"No such HTML file: {path}\n"
            f"Check the path, or capture the page first. For a client-rendered "
            f"page, save the post-render DOM from the browser console with "
            f"copy(document.documentElement.outerHTML)."
        )


class PageUnreadableError(InputError):
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Could not read {path}: {reason}")


class ExtractionError(SiteDiffError):
    """Base for failures while turning HTML into comparable text."""


class PageNotRenderedError(ExtractionError):
    """The page still shows a loading placeholder, so a diff would be noise."""

    def __init__(self, path: str, sentinel: str, side: str):
        self.path = path
        self.sentinel = sentinel
        self.side = side
        super().__init__(
            f"The {side} page ({path}) still contains the loading placeholder "
            f"{sentinel!r}, so it was captured before rendering finished.\n"
            f"Diffing it now would report every value as missing. Re-capture "
            f"after the page settles, or pass --allow-unrendered to override."
        )


class EmptyExtractionError(ExtractionError):
    def __init__(self, path: str, side: str):
        self.path = path
        self.side = side
        super().__init__(
            f"Extracted no visible text from the {side} page ({path}).\n"
            f"Either the file is not HTML, or the profile's include_selectors "
            f"matched nothing on this page. Run with --debug-lines to see what "
            f"the extractor kept."
        )


class NetworkError(SiteDiffError):
    """Base for anything that goes wrong acquiring a page over the network."""


class EgressBlockedError(NetworkError):
    """The tool refused to contact a host that the profile did not allow.

    This is the control that answers 'can this tool send our pages somewhere
    else'. It is enforced before every connection, including redirects.
    """

    def __init__(self, host: str, allowed: list[str], via: str = ""):
        self.host = host
        self.allowed = allowed
        self.via = via
        permitted = ", ".join(allowed) if allowed else "(nothing)"
        reached = f" (reached via {via})" if via else ""
        super().__init__(
            f"Refusing to contact {host}{reached}: it is not in the egress "
            f"allowlist.\nAllowed: {permitted}\n"
            f"If this host is legitimate, add it to [security] allow_hosts in "
            f"the profile. The tool never contacts a host you have not listed."
        )


class FetchError(NetworkError):
    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"Could not fetch {url}: {reason}")


class TooManyRedirectsError(NetworkError):
    def __init__(self, url: str, limit: int, chain: list[str]):
        self.url = url
        self.chain = chain
        trail = "\n  ".join(chain)
        super().__init__(
            f"{url} exceeded {limit} redirects. Chain:\n  {trail}\n"
            f"Raise max_redirects in the source config if this is expected."
        )


class SourceNotFoundError(ConfigError):
    def __init__(self, name: str, known: list[str]):
        self.name = name
        self.known = known
        available = ", ".join(known) if known else "(none defined)"
        super().__init__(
            f"No source named {name!r} in this profile.\nDefined sources: {available}\n"
            f"Add one with a [[source]] block, or pass a file path instead."
        )


class CaptureError(SiteDiffError):
    """A capture strategy could not produce a comparable payload."""


class UnsupportedCaptureError(CaptureError):
    def __init__(self, name: str):
        self.name = name
        super().__init__(
            f"Capture strategy {name!r} is not available in this install.\n"
            f"Browser capture needs the optional extra: pip install 'sitediff[browser]'.\n"
            f"Most sites do not need it. Run 'sitediff probe' against the page: if it "
            f"reports static, hydration or json, no browser is required."
        )


class MixedPayloadError(SiteDiffError):
    def __init__(self, left: str, right: str):
        super().__init__(
            f"Cannot compare a {left} payload against a {right} one.\n"
            f"Both sides must capture the same kind: either both structured "
            f"(json, hydration) or both text (static). Adjust 'capture' on one "
            f"of the sources so the two agree."
        )


class PairingError(SiteDiffError):
    """Entities could not be enumerated or paired across the two sides."""
