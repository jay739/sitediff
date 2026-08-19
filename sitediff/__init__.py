"""sitediff: compare the visible content of two renderings of the same page.

Built for cross-environment parity checks (legacy vs rewrite, staging vs prod,
region vs region) where the two sides are produced by different codebases and
so cannot be compared byte for byte.
"""

from .config import Profile, load_profile
from .engine import DiffResult, Status, compare, noise_baseline
from .errors import SiteDiffError
from .extract import Page, extract

__version__ = "0.1.0"

__all__ = [
    "DiffResult",
    "Page",
    "Profile",
    "SiteDiffError",
    "Status",
    "__version__",
    "compare",
    "extract",
    "load_profile",
    "noise_baseline",
]
