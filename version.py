"""Single source of truth for the app version (spec 015).

Bumped in lockstep with the git tag / GitHub release when cutting a release.
Baked into the build so the running exe knows its own version.
"""

__version__ = "1.1.0"
