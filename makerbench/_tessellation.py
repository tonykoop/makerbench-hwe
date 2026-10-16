"""Shared relative STL meshing tolerances for the B-rep backends.

The linear constant retains its existing public name for compatibility;
relative meshing scales the deflection with edge size.
"""

STL_LINEAR_TOLERANCE_MM = 0.01
STL_ANGULAR_TOLERANCE_RAD = 0.1
