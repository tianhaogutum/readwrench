"""Exception shared by every module; kept free of third-party imports."""


class InputError(ValueError):
    """An input cannot be processed without losing its meaning or location."""
