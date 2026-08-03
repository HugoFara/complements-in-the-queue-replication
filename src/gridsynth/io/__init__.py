"""I/O adapters that bring external grid formats into pandapower.

The first adapter is `import_opendss`: OpenDSS is the lingua franca of North
American distribution modelling (and the only format NREL SMART-DS ships in),
yet pandapower has no importer for it (issue #1442). This package fills that gap.
"""

from gridsynth.io.opendss import import_opendss

__all__ = ["import_opendss"]
