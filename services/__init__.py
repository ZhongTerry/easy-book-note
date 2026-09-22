"""Service layer exports without eager, cross-service initialization."""

__all__ = ['book_service']


def __getattr__(name):
    if name == 'book_service':
        from .book_service import book_service
        return book_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
