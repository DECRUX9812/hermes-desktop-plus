"""HTTP routers contributed by hermes-plus features (registered before the /api proxy)."""

from starlette.routing import Route

from hermes_plus.http import desktop_plugins, files, git, mobile, share, uploads, webapp

# Our routes must precede the catch-all /api proxy in the route table.
ROUTERS: list[Route] = []
ROUTERS.extend(uploads.routes)
ROUTERS.extend(files.routes)
ROUTERS.extend(share.routes)
ROUTERS.extend(mobile.routes)
ROUTERS.extend(desktop_plugins.routes)
ROUTERS.extend(webapp.routes)
ROUTERS.extend(git.routes)

__all__ = ["ROUTERS"]
