from __future__ import annotations

import json

import pytest

from sciproof.numeric import NumericFormatError, compare, load_numeric


def _json(path, value):
    path.write_text(json.dumps(value))
    return load_numeric(path)


def test_values_within_relative_tolerance_are_equivalent(tmp_path):
    a = _json(tmp_path / "a.json", {"x": 1000.0, "y": [1, 2]})
    b = _json(tmp_path / "b.json", {"x": 1000.0000001, "y": [1, 2]})
    assert compare(a, b, rtol=1e-6, atol=0.0) == []
    assert compare(a, b, rtol=1e-12, atol=0.0) != []


def test_absolute_tolerance_matters_near_zero(tmp_path):
    a = _json(tmp_path / "a.json", {"x": 0.0})
    b = _json(tmp_path / "b.json", {"x": 1e-9})
    assert compare(a, b, rtol=1e-6, atol=0.0) != []  # relative tolerance of 0 is 0
    assert compare(a, b, rtol=0.0, atol=1e-6) == []


def test_zero_tolerance_means_exact(tmp_path):
    a = _json(tmp_path / "a.json", {"x": 0.1 + 0.2})
    b = _json(tmp_path / "b.json", {"x": 0.3})
    assert compare(a, b, rtol=0.0, atol=0.0) != []


def test_nan_equals_nan_and_infinities_must_match(tmp_path):
    (tmp_path / "a.json").write_text('{"x": NaN, "y": Infinity}')
    (tmp_path / "b.json").write_text('{"x": NaN, "y": Infinity}')
    (tmp_path / "c.json").write_text('{"x": NaN, "y": -Infinity}')
    a, b, c = (load_numeric(tmp_path / f"{n}.json") for n in "abc")
    assert compare(a, b, 0, 0) == []
    assert compare(a, c, 1.0, 1.0) != []


def test_non_numeric_values_must_match_exactly(tmp_path):
    a = _json(tmp_path / "a.json", {"label": "ok", "flag": True, "none": None})
    b = _json(tmp_path / "b.json", {"label": "ok ", "flag": True, "none": None})
    assert compare(a, b, 1.0, 1.0) != []


def test_booleans_are_not_numbers(tmp_path):
    a = _json(tmp_path / "a.json", {"flag": True})
    b = _json(tmp_path / "b.json", {"flag": 1})
    assert compare(a, b, 1.0, 1.0) != []


def test_structural_differences_are_detected(tmp_path):
    a = _json(tmp_path / "a.json", {"x": [1, 2, 3]})
    assert compare(a, _json(tmp_path / "b.json", {"x": [1, 2]}), 1.0, 1.0) != []
    assert compare(a, _json(tmp_path / "c.json", {"y": [1, 2, 3]}), 1.0, 1.0) != []


def test_number_vs_string_differs(tmp_path):
    a = _json(tmp_path / "a.json", {"x": 1})
    b = _json(tmp_path / "b.json", {"x": "1"})
    assert compare(a, b, 1.0, 1.0) != []


def test_csv_numbers_are_compared_with_tolerance_and_text_exactly(tmp_path):
    (tmp_path / "a.csv").write_text("name,value\nrun1,1.000000\nrun2,2.0\n")
    (tmp_path / "b.csv").write_text("name,value\nrun1,1.0000001\nrun2,2.0\n")
    (tmp_path / "c.csv").write_text("name,value\nrunX,1.0\nrun2,2.0\n")
    a, b, c = (load_numeric(tmp_path / f"{n}.csv") for n in "abc")
    assert compare(a, b, rtol=1e-6, atol=0.0) == []
    assert compare(a, c, rtol=1e-6, atol=0.0) != []


def test_csv_shape_changes_are_detected(tmp_path):
    (tmp_path / "a.csv").write_text("1,2\n3,4\n")
    (tmp_path / "b.csv").write_text("1,2\n3,4\n5,6\n")
    assert compare(load_numeric(tmp_path / "a.csv"), load_numeric(tmp_path / "b.csv"), 1, 1) != []


def test_number_of_reported_differences_is_limited(tmp_path):
    a = _json(tmp_path / "a.json", list(range(100)))
    b = _json(tmp_path / "b.json", [v + 1 for v in range(100)])
    assert len(compare(a, b, 0.0, 0.0)) == 10


@pytest.mark.parametrize("name,content", [("a.json", "{broken"), ("b.txt", "1,2")])
def test_unsupported_or_broken_files_raise(tmp_path, name, content):
    (tmp_path / name).write_text(content)
    with pytest.raises(NumericFormatError):
        load_numeric(tmp_path / name)


def test_non_utf8_file_raises(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"\xff\xfe1,2")
    with pytest.raises(NumericFormatError):
        load_numeric(tmp_path / "a.csv")


def test_large_integers_are_compared_exactly(tmp_path):
    a = _json(tmp_path / "a.json", {"x": 2**53 + 1})
    b = _json(tmp_path / "b.json", {"x": 2**53})
    assert compare(a, b, rtol=0.0, atol=0.0) != []  # float() would make these equal
    assert compare(a, a, rtol=0.0, atol=0.0) == []


def test_csv_quoted_line_breaks_are_kept(tmp_path):
    (tmp_path / "a.csv").write_bytes(b'c\n"x\ny"\n')
    (tmp_path / "b.csv").write_bytes(b'c\n"x y"\n')
    (tmp_path / "crlf.csv").write_bytes(b'c\r\n"x\r\ny"\r\n')
    a, b, crlf = (load_numeric(tmp_path / n) for n in ("a.csv", "b.csv", "crlf.csv"))
    assert dict(a)["r1c0"] == "x\ny"
    assert compare(a, b, 0.0, 0.0) != []
    assert len(crlf) == len(a)  # a CRLF file has the same structure


def test_csv_cells_with_digit_separators_are_text(tmp_path):
    (tmp_path / "a.csv").write_text("v\n1_000\n")
    (tmp_path / "b.csv").write_text("v\n1000\n")
    a, b = load_numeric(tmp_path / "a.csv"), load_numeric(tmp_path / "b.csv")
    assert compare(a, b, 1.0, 1.0) != []


def test_huge_integers_do_not_crash_the_comparison(tmp_path):
    huge = 10**400
    a = _json(tmp_path / "a.json", {"x": huge})
    same = _json(tmp_path / "b.json", {"x": huge})
    off_by_one = _json(tmp_path / "c.json", {"x": huge + 1})
    as_float = _json(tmp_path / "d.json", {"x": 1.5})
    assert compare(a, same, 1e-9, 0.0) == []
    assert compare(a, off_by_one, 1e-9, 0.0) != []
    assert compare(a, as_float, 1e-9, 0.0) != []  # used to raise OverflowError
    assert compare(as_float, a, 1e-9, 0.0) != []


def test_huge_integers_are_shortened_in_messages(tmp_path):
    a = _json(tmp_path / "a.json", {"x": 10**400})
    b = _json(tmp_path / "b.json", {"x": 10**400 + 1})
    (message,) = compare(a, b, 0.0, 0.0)
    assert len(message) < 300 and "characters" in message


def test_a_number_is_never_equal_to_a_boolean_in_either_direction(tmp_path):
    number = _json(tmp_path / "a.json", {"x": 1})
    flag = _json(tmp_path / "b.json", {"x": True})
    assert compare(number, flag, 1.0, 1.0) != []
    assert compare(flag, number, 1.0, 1.0) != []
    zero = _json(tmp_path / "c.json", {"x": 0})
    no = _json(tmp_path / "d.json", {"x": False})
    assert compare(zero, no, 1.0, 1.0) != []


def test_tsv_numbers_are_compared_with_tolerance_and_text_exactly(tmp_path):
    (tmp_path / "a.tsv").write_text("name\tvalue\nrun1\t1.000000\nrun2\t2.0\n")
    (tmp_path / "b.tsv").write_text("name\tvalue\nrun1\t1.0000001\nrun2\t2.0\n")
    (tmp_path / "c.tsv").write_text("name\tvalue\nrun1\t1.5\nrun2\t2.0\n")
    (tmp_path / "d.tsv").write_text("name\tvalue\nrunX\t1.0\nrun2\t2.0\n")
    a, b, c, d = (load_numeric(tmp_path / f"{n}.tsv") for n in "abcd")
    assert compare(a, b, rtol=1e-6, atol=0.0) == []
    diffs = compare(a, c, rtol=1e-6, atol=0.0)
    assert len(diffs) == 1
    assert "r1c1 (column 'value'): recorded 1.0, found 1.5" in diffs[0]
    assert compare(a, d, rtol=1e-6, atol=0.0) != []


def test_tsv_quoted_tab_inside_cell(tmp_path):
    (tmp_path / "a.tsv").write_text('col1\tcol2\n"val\twith\ttab"\t10.0\n')
    (tmp_path / "b.tsv").write_text('col1\tcol2\n"val\twith\ttab"\t10.0000001\n')
    (tmp_path / "c.tsv").write_text('col1\tcol2\n"different"\t10.0\n')
    a, b, c = (load_numeric(tmp_path / f"{n}.tsv") for n in "abc")
    assert dict(a)["r1c0"] == "val\twith\ttab"
    assert compare(a, b, rtol=1e-6, atol=0.0) == []
    assert compare(a, c, rtol=1e-6, atol=0.0) != []


def test_tsv_ragged_rows(tmp_path):
    (tmp_path / "a.tsv").write_text("1\t2\n3\n")
    (tmp_path / "b.tsv").write_text("1\t2\n3\t4\n")
    (tmp_path / "same.tsv").write_text("1\t2\n3\n")
    a, b, same = (load_numeric(tmp_path / f"{n}.tsv") for n in ("a", "b", "same"))
    assert compare(a, same, 0.0, 0.0) == []
    assert compare(a, b, 1.0, 1.0) != []


def test_unsupported_file_format_message(tmp_path):
    f = tmp_path / "test.unknown"
    f.write_text("1\t2\n")
    with pytest.raises(
        NumericFormatError,
        match=r"numeric comparison supports \.json, \.csv, \.tsv, \.npy files, got 'test\.unknown'",
    ):
        load_numeric(f)
