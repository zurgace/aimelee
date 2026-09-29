"""export_weights.ctype_value: Phillip's ctypes fields get the right Python type on every platform.

On Windows ctypes.c_uint is c_ulong (named "c_ulong"), which a name check took for a float field;
ctypes then refused the float ("'float' object cannot be interpreted as an integer")."""

import ctypes
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import export_weights  # noqa: E402


class CtypeValueTest(unittest.TestCase):
    def test_types(self):
        for t in (ctypes.c_uint, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_ubyte):
            self.assertIs(type(export_weights.ctype_value(t, 3.0)), int, t)
        self.assertIs(export_weights.ctype_value(ctypes.c_bool, 1.0), True)
        self.assertIs(type(export_weights.ctype_value(ctypes.c_float, 2)), float)

    def test_windows_c_uint_fields_take_the_values(self):
        class Player(ctypes.Structure):  # c_uint as Windows defines it
            _fields_ = [("action_state", ctypes.c_ulong), ("in_air", ctypes.c_bool), ("x", ctypes.c_float)]

        p = Player()
        for name, ftype in Player._fields_:
            setattr(p, name, export_weights.ctype_value(ftype, 42.0 if name != "in_air" else 1.0))
        self.assertEqual((p.action_state, p.in_air, p.x), (42, True, 42.0))
        with self.assertRaises(TypeError):
            p.action_state = 42.0  # what the old name check did


if __name__ == "__main__":
    unittest.main()
