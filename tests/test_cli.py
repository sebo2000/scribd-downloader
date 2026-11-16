"""Issue #9: command-line links, links files and bulk downloads."""

import argparse
import io

import pytest

A = "https://www.scribd.com/document/111/Report"
B = "https://de.scribd.com/document/222/Report"
C = "https://www.scribd.com/document/333/Other"


def test_parse_formats(sd):
    assert sd.parse_formats("pdf") == ("pdf",)
    assert sd.parse_formats(" DOCX , pdf,docx ") == ("docx", "pdf")
    assert sd.parse_formats("all") == ("pdf", "txt", "docx")
    with pytest.raises(argparse.ArgumentTypeError):
        sd.parse_formats("epub")
    with pytest.raises(argparse.ArgumentTypeError):
        sd.parse_formats(" , ")


def test_default_arguments_keep_old_behaviour(sd):
    args = sd.build_arg_parser().parse_args([])
    assert args.urls == []
    assert args.formats == ("pdf",)
    assert args.output_dir == "."


def test_read_links_from_text_ignores_comments_and_blank_lines(sd):
    text = f"# my list\n\n{A}\n  {B}   {C}\n# done\n"
    assert sd.read_links_from_text(text) == [A, B, C]


def test_plan_downloads_dedupes_and_avoids_name_clashes(sd):
    jobs, invalid = sd.plan_downloads([A, B, A, "https://example.com/x", C])
    assert invalid == ["https://example.com/x"]
    assert [job["info"]["doc_id"] for job in jobs] == ["111", "222", "333"]
    # Same title "Report" -> the second one gets its id appended.
    assert [job["base_name"] for job in jobs] == ["Report", "Report-222", "Other"]


def test_collect_urls_from_arguments_file_and_stdin(sd, tmp_path):
    links = tmp_path / "links.txt"
    links.write_text(f"﻿{B}\n# comment\n", encoding="utf-8")
    args = sd.build_arg_parser().parse_args([A, "--file", str(links), "--file", "-"])
    urls = sd.collect_urls(args, input_func=pytest.fail, stdin=io.StringIO(C))
    assert urls == [A, B, C]


def test_collect_urls_prompts_when_nothing_given(sd):
    args = sd.build_arg_parser().parse_args([])
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return f"{A} {B}"

    assert sd.collect_urls(args, input_func=fake_input) == [A, B]
    assert prompts == ["Input link Scribd: "]


def test_main_rejects_invalid_link(sd, capsys):
    with pytest.raises(SystemExit) as exit_info:
        sd.main(["https://example.com/document/1/x"])
    assert exit_info.value.code == 1
    assert "Not a valid Scribd document URL" in capsys.readouterr().out


def test_main_bulk_downloads_continue_after_a_failure(sd, monkeypatch, tmp_path, capsys):
    calls = []

    def fake_download(job, formats, output_dir):
        calls.append((job["info"]["doc_id"], formats, output_dir))
        if job["info"]["doc_id"] == "222":
            raise RuntimeError("No printable document pages were detected.\n  Reason: test")
        return {}

    monkeypatch.setattr(sd, "download_document", fake_download)
    out_dir = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        sd.main([A, B, C, "--format", "pdf,txt", "-o", str(out_dir)])

    assert exit_info.value.code == 1
    assert calls == [
        ("111", ("pdf", "txt"), str(out_dir)),
        ("222", ("pdf", "txt"), str(out_dir)),
        ("333", ("pdf", "txt"), str(out_dir)),
    ]
    assert out_dir.is_dir()
    output = capsys.readouterr().out
    assert "Summary: 2 succeeded, 1 failed." in output
    assert "FAILED Report-222: No printable document pages were detected." in output


def test_main_succeeds_with_exit_code_zero(sd, monkeypatch):
    monkeypatch.setattr(sd, "download_document", lambda job, formats, output_dir: {})
    assert sd.main([A]) is None


def test_main_reports_missing_links_file(sd, capsys):
    with pytest.raises(SystemExit) as exit_info:
        sd.main(["--file", "does-not-exist.txt"])
    assert exit_info.value.code == 2
    assert "could not read links file" in capsys.readouterr().err


def test_interactive_mode_keeps_asking_until_empty_line(sd, monkeypatch, capsys):
    calls = []

    def fake_download(job, formats, output_dir):
        calls.append(job["info"]["doc_id"])
        if job["info"]["doc_id"] == "222":
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr(sd, "download_document", fake_download)
    answers = iter([A, "not-a-link", B, C, ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    # A failed or invalid link does not end the session, and quitting is exit code 0.
    assert sd.main([]) is None
    assert calls == ["111", "222", "333"]
    output = capsys.readouterr().out
    assert "Not a valid Scribd document URL" in output
    assert "Export failed: boom" in output


@pytest.mark.parametrize("ending", ["q", EOFError, KeyboardInterrupt])
def test_interactive_mode_quits_on_q_or_end_of_input(sd, monkeypatch, ending):
    calls = []
    monkeypatch.setattr(
        sd, "download_document", lambda job, formats, output_dir: calls.append(job)
    )
    answers = iter([A, ending])

    def fake_input(prompt):
        answer = next(answers)
        if isinstance(answer, str):
            return answer
        raise answer

    monkeypatch.setattr("builtins.input", fake_input)
    assert sd.main([]) is None
    assert len(calls) == 1
