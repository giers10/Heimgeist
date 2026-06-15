import os
import sys


# The macOS wheels for PyTorch (used by Whisper) and FAISS bundle different
# libomp copies. FAISS aborts the process when its copy initializes second.
if sys.platform == "darwin":
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
