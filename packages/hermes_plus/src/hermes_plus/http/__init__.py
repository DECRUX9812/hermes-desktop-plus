"""HTTP routers contributed by hermes-plus features."""

from starlette.routing import Route

# Import routes from each module
from hermes_plus.http import desktop_plugins, git, mobile, share, uploads, webapp

# Collect all routes in order
ROUTERS: list[Route] = []
ROUTERS.extend(uploads.routes)
ROUTERS.extend(share.routes)
ROUTERS.extend(mobile.routes)
ROUTERS.extend(desktop_plugins.routes)
ROUTERS.extend(webapp.routes)
ROUTERS.extend(git.routes)

__all__ = ["ROUTERS"]
