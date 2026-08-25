"""Image and DICOM handling.

PREPROCESSING_VERSION identifies the whole pixel pipeline -- DICOM rendering here
plus the model input preparation in chester.inference -- and is recorded on every
analysis result. It was previously declared in two modules that could drift; this is
the single definition. Bump it whenever the pixels reaching the model change.
"""

PREPROCESSING_VERSION = "2.0.0"

__all__ = ["PREPROCESSING_VERSION"]
