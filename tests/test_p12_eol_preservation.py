"""Regression coverage for P12 dominant-EOL and final-newline preservation."""
import asyncio
import json
import tempfile
from pathlib import Path

import tools_upgrade_p12 as p12


def _run(handler, **kwargs):
    result = json.loads(asyncio.run(handler(**kwargs)))
    assert result.get("ok"), result
    return result


def _assert_no_lone_lf(raw: bytes):
    assert raw.count(b"\n") == raw.count(b"\r\n"), raw


def test_p12_eol_preservation():
    failures = []

    def check(name, case):
        try:
            case()
            print(f"PASS: {name}")
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"FAIL: {failures[-1]}")

    with tempfile.TemporaryDirectory(prefix="p12_eol_") as scratch:
        root = Path(scratch)

        def crlf_case():
            fp = root / "crlf.txt"
            original = b"alpha\r\nbeta\r\n"
            fp.write_bytes(original)
            result = _run(p12.insert_text, file_path=str(fp), content="middle",
                          at_line=2)
            raw = fp.read_bytes()
            _assert_no_lone_lf(raw)
            assert raw.endswith(b"\r\n"), raw
            assert Path(result["backup"]).read_bytes() == original

        def lf_case():
            fp = root / "lf.txt"
            fp.write_bytes(b"alpha\nbeta\n")
            _run(p12.insert_text, file_path=str(fp), content="middle", at_line=2)
            raw = fp.read_bytes()
            assert b"\r\n" not in raw, raw
            assert raw.endswith(b"\n"), raw

        def no_final_eol_case():
            inserted = root / "no-final-insert.txt"
            inserted.write_bytes(b"alpha\nbeta")
            _run(p12.insert_text, file_path=str(inserted), content="middle", at_line=2)
            assert not inserted.read_bytes().endswith(b"\n")

            cut = root / "no-final-cut.txt"
            cut.write_bytes(b"alpha\nbeta\ngamma")
            _run(p12.cut_range, file_path=str(cut), start_line=2, end_line=2)
            assert cut.read_bytes() == b"alpha\ngamma"
            assert not cut.read_bytes().endswith(b"\n")

        def mixed_dominant_case():
            fp = root / "mixed.txt"
            fp.write_bytes(b"alpha\r\nbeta\r\ngamma\n")
            _run(p12.insert_text, file_path=str(fp), content="middle", at_line=2)
            raw = fp.read_bytes()
            _assert_no_lone_lf(raw)
            assert raw == b"alpha\r\nmiddle\r\nbeta\r\ngamma\r\n", raw

        def backup_byte_exact_case():
            fp = root / "backup.txt"
            original = b"one\r\ntwo\nthree"
            fp.write_bytes(original)
            result = _run(p12.cut_range, file_path=str(fp), start_line=2, end_line=2)
            assert Path(result["backup"]).read_bytes() == original

        check("CRLF remains pure CRLF", crlf_case)
        check("LF remains pure LF", lf_case)
        check("insert/cut preserve missing final newline", no_final_eol_case)
        check("mixed EOL normalizes to dominant style", mixed_dominant_case)
        check("backup remains byte-exact", backup_byte_exact_case)

    if failures:
        print("FAILURES:")
        for failure in failures:
            print(f"- {failure}")
    else:
        print("ALL_PASS")
    assert not failures, failures