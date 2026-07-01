import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from . import cli as cli_module
from . import downloader as downloader_module
from .merger import _run_latexmk_with_timeout, find_main_tex_file, merge_tex_files


class MergerInputCommentTests(unittest.TestCase):
    def test_find_main_tex_uses_arxiv_readme_toplevel_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "00README.json").write_text(
                '{"sources":[{"usage":"toplevel","filename":"latex/main.tex"}]}',
                encoding="utf-8",
            )
            (root / "latex").mkdir()
            (root / "latex" / "main.tex").write_text(
                "\\documentclass{article}\n",
                encoding="utf-8",
            )

            main_tex_path = find_main_tex_file(str(root))

        self.assertEqual(main_tex_path, str(root / "latex" / "main.tex"))

    def test_find_main_tex_does_not_return_non_tex_single_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "figs").mkdir()
            (root / "figs" / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            with self.assertRaises(FileNotFoundError):
                find_main_tex_file(str(root))

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

    def test_inputs_inside_comment_environment_are_not_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Before comment.\n"
                "\\begin{comment}\n"
                "\\input{missing}\n"
                "\\end{comment}\n"
                "\\input{section}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text("Section body.\n", encoding="utf-8")

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\begin{comment}", merged)
        self.assertIn("\\input{missing}", merged)
        self.assertIn("\\end{comment}", merged)
        self.assertIn("Section body.", merged)
        self.assertNotIn("\\input{section}", merged)

    def test_inputs_inside_literal_environment_are_not_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\begin{verbatim}\n"
                "\\input{missing}\n"
                "\\end{verbatim}\n"
                "\\input{section}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text("Section body.\n", encoding="utf-8")

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\begin{verbatim}", merged)
        self.assertIn("\\input{missing}", merged)
        self.assertIn("\\end{verbatim}", merged)
        self.assertIn("Section body.", merged)
        self.assertNotIn("\\input{section}", merged)

    def test_inputs_inside_iffalse_block_are_not_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{section}\n"
                "\\iffalse\n"
                "\\input{missing}\n"
                "\\fi\n"
                "\\input{appendix}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text("Section body.\n", encoding="utf-8")
            (root / "appendix.tex").write_text("Appendix body.\n", encoding="utf-8")

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Section body.", merged)
        self.assertIn("\\input{missing}", merged)
        self.assertIn("Appendix body.", merged)
        self.assertNotIn("\\input{section}", merged)
        self.assertNotIn("\\input{appendix}", merged)

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

    def test_include_commands_are_processed_recursively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\include{sections/method}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sections" / "method.tex").write_text(
                "\\section{Method}\n"
                "\\include{sections/details}\n",
                encoding="utf-8",
            )
            (root / "sections" / "details.tex").write_text(
                "Method details.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\section{Method}", merged)
        self.assertIn("Method details.", merged)
        self.assertNotIn("\\include{sections/method}", merged)
        self.assertNotIn("\\include{sections/details}", merged)

    def test_subfile_commands_are_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\subfile{sections/experiment}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sections" / "experiment.tex").write_text(
                "\\section{Experiment}\n"
                "Experiment details.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\section{Experiment}", merged)
        self.assertIn("Experiment details.", merged)
        self.assertNotIn("\\subfile{sections/experiment}", merged)

    def test_import_commands_are_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "paper").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\import{paper/}{abstract}\n"
                "\\subimport{paper/}{body}\n"
                "\\includefrom{paper/}{results}\n"
                "\\subincludefrom{paper/}{conclusion}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "paper" / "abstract.tex").write_text(
                "\\begin{abstract}\nAbstract text.\n\\end{abstract}\n",
                encoding="utf-8",
            )
            (root / "paper" / "body.tex").write_text(
                "\\section{Body}\nBody text.\n",
                encoding="utf-8",
            )
            (root / "paper" / "results.tex").write_text(
                "\\section{Results}\nResults text.\n",
                encoding="utf-8",
            )
            (root / "paper" / "conclusion.tex").write_text(
                "\\section{Conclusion}\nConclusion text.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\begin{abstract}", merged)
        self.assertIn("Abstract text.", merged)
        self.assertIn("\\section{Body}", merged)
        self.assertIn("\\section{Results}", merged)
        self.assertIn("\\section{Conclusion}", merged)
        self.assertNotIn("\\import{paper/}{abstract}", merged)
        self.assertNotIn("\\subimport{paper/}{body}", merged)
        self.assertNotIn("\\includefrom{paper/}{results}", merged)
        self.assertNotIn("\\subincludefrom{paper/}{conclusion}", merged)

    def test_new_include_commands_inside_comments_and_iffalse_are_not_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "% \\include{missing}\n"
                "\\iffalse\n"
                "\\subfile{also_missing}\n"
                "\\fi\n"
                "\\begin{comment}\n"
                "\\import{missing/}{file}\n"
                "\\end{comment}\n"
                "\\include{visible}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "visible.tex").write_text("Visible body.\n", encoding="utf-8")

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("% \\include{missing}", merged)
        self.assertIn("\\subfile{also_missing}", merged)
        self.assertIn("\\import{missing/}{file}", merged)
        self.assertIn("Visible body.", merged)
        self.assertNotIn("\\include{visible}", merged)

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

    def test_input_with_dotted_tex_basename_is_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{sections/appendix.implementation}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sections" / "appendix.implementation.tex").write_text(
                "Implementation details.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Implementation details.", merged)
        self.assertNotIn("\\input{sections/appendix.implementation}", merged)

    def test_input_path_ignores_surrounding_whitespace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "table").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{ table/reference-compatible}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "table" / "reference-compatible.tex").write_text(
                "Reference compatibility table.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Reference compatibility table.", merged)
        self.assertNotIn("\\input{ table/reference-compatible}", merged)

    def test_input_path_ignores_whitespace_around_separators_when_needed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{sections/ dataset_stats.tex}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sections" / "dataset_stats.tex").write_text(
                "Dataset statistics.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Dataset statistics.", merged)
        self.assertNotIn("\\input{sections/ dataset_stats.tex}", merged)

    def test_input_path_preserves_real_space_after_separator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{sections/ dataset_stats.tex}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sections" / " dataset_stats.tex").write_text(
                "Filename with leading space.\n",
                encoding="utf-8",
            )
            (root / "sections" / "dataset_stats.tex").write_text(
                "Filename without leading space.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Filename with leading space.", merged)
        self.assertNotIn("Filename without leading space.", merged)

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

    def test_inputs_can_resolve_paths_relative_to_source_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "00README.json").write_text(
                '{"sources":[{"usage":"toplevel","filename":"paper/main.tex"}]}',
                encoding="utf-8",
            )
            (root / "paper").mkdir()
            (root / "section").mkdir()
            (root / "paper" / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{section/abs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section" / "abs.tex").write_text(
                "Abstract body.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "paper" / "main.tex"), merge_bib=False)

        self.assertIn("Abstract body.", merged)
        self.assertNotIn("\\input{section/abs}", merged)

    def test_basedir_absolute_input_alias_resolves_from_source_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{/basedir/00_abstract}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "00_abstract.tex").write_text(
                "Abstract body.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Abstract body.", merged)
        self.assertNotIn("\\input{/basedir/00_abstract}", merged)

    def test_input_path_macro_is_expanded_before_resolution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\newcommand{\\basedir}{sections}\n"
                "\\begin{document}\n"
                "\\input{\\basedir/00_abstract}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sections" / "00_abstract.tex").write_text(
                "Abstract body.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Abstract body.", merged)
        self.assertNotIn("\\input{\\basedir/00_abstract}", merged)

    def test_nested_inputs_can_fallback_to_child_file_directory(self):
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

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Nested body.", merged)
        self.assertNotIn("\\input{subsection}", merged)

    def test_nested_inputs_can_resolve_sibling_directory_from_child_file_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "content").mkdir()
            (root / "content" / "table").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{content/method}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "content" / "method.tex").write_text(
                "\\input{table/rl_compatibility}\n",
                encoding="utf-8",
            )
            (root / "content" / "table" / "rl_compatibility.tex").write_text(
                "RL compatibility table.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("RL compatibility table.", merged)
        self.assertNotIn("\\input{table/rl_compatibility}", merged)

    def test_missing_glyphtounicode_input_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\input{glyphtounicode}\n"
                "\\begin{document}\n"
                "Body.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\input{glyphtounicode}", merged)
        self.assertIn("Body.", merged)

    def test_local_glyphtounicode_input_is_still_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\input{glyphtounicode}\n"
                "\\begin{document}\n"
                "Body.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "glyphtounicode.tex").write_text(
                "\\pdfglyphtounicode{A}{0041}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\pdfglyphtounicode{A}{0041}", merged)
        self.assertNotIn("\\input{glyphtounicode}", merged)

    def test_missing_insbox_input_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\input{insbox}\n"
                "\\begin{document}\n"
                "Body.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\input{insbox}", merged)
        self.assertIn("Body.", merged)

    def test_missing_epsf_input_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\input{epsf}\n"
                "\\begin{document}\n"
                "Body.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\input{epsf}", merged)
        self.assertIn("Body.", merged)

    def test_local_insbox_input_is_still_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\input{insbox}\n"
                "\\begin{document}\n"
                "Body.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "insbox.tex").write_text(
                "\\newbox\\insbox\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\newbox\\insbox", merged)
        self.assertNotIn("\\input{insbox}", merged)

    def test_missing_if_file_exists_input_uses_false_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\IfFileExists{rev.tex}{\\input{rev}}{No revision file.}\n"
                "Body.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("No revision file.", merged)
        self.assertIn("Body.", merged)
        self.assertNotIn("\\input{rev}", merged)

    def test_existing_if_file_exists_input_uses_true_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\IfFileExists{rev.tex}{\\input{rev}}{No revision file.}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "rev.tex").write_text(
                "Revision notes.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Revision notes.", merged)
        self.assertNotIn("No revision file.", merged)
        self.assertNotIn("\\input{rev}", merged)

    def test_multiline_if_file_exists_selects_one_input_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "common").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\IfFileExists{common/0abs_cr.tex}{\n"
                "  \\input{common/0abs_cr}\n"
                "}{\n"
                "  \\input{common/0abs}\n"
                "}\n"
                "\\IfFileExists{common/1intro_cr.tex}{\n"
                "  \\input{common/1intro_cr}\n"
                "}{\n"
                "  \\input{common/1intro}\n"
                "}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "common" / "0abs.tex").write_text(
                "Abstract body.\n",
                encoding="utf-8",
            )
            (root / "common" / "1intro_cr.tex").write_text(
                "Camera-ready intro.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("Abstract body.", merged)
        self.assertIn("Camera-ready intro.", merged)
        self.assertNotIn("\\input{common/0abs_cr}", merged)
        self.assertNotIn("\\input{common/0abs}", merged)
        self.assertNotIn("\\input{common/1intro}", merged)

    def test_macro_parameter_input_is_preserved_when_unresolved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\newcommand{\\loadsection}[1]{\\input{#1}}\n"
                "\\input{section}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "section.tex").write_text(
                "Section body.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "main.tex"), merge_bib=False)

        self.assertIn("\\newcommand{\\loadsection}[1]{\\input{#1}}", merged)
        self.assertIn("Section body.", merged)
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

    def test_existing_bbl_can_be_inlined_from_source_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "00README.json").write_text(
                '{"sources":[{"usage":"toplevel","filename":"latex/main.tex"}]}',
                encoding="utf-8",
            )
            (root / "latex").mkdir()
            (root / "latex" / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\bibliography{latex/custom}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "main.bbl").write_text(
                "\\begin{thebibliography}{1}\n"
                "\\bibitem{key} Source-root reference.\n"
                "\\end{thebibliography}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(str(root / "latex" / "main.tex"))

        self.assertIn("\\bibitem{key} Source-root reference.", merged)
        self.assertNotIn("\\bibliography{latex/custom}", merged)

    def test_missing_bbl_is_generated_from_source_root_relative_bib(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "00README.json").write_text(
                '{"sources":[{"usage":"toplevel","filename":"latex/main.tex"}]}',
                encoding="utf-8",
            )
            (root / "latex").mkdir()
            (root / "latex" / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Cite \\cite{key}.\n"
                "\\bibliographystyle{plain}\n"
                "\\bibliography{latex/refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "latex" / "refs.bib").write_text(
                "@article{key, title={Title}, author={Author}, year={2024}}\n",
                encoding="utf-8",
            )

            def fake_run(args, **kwargs):
                self.assertEqual(args, ["bibtex", "arxiv_latex_merger_bibtex_fallback"])
                fallback_root = Path(kwargs["cwd"])
                aux_content = (fallback_root / "arxiv_latex_merger_bibtex_fallback.aux").read_text(
                    encoding="utf-8",
                )
                self.assertIn("\\bibdata{bib_0}", aux_content)
                self.assertTrue((fallback_root / "bib_0.bib").is_file())
                (fallback_root / "arxiv_latex_merger_bibtex_fallback.bbl").write_text(
                    "\\begin{thebibliography}{1}\n"
                    "\\bibitem{key} Generated root-relative reference.\n"
                    "\\end{thebibliography}\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args=args, returncode=0)

            with patch("arxiv_latex_merger.merger.subprocess.run", side_effect=fake_run) as run_mock:
                merged, _ = merge_tex_files(str(root / "latex" / "main.tex"))

        run_mock.assert_called_once()
        self.assertIn("\\bibitem{key} Generated root-relative reference.", merged)
        self.assertNotIn("\\bibliography{latex/refs}", merged)
        self.assertNotIn("\\bibliographystyle{plain}", merged)

    def test_missing_bbl_uses_arxiv_readme_compiler(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "00README.json").write_text(
                '{"sources":[{"usage":"toplevel","filename":"main.tex"}],'
                '"process":{"compiler":"xelatex"}}',
                encoding="utf-8",
            )
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

            with patch("arxiv_latex_merger.merger.subprocess.run", return_value=subprocess.CompletedProcess(args=["bibtex"], returncode=1)):
                with patch("arxiv_latex_merger.merger._run_latexmk_with_timeout") as latexmk_mock:
                    def fake_latexmk(args, cwd):
                        (root / "main.bbl").write_text(
                            "\\begin{thebibliography}{1}\n"
                            "\\bibitem{key} Generated xelatex reference.\n"
                            "\\end{thebibliography}\n",
                            encoding="utf-8",
                        )
                        return subprocess.CompletedProcess(args=args, returncode=0)

                    latexmk_mock.side_effect = fake_latexmk
                    merged, _ = merge_tex_files(str(root / "main.tex"))

        run_args = latexmk_mock.call_args.args[0]
        self.assertIn("-pdfxe", run_args)
        self.assertNotIn("-pdf", run_args)
        self.assertIn("\\bibitem{key} Generated xelatex reference.", merged)
        self.assertNotIn("\\bibliography{refs}", merged)

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

    def test_bibtex_fallback_runs_before_latexmk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Cite \\citep[see][chap. 2]{alpha, beta} and \\citet{gamma}.\n"
                "% \\cite{commented}\n"
                "\\bibliographystyle{plain}\n"
                "\\bibliography{refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "refs.bib").write_text(
                "@article{alpha, title={Alpha}, author={Author}, year={2024}}\n"
                "@article{beta, title={Beta}, author={Author}, year={2024}}\n"
                "@article{gamma, title={Gamma}, author={Author}, year={2024}}\n",
                encoding="utf-8",
            )

            def fake_run(args, **kwargs):
                if args[0] == "latexmk":
                    return subprocess.CompletedProcess(args=args, returncode=1)

                self.assertEqual(args, ["bibtex", "arxiv_latex_merger_bibtex_fallback"])
                fallback_root = Path(kwargs["cwd"])
                aux_content = (fallback_root / "arxiv_latex_merger_bibtex_fallback.aux").read_text(
                    encoding="utf-8",
                )
                self.assertIn("\\citation{alpha}", aux_content)
                self.assertIn("\\citation{beta}", aux_content)
                self.assertIn("\\citation{gamma}", aux_content)
                self.assertNotIn("commented", aux_content)
                self.assertIn("\\bibstyle{plain}", aux_content)
                self.assertIn("\\bibdata{bib_0}", aux_content)
                self.assertTrue((fallback_root / "bib_0.bib").is_file())
                (fallback_root / "arxiv_latex_merger_bibtex_fallback.bbl").write_text(
                    "\\begin{thebibliography}{1}\n"
                    "\\bibitem{alpha} Alpha reference.\n"
                    "\\bibitem{beta} Beta reference.\n"
                    "\\bibitem{gamma} Gamma reference.\n"
                    "\\end{thebibliography}\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args=args, returncode=0)

            with patch("arxiv_latex_merger.merger.subprocess.run", side_effect=fake_run) as run_mock:
                with patch("arxiv_latex_merger.merger._run_latexmk_with_timeout") as latexmk_mock:
                    merged, _ = merge_tex_files(str(root / "main.tex"))

        run_mock.assert_called_once()
        latexmk_mock.assert_not_called()
        self.assertIn("\\bibitem{alpha} Alpha reference.", merged)
        self.assertIn("\\bibitem{beta} Beta reference.", merged)
        self.assertIn("\\bibitem{gamma} Gamma reference.", merged)
        self.assertNotIn("\\bibliography{refs}", merged)
        self.assertNotIn("\\bibliographystyle{plain}", merged)

    def test_bibtex_fallback_handles_multiple_bib_files_and_nocite_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\nocite{*}\n"
                "\\bibliography{refs,more_refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "refs.bib").write_text("@article{alpha, title={Alpha}}\n", encoding="utf-8")
            (root / "more_refs.bib").write_text("@article{beta, title={Beta}}\n", encoding="utf-8")

            def fake_run(args, **kwargs):
                if args[0] == "latexmk":
                    return subprocess.CompletedProcess(args=args, returncode=1)

                fallback_root = Path(kwargs["cwd"])
                aux_content = (fallback_root / "arxiv_latex_merger_bibtex_fallback.aux").read_text(
                    encoding="utf-8",
                )
                self.assertIn("\\citation{*}", aux_content)
                self.assertIn("\\bibstyle{plain}", aux_content)
                self.assertIn("\\bibdata{bib_0,bib_1}", aux_content)
                self.assertTrue((fallback_root / "bib_0.bib").is_file())
                self.assertTrue((fallback_root / "bib_1.bib").is_file())
                (fallback_root / "arxiv_latex_merger_bibtex_fallback.bbl").write_text(
                    "\\begin{thebibliography}{1}\n"
                    "\\bibitem{alpha} Alpha reference.\n"
                    "\\bibitem{beta} Beta reference.\n"
                    "\\end{thebibliography}\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args=args, returncode=0)

            with patch("arxiv_latex_merger.merger.subprocess.run", side_effect=fake_run):
                merged, _ = merge_tex_files(str(root / "main.tex"))

        self.assertIn("\\bibitem{alpha} Alpha reference.", merged)
        self.assertIn("\\bibitem{beta} Beta reference.", merged)
        self.assertNotIn("\\bibliography{refs,more_refs}", merged)

    def test_missing_bbl_generation_failure_warns_and_preserves_commands(self):
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
            (root / "refs.bib").write_text("@article{key, title={Title}}\n", encoding="utf-8")

            stdout = io.StringIO()
            failed = subprocess.CompletedProcess(args=["latexmk"], returncode=1, stderr=b"failed")
            with patch("arxiv_latex_merger.merger.subprocess.run", return_value=failed) as run_mock:
                with patch("arxiv_latex_merger.merger._run_latexmk_with_timeout", return_value=failed) as latexmk_mock:
                    with redirect_stdout(stdout):
                        merged, _ = merge_tex_files(str(root / "main.tex"))

        run_mock.assert_called_once()
        latexmk_mock.assert_called_once()
        self.assertIn("Warning:", stdout.getvalue())
        self.assertIn("\\bibliographystyle{plain}", merged)
        self.assertIn("\\bibliography{refs}", merged)
        self.assertNotIn("\\begin{thebibliography}", merged)

    def test_latexmk_timeout_terminates_process_group(self):
        class FakeProcess:
            pid = 12345
            returncode = None

            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd=["latexmk"], timeout=timeout)

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.returncode = -15

        fake_process = FakeProcess()
        with patch("arxiv_latex_merger.merger.subprocess.Popen", return_value=fake_process) as popen_mock:
            with patch("arxiv_latex_merger.merger.os.killpg") as killpg_mock:
                result = _run_latexmk_with_timeout(
                    ["latexmk", "-pdf", "main.tex"],
                    "/tmp",
                )

        self.assertEqual(result.returncode, -15)
        popen_mock.assert_called_once()
        if os.name == "posix":
            killpg_mock.assert_called_once_with(fake_process.pid, 15)


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

    def test_remove_comments_keeps_text_after_comment_inside_inlined_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{abstract}\n"
                "\\input{method}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "abstract.tex").write_text(
                "\\begin{abstract}\n"
                "Visible abstract before note.\n"
                "% draft note\n"
                "Visible abstract after note.\n"
                "\\end{abstract}\n",
                encoding="utf-8",
            )
            (root / "method.tex").write_text(
                "\\section{Method}\n"
                "Method body.\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(
                str(root / "main.tex"),
                merge_bib=False,
                remove_comments=True,
            )

        self.assertIn("\\begin{abstract}", merged)
        self.assertIn("Visible abstract before note.", merged)
        self.assertIn("Visible abstract after note.", merged)
        self.assertIn("\\end{abstract}", merged)
        self.assertIn("\\section{Method}", merged)
        self.assertIn("Method body.", merged)
        self.assertNotIn("draft note", merged)

    def test_remove_comments_keeps_all_recursive_inputs_and_inlines_bibliography(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{macros}\n"
                "\\input{sections/abstract}\n"
                "\\input{sections/body}\n"
                "\\bibliographystyle{plain}\n"
                "\\bibliography{refs}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (root / "macros.tex").write_text(
                "% macro draft note\n"
                "\\newcommand{\\topicmodel}{TopicModelX}\n",
                encoding="utf-8",
            )
            (root / "sections" / "abstract.tex").write_text(
                "\\begin{abstract}\n"
                "Abstract opening uses \\topicmodel.\n"
                "% discarded abstract alternative\n"
                "Abstract text after a comment line remains.\n"
                "\\end{abstract}\n",
                encoding="utf-8",
            )
            (root / "sections" / "body.tex").write_text(
                "\\section{Method}\n"
                "Method body before inline note. % inline note removed\n"
                "\\input{sections/nested}\n",
                encoding="utf-8",
            )
            (root / "sections" / "nested.tex").write_text(
                "\\subsection{Nested Results}\n"
                "Nested input body remains.\n",
                encoding="utf-8",
            )
            (root / "main.bbl").write_text(
                "\\begin{thebibliography}{1}\n"
                "\\bibitem{ref} Reference body.\n"
                "\\end{thebibliography}\n",
                encoding="utf-8",
            )

            merged, _ = merge_tex_files(
                str(root / "main.tex"),
                merge_bib=True,
                remove_comments=True,
            )

        self.assertIn("\\newcommand{\\topicmodel}{TopicModelX}", merged)
        self.assertIn("\\begin{abstract}", merged)
        self.assertIn("Abstract opening uses \\topicmodel.", merged)
        self.assertIn("Abstract text after a comment line remains.", merged)
        self.assertIn("\\end{abstract}", merged)
        self.assertIn("\\section{Method}", merged)
        self.assertIn("Method body before inline note.\n", merged)
        self.assertIn("\\subsection{Nested Results}", merged)
        self.assertIn("Nested input body remains.", merged)
        self.assertIn("\\begin{thebibliography}{1}", merged)
        self.assertIn("\\bibitem{ref} Reference body.", merged)
        self.assertNotIn("\\input{macros}", merged)
        self.assertNotIn("\\input{sections/abstract}", merged)
        self.assertNotIn("\\input{sections/body}", merged)
        self.assertNotIn("\\input{sections/nested}", merged)
        self.assertNotIn("\\bibliographystyle{plain}", merged)
        self.assertNotIn("\\bibliography{refs}", merged)
        self.assertNotIn("macro draft note", merged)
        self.assertNotIn("discarded abstract alternative", merged)
        self.assertNotIn("inline note removed", merged)

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
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files", return_value="1234.56789v1") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789v1/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((Path(temp_dir) / "1234.56789v1.tex").read_text(), "merged")
            self.assertFalse((Path(temp_dir) / "1234.56789v1_merged_utf-8.tex").exists())

        download_mock.assert_called_once_with("1234.56789")
        find_mock.assert_called_once_with("1234.56789v1")
        merge_mock.assert_called_once_with(
            "1234.56789v1/main.tex",
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
            (root / "1234.56789v2").mkdir()
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789v2/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            stdout = io.StringIO()
                            with redirect_stdout(stdout):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((root / "1234.56789v2.tex").read_text(), "merged")

        download_mock.assert_not_called()
        find_mock.assert_called_once_with("1234.56789v2")
        merge_mock.assert_called_once_with(
            "1234.56789v2/main.tex",
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
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files", return_value="1234.56789v1") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789v1/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")):
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

        download_mock.assert_called_once_with("1234.56789")
        find_mock.assert_called_once_with("1234.56789v1")

    def test_skip_download_if_exists_downloads_when_existing_directory_has_no_main_tex(self):
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
            (root / "1234.56789v1").mkdir()
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files", return_value="1234.56789v1") as download_mock:
                    with patch(
                        "arxiv_latex_merger.cli.find_main_tex_file",
                        side_effect=[
                            FileNotFoundError("No main .tex file found"),
                            "1234.56789v1/main.tex",
                        ],
                    ) as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")):
                            stdout = io.StringIO()
                            with redirect_stdout(stdout):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((root / "1234.56789v1.tex").read_text(), "merged")

        download_mock.assert_called_once_with("1234.56789")
        self.assertEqual(find_mock.call_count, 2)
        self.assertIn("downloading again", stdout.getvalue())

    def test_skip_download_if_exists_skips_existing_pdf_only_directory(self):
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
            source_dir = root / "1234.56789v1"
            source_dir.mkdir()
            (source_dir / "1234.56789v1.pdf").write_bytes(b"%PDF-1.7\n")
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", side_effect=FileNotFoundError("No main .tex file found")) as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files") as merge_mock:
                            stdout = io.StringIO()
                            with redirect_stdout(stdout):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertFalse((root / "1234.56789v1.tex").exists())

        download_mock.assert_not_called()
        find_mock.assert_not_called()
        merge_mock.assert_not_called()
        self.assertIn("Local PDF-only source exists for 1234.56789v1", stdout.getvalue())

    def test_skip_download_if_exists_prefers_existing_tex_over_pdf(self):
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
            source_dir = root / "1234.56789v1"
            source_dir.mkdir()
            (source_dir / "1234.56789v1.pdf").write_bytes(b"%PDF-1.7\n")
            (source_dir / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789v1/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((root / "1234.56789v1.tex").read_text(), "merged")

        download_mock.assert_not_called()
        find_mock.assert_called_once_with("1234.56789v1")
        merge_mock.assert_called_once_with(
            "1234.56789v1/main.tex",
            remove_src=False,
            merge_bib=True,
            remove_comments=False,
        )

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
            (root / "1111.11111v2").mkdir()
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files", return_value="2222.22222v1") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", side_effect=fake_find_main_tex_file) as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual((root / "1111.11111v2.tex").read_text(), "merged")
            self.assertEqual((root / "2222.22222v1.tex").read_text(), "merged")

        download_mock.assert_called_once_with("2222.22222")
        self.assertEqual(find_mock.call_count, 2)
        self.assertEqual(merge_mock.call_count, 2)

    def test_keeps_only_selected_local_version_when_skipping_download(self):
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
            old_dir = root / "1234.56789v1"
            old_dir.mkdir()
            (old_dir / "main.tex").write_text("old", encoding="utf-8")
            (root / "1234.56789v1.tex").write_text("old merged", encoding="utf-8")
            (root / "1234.56789v1_clean.tex").write_text("old clean", encoding="utf-8")
            (root / "1234.56789").mkdir()
            (root / "1234.56789.tex").write_text("old unversioned merged", encoding="utf-8")
            (root / "9999.99999v1").mkdir()
            (old_dir / "1234.56789v1.pdf").write_bytes(b"%PDF-1.7\n")
            os.symlink("1234.56789v1/1234.56789v1.pdf", root / "1234.56789v1.pdf")
            (root / "1234.56789v2").mkdir()

            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789v2/main.tex") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("new merged", "utf-8")):
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertFalse((root / "1234.56789v1").exists())
            self.assertFalse((root / "1234.56789v1.tex").exists())
            self.assertFalse((root / "1234.56789v1_clean.tex").exists())
            self.assertFalse((root / "1234.56789v1.pdf").exists())
            self.assertFalse((root / "1234.56789").exists())
            self.assertFalse((root / "1234.56789.tex").exists())
            self.assertTrue((root / "9999.99999v1").is_dir())
            self.assertTrue((root / "1234.56789v2").is_dir())
            self.assertEqual((root / "1234.56789v2.tex").read_text(), "new merged")

        download_mock.assert_not_called()
        find_mock.assert_called_once_with("1234.56789v2")

    def test_downloaded_source_version_removes_older_local_versions(self):
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
            root = Path(temp_dir)
            (root / "1234.56789v1").mkdir()
            (root / "1234.56789v1.tex").write_text("old merged", encoding="utf-8")
            (root / "1234.56789v1_clean.tex").write_text("old clean", encoding="utf-8")
            (root / "1234.56789v2").mkdir()

            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files", return_value="1234.56789v2") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", return_value="1234.56789v2/main.tex"):
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("new merged", "utf-8")):
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertFalse((root / "1234.56789v1").exists())
            self.assertFalse((root / "1234.56789v1.tex").exists())
            self.assertFalse((root / "1234.56789v1_clean.tex").exists())
            self.assertTrue((root / "1234.56789v2").is_dir())
            self.assertEqual((root / "1234.56789v2.tex").read_text(), "new merged")

        download_mock.assert_called_once_with("1234.56789")

    def test_downloaded_pdf_version_removes_older_local_versions(self):
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
            root = Path(temp_dir)
            (root / "1234.56789v1").mkdir()
            (root / "1234.56789v1.tex").write_text("old merged", encoding="utf-8")
            pdf_dir = root / "1234.56789v2"
            pdf_dir.mkdir()
            (pdf_dir / "1234.56789v2.pdf").write_bytes(b"%PDF-1.7\n")
            os.symlink("1234.56789v2/1234.56789v2.pdf", root / "1234.56789v2.pdf")

            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files", return_value="1234.56789v2") as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file") as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files") as merge_mock:
                            with redirect_stdout(io.StringIO()):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertFalse((root / "1234.56789v1").exists())
            self.assertFalse((root / "1234.56789v1.tex").exists())
            self.assertTrue((root / "1234.56789v2").is_dir())
            self.assertTrue((root / "1234.56789v2.pdf").is_symlink())

        download_mock.assert_called_once_with("1234.56789")
        find_mock.assert_not_called()
        merge_mock.assert_not_called()

    def test_source_download_error_skips_code_and_continues(self):
        args = SimpleNamespace(
            arxiv_codes=["1111.11111", "2222.22222"],
            n_random=1,
            demacro=False,
            remove_src=False,
            no_bib=False,
            remove_comments=False,
            skip_download_if_exists=False,
        )

        def fake_download(code):
            if code == "1111.11111":
                raise downloader_module.SourceDownloadError("no source archive")
            return "2222.22222v1"

        def fake_find_main_tex_file(code):
            return f"{code}/main.tex"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.cli.download_arxiv_source_files", side_effect=fake_download) as download_mock:
                    with patch("arxiv_latex_merger.cli.find_main_tex_file", side_effect=fake_find_main_tex_file) as find_mock:
                        with patch("arxiv_latex_merger.cli.merge_tex_files", return_value=("merged", "utf-8")) as merge_mock:
                            stdout = io.StringIO()
                            with redirect_stdout(stdout):
                                cli_module.main(args)
            finally:
                os.chdir(previous_cwd)

            self.assertFalse((root / "1111.11111.tex").exists())
            self.assertEqual((root / "2222.22222v1.tex").read_text(), "merged")

        self.assertEqual(download_mock.call_count, 2)
        find_mock.assert_called_once_with("2222.22222v1")
        merge_mock.assert_called_once()
        self.assertIn("Skipping 1111.11111", stdout.getvalue())


class DownloaderTests(unittest.TestCase):
    def _download_response(self, payload, filename=None):
        class DownloadResponse:
            headers = {"content-length": str(len(payload))}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield payload

        if filename:
            DownloadResponse.headers["content-disposition"] = f'attachment; filename="{filename}"'

        return DownloadResponse()

    def test_download_arxiv_source_files_reports_download_progress(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            content = b"\\documentclass{article}\n"
            info = tarfile.TarInfo("main.tex")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_payload = payload.getvalue()

        class SourceResponse:
            headers = {
                "content-length": str(len(tar_payload)),
                "content-disposition": 'attachment; filename="arXiv-1234.56789v1.tar.gz"',
            }

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield tar_payload[:10]
                yield tar_payload[10:]

        def fake_get(url, **kwargs):
            if url == "https://arxiv.org/src/1234.56789":
                return SourceResponse()
            raise AssertionError(f"Unexpected URL: {url}")

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                stdout = io.StringIO()
                stderr = io.StringIO()
                with patch("arxiv_latex_merger.downloader.requests.get", side_effect=fake_get) as get_mock:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        downloaded_code = downloader_module.download_arxiv_source_files("1234.56789")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789v1"
            self.assertTrue((output_dir / "main.tex").exists())
            self.assertFalse((output_dir / "1234.56789v1.tar.gz").exists())

        self.assertEqual(downloaded_code, "1234.56789v1")
        self.assertIn("Downloading source files for 1234.56789v1...", stdout.getvalue())
        self.assertIn("Downloading source for 1234.56789v1", stderr.getvalue())
        self.assertEqual(
            get_mock.call_args_list,
            [
                call("https://arxiv.org/src/1234.56789", stream=True),
            ],
        )

    def test_download_arxiv_source_files_falls_back_to_pdf_when_no_source_versions_download(self):
        pdf_payload = b"%PDF-1.7\n"

        class SourcePdfResponse:
            headers = {
                "content-length": str(len(pdf_payload)),
                "content-disposition": 'attachment; filename="arXiv-1234.56789v2.pdf"',
                "content-type": "application/pdf",
            }

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield pdf_payload

        class MissingResponse:
            headers = {}

            def raise_for_status(self):
                raise RuntimeError("not found")

        def fake_get(url, **kwargs):
            if url == "https://arxiv.org/src/1234.56789":
                return SourcePdfResponse()
            if url == "https://arxiv.org/pdf/1234.56789v2":
                return self._download_response(pdf_payload, filename="1234.56789v2.pdf")
            return MissingResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.downloader.requests.get", side_effect=fake_get) as get_mock:
                    with redirect_stderr(io.StringIO()):
                        downloaded_code = downloader_module.download_arxiv_source_files("1234.56789")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789v2"
            pdf_path = output_dir / "1234.56789v2.pdf"
            symlink_path = Path(temp_dir) / "1234.56789v2.pdf"
            self.assertEqual(pdf_path.read_bytes(), pdf_payload)
            self.assertTrue(symlink_path.is_symlink())
            self.assertEqual(os.readlink(symlink_path), "1234.56789v2/1234.56789v2.pdf")
            self.assertTrue(symlink_path.samefile(pdf_path))
            self.assertFalse((output_dir / "1234.56789v2.tar.gz").exists())

        self.assertEqual(downloaded_code, "1234.56789v2")
        self.assertEqual(
            [call.args[0] for call in get_mock.call_args_list],
            ["https://arxiv.org/src/1234.56789"]
            + ["https://arxiv.org/src/1234.56789v1"] * 3
            + ["https://arxiv.org/pdf/1234.56789v2"],
        )

    def test_download_arxiv_source_files_falls_back_through_source_versions_before_pdf(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            content = b"\\documentclass{article}\n"
            info = tarfile.TarInfo("main.tex")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_payload = payload.getvalue()

        class BrokenLatestResponse:
            headers = {
                "content-length": str(len(payload.getvalue())),
                "content-disposition": 'attachment; filename="arXiv-1234.56789v3.tar.gz"',
            }

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield b"not a gzipped tar archive"

        class MissingResponse:
            headers = {}

            def raise_for_status(self):
                raise RuntimeError("not found")

        def fake_get(url, **kwargs):
            if url == "https://arxiv.org/src/1234.56789":
                return BrokenLatestResponse()
            if url == "https://arxiv.org/src/1234.56789v1":
                return self._download_response(tar_payload, filename="arXiv-1234.56789v1.tar.gz")
            return MissingResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.downloader.requests.get", side_effect=fake_get) as get_mock:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        downloaded_code = downloader_module.download_arxiv_source_files("1234.56789")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789v1"
            self.assertTrue((output_dir / "main.tex").exists())

        self.assertEqual(downloaded_code, "1234.56789v1")
        self.assertEqual(
            [call.args[0] for call in get_mock.call_args_list],
            ["https://arxiv.org/src/1234.56789"] * 3
            + ["https://arxiv.org/src/1234.56789v2"] * 3
            + ["https://arxiv.org/src/1234.56789v1"],
        )

    def test_download_arxiv_source_files_falls_back_to_v1_when_unversioned_has_no_version_hint(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            content = b"\\documentclass{article}\n"
            info = tarfile.TarInfo("main.tex")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_payload = payload.getvalue()

        class MissingResponse:
            headers = {}

            def raise_for_status(self):
                raise RuntimeError("not found")

        def fake_get(url, **kwargs):
            if url == "https://arxiv.org/src/1234.56789v1":
                return self._download_response(tar_payload, filename="arXiv-1234.56789v1.tar.gz")
            return MissingResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.downloader.requests.get", side_effect=fake_get) as get_mock:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        downloaded_code = downloader_module.download_arxiv_source_files("1234.56789")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789v1"
            self.assertTrue((output_dir / "main.tex").exists())

        self.assertEqual(downloaded_code, "1234.56789v1")
        self.assertEqual(
            [call.args[0] for call in get_mock.call_args_list],
            ["https://arxiv.org/src/1234.56789"] * 3
            + ["https://arxiv.org/src/1234.56789v1"],
        )

    def test_download_arxiv_source_files_retries_partial_source_downloads_with_clean_dirs(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            content = b"\\documentclass{article}\n"
            info = tarfile.TarInfo("main.tex")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_payload = payload.getvalue()
        source_attempts = 0

        class InterruptedResponse:
            headers = {"content-length": "100"}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                output_dir = Path("1234.56789v1")
                self.assertTrue(output_dir.is_dir())
                self.assertEqual(list(output_dir.iterdir()), [])
                yield b"partial"
                raise RuntimeError("stream reset")

        def fake_get(url, **kwargs):
            nonlocal source_attempts
            if url != "https://arxiv.org/src/1234.56789v1":
                raise AssertionError(f"Unexpected URL: {url}")

            source_attempts += 1
            if source_attempts < 3:
                return InterruptedResponse()

            return self._download_response(tar_payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.downloader.requests.get", side_effect=fake_get) as get_mock:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        downloaded_code = downloader_module.download_arxiv_source_files("1234.56789v1")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789v1"
            self.assertTrue((output_dir / "main.tex").exists())
            self.assertFalse((output_dir / "1234.56789v1.tar.gz").exists())

        self.assertEqual(downloaded_code, "1234.56789v1")
        self.assertEqual(source_attempts, 3)
        self.assertEqual(
            [call.args[0] for call in get_mock.call_args_list],
            ["https://arxiv.org/src/1234.56789v1"] * 3,
        )

    def test_download_arxiv_source_files_removes_empty_output_dir_after_download_error(self):
        class FakeResponse:
            headers = {}

            def raise_for_status(self):
                raise RuntimeError("download failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.downloader.requests.get", return_value=FakeResponse()):
                    with self.assertRaises(downloader_module.SourceDownloadError) as error:
                        downloader_module.download_arxiv_source_files("1234.56789v1")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789v1"
            self.assertFalse(output_dir.exists())

        self.assertIn("Could not download source files for 1234.56789v1", str(error.exception))
        self.assertIn("Could not download PDF for 1234.56789v1", str(error.exception))
        self.assertIn("download failed", str(error.exception))

    def test_download_arxiv_source_files_removes_empty_output_dir_after_empty_archive(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz"):
            pass
        tar_payload = payload.getvalue()

        class FakeResponse:
            headers = {"content-length": str(len(tar_payload))}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield tar_payload

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.downloader.requests.get", return_value=FakeResponse()):
                    with self.assertRaises(downloader_module.SourceDownloadError) as error:
                        with redirect_stderr(io.StringIO()):
                            downloader_module.download_arxiv_source_files("1234.56789v1")
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "1234.56789v1"
            self.assertFalse(output_dir.exists())

        self.assertIn("Downloaded source for 1234.56789v1 contained no files", str(error.exception))

    def test_download_arxiv_source_files_removes_all_dirs_after_all_versions_fail(self):
        class MissingResponse:
            headers = {}

            def raise_for_status(self):
                raise RuntimeError("not found")

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("arxiv_latex_merger.downloader.requests.get", return_value=MissingResponse()) as get_mock:
                    with self.assertRaises(downloader_module.SourceDownloadError):
                        downloader_module.download_arxiv_source_files("1234.56789")
            finally:
                os.chdir(previous_cwd)

            root = Path(temp_dir)
            self.assertFalse((root / "1234.56789").exists())
            self.assertFalse((root / "1234.56789.pdf").exists())

        self.assertEqual(
            [call.args[0] for call in get_mock.call_args_list],
            ["https://arxiv.org/src/1234.56789"] * 3
            + ["https://arxiv.org/src/1234.56789v1"] * 3
            + ["https://arxiv.org/pdf/1234.56789"] * 3
            + ["https://arxiv.org/pdf/1234.56789v1"] * 3,
        )

    def test_download_random_arxiv_papers_downloads_generated_ids_from_source_only(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            content = b"\\documentclass{article}\n"
            info = tarfile.TarInfo("main.tex")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_payload = payload.getvalue()
        requested_urls = []

        class SourceResponse:
            headers = {"content-length": str(len(tar_payload))}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield tar_payload

        def fake_get(url, **kwargs):
            requested_urls.append((url, kwargs))
            if url == "https://arxiv.org/src/2304.9319v1":
                return SourceResponse()
            raise AssertionError(f"Unexpected URL: {url}")

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch(
                    "arxiv_latex_merger.downloader.random.randint",
                    side_effect=[23, 4, 9319, 1],
                ):
                    with patch("arxiv_latex_merger.downloader.requests.get", side_effect=fake_get):
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            arxiv_codes = downloader_module.download_random_arxiv_papers(1)
            finally:
                os.chdir(previous_cwd)

            output_dir = Path(temp_dir) / "2304.9319v1"
            self.assertTrue((output_dir / "main.tex").exists())

        self.assertEqual(arxiv_codes, ["2304.9319v1"])
        self.assertEqual(
            requested_urls,
            [
                ("https://arxiv.org/src/2304.9319v1", {"stream": True}),
            ],
        )

    def test_download_random_arxiv_papers_removes_empty_dir_after_download_failure(self):
        class FakeResponse:
            headers = {}

            def raise_for_status(self):
                raise RuntimeError("download failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch(
                    "arxiv_latex_merger.downloader.random.randint",
                    side_effect=[23, 4, 9319, 1],
                ):
                    with patch("arxiv_latex_merger.downloader.requests.get", return_value=FakeResponse()) as get_mock:
                        with self.assertRaises(downloader_module.SourceDownloadError):
                            with redirect_stderr(io.StringIO()):
                                downloader_module.download_random_arxiv_papers(1)
            finally:
                os.chdir(previous_cwd)

            self.assertFalse((Path(temp_dir) / "2304.9319v1").exists())

        self.assertEqual(
            get_mock.call_args_list,
            [call("https://arxiv.org/src/2304.9319v1", stream=True)] * 3
            + [call("https://arxiv.org/pdf/2304.9319v1", stream=True)] * 3,
        )
