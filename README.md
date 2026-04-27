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

This will download the source files for all selected codes, detect the main TeX file, merge the TeX inputs, inline the generated `.bbl` bibliography when available, and save the merged TeX files as `2304.09319_merged_utf-8.tex` and `1812.09740_merged_utf-8.tex`.

Use `--no_bib` to preserve the original `\bibliographystyle{...}` and `\bibliography{...}` commands instead of inlining the generated `.bbl` bibliography:

```
arxiv-latex-merger --arxiv_codes 2304.09319 --no_bib
```

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


