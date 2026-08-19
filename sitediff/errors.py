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
