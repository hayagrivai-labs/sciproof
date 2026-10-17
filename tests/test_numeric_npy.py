from __future__ import annotations

import sys

import pytest

from sciproof.capture import capture
from sciproof.numeric import NumericFormatError, compare, load_numeric
from sciproof.verify import Status, verify

np = pytest.importorskip("numpy")


def _save(path, array):
    np.save(path, array, allow_pickle=False)
    return load_numeric(path)


def test_equal_arrays_are_equivalent(tmp_path):
    a = _save(tmp_path / "a.npy", np.arange(12, dtype=float).reshape(3, 4))
    b = _save(tmp_path / "b.npy", np.arange(12, dtype=float).reshape(3, 4))
    assert compare(a, b, rtol=0.0, atol=0.0) == []


def test_tolerance_applies_to_float_arrays(tmp_path):
    a = _save(tmp_path / "a.npy", np.array([1.0, 1000.0]))
    b = _save(tmp_path / "b.npy", np.array([1.0, 1000.0000001]))
    assert compare(a, b, rtol=1e-6, atol=0.0) == []
    differences = compare(a, b, rtol=1e-12, atol=0.0)
    assert len(differences) == 1 and "data[1]" in differences[0]


def test_integer_arrays_are_compared_exactly(tmp_path):
    a = _save(tmp_path / "a.npy", np.array([2**53 + 1], dtype=np.int64))
    b = _save(tmp_path / "b.npy", np.array([2**53], dtype=np.int64))
    assert compare(a, b, rtol=1.0, atol=1.0) != []


def test_nan_matches_nan_and_infinities_must_match(tmp_path):
    a = _save(tmp_path / "a.npy", np.array([np.nan, np.inf, -np.inf]))
    same = _save(tmp_path / "b.npy", np.array([np.nan, np.inf, -np.inf]))
    other = _save(tmp_path / "c.npy", np.array([np.nan, -np.inf, -np.inf]))
    assert compare(a, same, 0.0, 0.0) == []
    assert compare(a, other, 1.0, 1.0) != []


def test_nan_does_not_match_a_number(tmp_path):
    a = _save(tmp_path / "a.npy", np.array([np.nan]))
    b = _save(tmp_path / "b.npy", np.array([0.0]))
    assert compare(a, b, 1.0, 1.0) != []


def test_shape_and_dtype_differences_are_reported(tmp_path):
    base = _save(tmp_path / "a.npy", np.zeros((2, 3)))
    assert compare(base, _save(tmp_path / "b.npy", np.zeros((3, 2))), 0.0, 0.0) != []
    assert compare(base, _save(tmp_path / "c.npy", np.zeros((2, 3), dtype=np.float32)), 1, 1) != []


def test_complex_arrays_are_supported(tmp_path):
    a = _save(tmp_path / "a.npy", np.array([1 + 1j, 2 - 2j]))
    b = _save(tmp_path / "b.npy", np.array([1 + 1j, 2 - 2.0000001j]))
    assert compare(a, b, rtol=1e-6, atol=0.0) == []
    assert compare(a, b, rtol=0.0, atol=0.0) != []


def test_many_differences_are_summarised(tmp_path):
    a = _save(tmp_path / "a.npy", np.zeros(100))
    b = _save(tmp_path / "b.npy", np.ones(100))
    differences = compare(a, b, 0.0, 0.0)
    assert len(differences) == 11 and "100 of 100 values differ" in differences[-1]


def test_object_arrays_are_refused_without_unpickling(tmp_path):
    path = tmp_path / "obj.npy"
    np.save(path, np.array([{"a": 1}], dtype=object), allow_pickle=True)
    with pytest.raises(NumericFormatError, match="not a plain .npy array"):
        load_numeric(path)


def test_string_arrays_are_refused(tmp_path):
    path = tmp_path / "s.npy"
    np.save(path, np.array(["a", "b"]))
    with pytest.raises(NumericFormatError, match="only numeric"):
        load_numeric(path)


def test_a_corrupt_npy_file_is_a_clean_error(tmp_path):
    path = tmp_path / "bad.npy"
    path.write_bytes(b"this is not an npy file")
    with pytest.raises(NumericFormatError):
        load_numeric(path)


def test_missing_numpy_gives_an_actionable_message(tmp_path, monkeypatch):
    path = tmp_path / "a.npy"
    np.save(path, np.zeros(2))
    monkeypatch.setitem(sys.modules, "numpy", None)  # makes "import numpy" fail
    with pytest.raises(NumericFormatError, match=r"sciproof\[numpy\]"):
        load_numeric(path)


def test_capture_and_rerun_of_an_npy_output(project):
    code = (
        "import numpy as np; open('marker.txt', 'a').write('ran' + chr(10)); "
        "np.save('out.npy', np.linspace(0.0, 1.0, 5))"
    )
    project.capture(
        argv=[sys.executable, "-c", code],
        inputs=["data.json"],
        outputs=[],
        numeric_outputs=["out.npy"],
        rtol=1e-9,
    )
    assert verify(project.manifest).status is Status.PASS
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.PASS
    assert any("numerically equivalent" in c.message for c in report.checks)


def test_capture_rejects_an_npy_file_that_cannot_be_compared(project):
    code = "import numpy as np; np.save('out.npy', np.array(['x']))"
    with pytest.raises(Exception, match="only numeric"):
        capture(
            name="bad",
            argv=[sys.executable, "-c", code],
            base=project.root,
            inputs=["data.json"],
            numeric_outputs=["out.npy"],
        )
