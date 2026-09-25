"""Setu payment reconciliation service. Importing this package has no side effects."""


def create_app(settings=None):
    """Lazy factory also keeps data-generation/seed tools free of ASGI imports."""
    from .api import create_app as factory
    return factory(settings)
