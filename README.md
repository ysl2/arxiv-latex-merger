# arXiv LaTeX Merger

A simple command line tool to merge LaTeX files from arXiv source. This tool downloads the LaTeX source files for a given arXiv paper, detects the main TeX file, and merges the contents into a single TeX file.

## Installation

To install the arXiv LaTeX Merger, clone the repository and run the setup script:

```
git clone https://github.com/iokarkan/arxiv_latex_merger.git
cd arxiv_latex_merger
python setup.py install
```

Alternatively, you can install it directly from PyPI (if/once it's published there) using `pip`:

```
pip install arxiv-latex-merger
```

## Usage

To use the arXiv LaTeX Merger, simply run the command `arxiv-latex-merger` followed by the arXiv code of the paper you want to merge:

```
arxiv-latex-merger --arxiv_codes 2304.09319 1812.09740 [...]
```

This will download the source files for all selected codes, detect the main TeX file, merge the TeX inputs, inline the generated `.bbl` bibliography when available, and save the merged TeX files with the downloaded arXiv version in the filename, such as `2304.09319v1.tex` and `1812.09740v2.tex`.

When a code is passed without an explicit version, the tool looks up the latest arXiv version first. If the latest source archive cannot be downloaded, it falls back through older source versions in descending order, for example `v3`, `v2`, `v1`. If no source version is available, it downloads the latest PDF and then falls back through older PDF versions in the same order. Each source/PDF download is retried up to three times before falling back, and failed attempts remove partial files before retrying. Downloaded source/PDF directories also include the version, for example `2304.09319v1/`. PDF-only downloads also create a relative symlink next to the versioned directory, such as `2304.09319v1.pdf -> 2304.09319v1/2304.09319v1.pdf`.

Use `--skip_download_if_exists` to reuse an existing local source directory instead of downloading it again:

```
arxiv-latex-merger --arxiv_codes 2304.09319 --skip_download_if_exists
```

If a local versioned directory such as `2304.09319v1/` exists, the tool reuses it. If no matching versioned directory exists, it falls back to downloading from arXiv as usual.

Use `--no_bib` to preserve the original `\bibliographystyle{...}` and `\bibliography{...}` commands instead of inlining the generated `.bbl` bibliography:

```
arxiv-latex-merger --arxiv_codes 2304.09319 --no_bib
```

Use `--remove_comments` to remove LaTeX comments from the merged file:

```
arxiv-latex-merger --arxiv_codes 2304.09319 --remove_comments
```

This removes `%` comments and standard `comment` environments after merging, while preserving escaped percent signs, syntax-sensitive line-ending `%` characters, and percent signs inside common verbatim/listing environments.

Or try this to download randomly:

```
arxiv-latex-merger --n_random 2
```

### De-macro (🚧)

The motivation to include this tool is to be able to get clean source files that are 
free from custom definitions. As I could not find a working de-macro tool, I have put together some functionality to 
attempt to de-macro common commands found in papers. There are some caveats, and there 
will most likely be exceptions raised in the processing of files, but sometimes it is 
successful in de-macroing all macros found in papers.

You can attempt to de-macro the merged file using the `--demacro` option.

```
arxiv-latex-merger --demacro
```

## License

This project is licensed under the MIT License.
