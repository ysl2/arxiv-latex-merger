import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import cli as cli_module
from . import downloader as downloader_module
from .merger import merge_tex_files


class MergerInputCommentTests(unittest.TestCase):
    def test_commented_inputs_in_child_files_are_not_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{section}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text(
                "Section body.\n"
                "%   \\input{missing}\n"
                "Text before comment. % \\input{also_missing}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Section body.", merged)
        self.assertIn("%   \\input{missing}", merged)
        self.assertIn("Text before comment. % \\input{also_missing}", merged)

    def test_active_inputs_in_child_files_are_processed_recursively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{section}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text(
                "Before subsection.\n"
                "\\input{subsection}\n"
                "After subsection.\n",
                encoding="utf-8",
            )
            (root / "subsection.tex").write_text("Nested body.\n", encoding="utf-8")

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Before subsection.", merged)
        self.assertIn("Nested body.", merged)
        self.assertIn("After subsection.", merged)
        self.assertNotIn("\\input{subsection}", merged)

    def test_input_with_explicit_non_tex_extension_is_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{references.bbl}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "references.bbl").write_text(
                "\\begin{thebibliography}{1}\n"
                "\\bibitem{key} Reference text.\n"
                "\\end{thebibliography}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\begin{thebibliography}{1}", merged)
        self.assertIn("\\bibitem{key} Reference text.", merged)
        self.assertNotIn("\\input{references.bbl}", merged)

    def test_escaped_percent_does_not_comment_out_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\% \\input{section}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text("Section body.\n", encoding="utf-8")

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\% Section body.", merged)
        self.assertNotIn("\\input{section}", merged)

    def test_nested_inputs_can_resolve_paths_relative_to_main_file_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "camera-ready" / "sec").mkdir(parents=True)
            (root / "camera-ready" / "tab").mkdir(parents=True)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{camera-ready/sec/method}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "camera-ready" / "sec" / "method.tex").write_text(
                "Before table.\n"
                "\\input{camera-ready/tab/results}\n"
                "After table.\n",
                encoding="utf-8",
            )
            (root / "camera-ready" / "tab" / "results.tex").write_text(
                "Table body.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Before table.", merged)
        self.assertIn("Table body.", merged)
        self.assertIn("After table.", merged)
        self.assertNotIn("\\input{camera-ready/tab/results}", merged)

    def test_nested_inputs_do_not_fallback_to_child_file_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sec").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{sec/method}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sec" / "method.tex").write_text(
                "\\input{subsection}\n",
                encoding="utf-8",
            )
            (root / "sec" / "subsection.tex").write_text(
                "Nested body.\n",
                encoding="utf-8",
            )

            with self.assertRaises(FileNotFoundError) as error:
                merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Expected", str(error.exception))
        self.assertIn("subsection.tex", str(error.exception))


class MergerBibliographyTests(unittest.TestCase):
    def test_existing_bbl_is_inlined_and_bibliography_commands_are_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{section}\n"
                "\\bibliographystyle{plain}\n"
                "\\bibliography{refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text("Section body.\n", encoding="utf-8")
            (root / "main.bbl").write_text(
                "\\begin{thebibliography}{1}\n"
                "\\bibitem{key} Reference text.\n"
                "\\end{thebibliography}\n",
                encoding="utf-8",
            )

            merged, encoding = merge_tex_files(str(root / "main.tex"))

        self.assertEqual(encoding, "utf-8")
        self.assertIn("Section body.", merged)
        self.assertIn("\\begin{thebibliography}{1}", merged)
        self.assertIn("\\bibitem{key} Reference text.", merged)
        self.assertNotIn("\\input{section}", merged)
        self.assertNotIn("\\bibliographystyle", merged)
        self.assertNotIn("\\bibliography", merged)

    def test_no_bib_option_preserves_bibliography_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\bibliographystyle{plain}\n"
                "\\bibliography{refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "main.bbl").write_text(
                "\\begin{thebibliography}{1}\n"
                "\\bibitem{key} Reference text.\n"
                "\\end{thebibliography}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\bibliographystyle{plain}", merged)
        self.assertIn("\\bibliography{refs}", merged)
        self.assertNotIn("\\begin{thebibliography}", merged)

    def test_commented_bibliography_commands_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "%   \\bibliography{refs}\n"
                "Text before comment. % \\bibliographystyle{plain}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "main.bbl").write_text(
                "\\begin{thebibliography}{1}\n"
                "\\bibitem{key} Reference text.\n"
                "\\end{thebibliography}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"))

        self.assertIn("%   \\bibliography{refs}", merged)
        self.assertIn("Text before comment. % \\bibliographystyle{plain}", merged)
        self.assertNotIn("\\begin{thebibliography}", merged)

    def test_missing_bbl_is_generated_from_existing_bib(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Cite \\cite{key}.\n"
                "\\bibliographystyle{plain}\n"
                "\\bibliography{refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "refs.bib").write_text(
                "@article{key, title={Title}, author={Author}, year={2024}}\n",
                encoding="utf-8",
            )

            def fake_run(*args, **kwargs):
                (root / "main.bbl").write_text(
                    "\\begin{thebibliography}{1}\n"
                    "\\bibitem{key} Generated reference.\n"
                    "\\end{thebibliography}\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args=args[0], returncode=0)

            with patch("arxiv_latex_merger.merger.subprocess.run", side_effect=fake_run) as run_mock:
                merged, _ = merge_tex_files(str(root / "main.tex"))

        run_mock.assert_called_once()
        self.assertIn("\\bibitem{key} Generated reference.", merged)
        self.assertNotIn("\\bibliography{refs}", merged)
        self.assertNotIn("\\bibliographystyle{plain}", merged)

    def test_missing_bbl_generation_failure_warns_and_preserves_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\bibliographystyle{plain}\n"
                "\\bibliography{refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "refs.bib").write_text("@article{key, title={Title}}\n", encoding="utf-8")

            stdout = io.StringIO()
            failed = subprocess.CompletedProcess(args=["latexmk"], returncode=1, stderr=b"failed")
            with patch("arxiv_latex_merger.merger.subprocess.run", return_value=failed):
                with redirect_stdout(stdout):
                    merged, _ = merge_tex_files(str(root / "main.tex"))

        self.assertIn("Warning:", stdout.getvalue())
        self.assertIn("\\bibliographystyle{plain}", merged)
        self.assertIn("\\bibliography{refs}", merged)
        self.assertNotIn("\\begin{thebibliography}", merged)


class MergerRemoveCommentsTests(unittest.TestCase):
    def test_remove_comments_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "% draft note\n"
                "\\begin{document}\n"
                "Visible text. % hidden note\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("% draft note", merged)
        self.assertIn("Visible text. % hidden note", merged)

    def test_remove_comments_drops_comment_lines_and_inline_comment_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article} % template note\n"
                "  % draft note\n"
                "\\begin{document}\n"
                "Visible text. % hidden note\n"
                "Escaped percent \\% remains. % hidden note\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False, remove_comments=True)

        self.assertIn("\\documentclass{article}\n", merged)
        self.assertIn("Visible text.\n", merged)
        self.assertIn("Escaped percent \\% remains.\n", merged)
        self.assertNotIn("draft note", merged)
        self.assertNotIn("hidden note", merged)
        self.assertIn("\\%", merged)

    def test_remove_comments_preserves_syntax_sensitive_line_end_percent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\newcommand{\\foo}{%\n"
                "  body%\n"
                "}%\n"
                "\\begin{document}\n"
                "\\foo\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False, remove_comments=True)

        self.assertIn("\\newcommand{\\foo}{%\n", merged)
        self.assertIn("  body%\n", merged)
        self.assertIn("}%\n", merged)

    def test_remove_comments_preserves_literal_environment_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\begin{lstlisting}\n"
                "% shown in listing\n"
                "code % shown in listing\n"
                "\\end{lstlisting}\n"
                "Text after. % removed\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False, remove_comments=True)

        self.assertIn("% shown in listing", merged)
        self.assertIn("code % shown in listing", merged)
        self.assertIn("Text after.\n", merged)
        self.assertNotIn("removed", merged)

    def test_remove_comments_deletes_comment_environment_but_keeps_package_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\usepackage{comment}\n"
                "\\begin{document}\n"
                "Before.\n"
                "\\begin{comment}\n"
                "Hidden text.\n"
                "\\end{comment}\n"
                "After.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False, remove_comments=True)

        self.assertIn("\\usepackage{comment}", merged)
        self.assertIn("Before.", merged)
        self.assertIn("After.", merged)
        self.assertNotIn("\\begin{comment}", merged)
        self.assertNotIn("Hidden text.", merged)
        self.assertNotIn("\\end{comment}", merged)


class CliTests(unittest.TestCase):
    def test_arxiv_codes_downloads_and_merges_each_code_once(self):
        args = SimpleNamespace(
            arxiv_codes=["1234.56789"],
            n_random=1,
            demacro=False,
            remove_src=False,
            no_bib=False,
            remove_comments=False,
            skip_download_if_exists=False,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((Path(temp_dir) / "1234.56789.tex").read_text(), "merged")
            self.assertFalse((Path(temp_dir) / "1234.56789_merged_utf-8.tex").exists())

        download_mock.assert_called_once_with("1234.56789")
        find_mock.assert_called_once_with("1234.56789")
        merge_mock.assert_called_once_with(
            "1234.56789/main.tex",
            remove_src=False,
            merge_bib=True,
            remove_comments=False,
        )

    def test_skip_download_if_exists_uses_existing_directory(self):
        args = SimpleNamespace(
            arxiv_codes=["1234.56789"],
            n_random=1,
            demacro=False,
            remove_src=False,
            no_bib=False,
            remove_comments=False,
            skip_download_if_exists=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "1234.56789").mkdir()
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            stdout = io.StringIO()
                            with redirect_stdout(stdout):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((root / "1234.56789.tex").read_text(), "merged")

        download_mock.assert_not_called()
        find_mock.assert_called_once_with("1234.56789")
        merge_mock.assert_called_once_with(
            "1234.56789/main.tex",
            remove_src=False,
            merge_bib=True,
            remove_comments=False,
        )
        self.assertIn("skipping download", stdout.getvalue())

    def test_skip_download_if_exists_downloads_when_directory_missing(self):
        args = SimpleNamespace(
            arxiv_codes=["1234.56789"],
            n_random=1,
            demacro=False,
            remove_src=False,
            no_bib=False,
            remove_comments=False,
            skip_download_if_exists=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")):
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

        download_mock.assert_called_once_with("1234.56789")
        find_mock.assert_called_once_with("1234.56789")

    def test_skip_download_if_exists_is_checked_per_code(self):
        args = SimpleNamespace(
            arxiv_codes=["1111.11111", "2222.22222"],
            n_random=1,
            demacro=False,
            remove_src=False,
            no_bib=False,
            remove_comments=False,
            skip_download_if_exists=True,
        )

        def fake_find_main_tex_file(code):
            return f"{code}/main.tex"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "1111.11111").mkdir()
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", side_effect=fake_find_main_tex_file) as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((root / "1111.11111.tex").read_text(), "merged")
            self.assertEqual((root / "2222.22222.tex").read_text(), "merged")

        download_mock.assert_called_once_with("2222.22222")
        self.assertEqual(find_mock.call_count, 2)
        self.assertEqual(merge_mock.call_count, 2)


class DownloaderTests(unittest.TestCase):
    def test_download_arxiv_source_files_reports_download_progress(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            content = b"\\documentclass{article}\n"
            info = tarfile.TarInfo("main.tex")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_payload = payload.getvalue()

        class FakeResponse:
            headers = {"content-length": str(len(tar_payload))}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield tar_payload[:10]
                yield tar_payload[10:]

        paper = SimpleNamespace(pdf_url="https://arxiv.org/pdf/1234.56789")

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                stdout = io.StringIO()
                stderr = io.StringIO()
                with patch("arxiv_latex_merger.downloader._arxiv_results", return_value=iter([paper])):
                    with patch("arxiv_latex_merger.downloader.requests.get", return_value=FakeResponse()) as get_mock:
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            downloader_module.download_arxiv_source_files("1234.56789")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789"
            self.assertTrue((output_dir / "main.tex").exists())
            self.assertFalse((output_dir / "1234.56789.tar.gz").exists())

        output = stdout.getvalue()
        self.assertIn("Fetching arXiv metadata for 1234.56789...", output)
        self.assertIn("Downloading source files for 1234.56789...", output)
        self.assertIn("Downloading source for 1234.56789", stderr.getvalue())
        get_mock.assert_called_once_with("https://arxiv.org/src/1234.56789", stream=True)
