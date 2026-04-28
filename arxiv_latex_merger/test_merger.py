import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import cli as cli_module
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


class CliTests(unittest.TestCase):
    def test_arxiv_codes_downloads_and_merges_each_code_once(self):
        args = SimpleNamespace(
            arxiv_codes=["1234.56789"],
            n_random=1,
            demacro=False,
            remove_src=False,
            no_bib=False,
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
        )
