"""Personal budget manager.

Layers, from the centre outward (each one may import only the ones above it):

    models      transaction schema / domain types   (imports nothing)
    validation  boundary checks, clear error messages
    services    budget logic: add, filter, summarise (pure, no I/O)
    storage     load/save CSV files
    cli         command-line and interactive I/O
"""

__version__ = "1.0.0"
