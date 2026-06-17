#!/usr/bin/env python3

__version__='0.2.0'
__author__='iokarkan'

import argparse
from pathlib import Path
from .merger import merge_tex_files, find_main_tex_file
from .downloader import download_arxiv_source_files, download_random_arxiv_papers
from .demacro import LatexDemacro

def main(args):
    print(f"arxiv_latex_merger {__version__}")

    if args.arxiv_codes:
        local_main_tex_paths = {}
        for code in args.arxiv_codes:
            local_main_tex_path = _find_local_main_tex_file(code) if getattr(args, 'skip_download_if_exists', False) else None
            if local_main_tex_path:
                print(f"Local source directory exists for {code}; skipping download.")
                local_main_tex_paths[code] = local_main_tex_path
            else:
                download_arxiv_source_files(code)
    else:
        print(f"Downloading {args.n_random} random arXiv paper(s)...")
        args.arxiv_codes = download_random_arxiv_papers(args.n_random)
        local_main_tex_paths = {}

    for code in args.arxiv_codes:
        main_tex_path = local_main_tex_paths.get(code) or find_main_tex_file(code)

        merged_tex_content, _encoding = merge_tex_files(
            main_tex_path,
            remove_src=args.remove_src,
            merge_bib=not getattr(args, 'no_bib', False),
            remove_comments=getattr(args, 'remove_comments', False),
        )
        
        output_tex_path = f'{code}.tex'
        
        with open(f"{output_tex_path}", "w") as output_file:
            output_file.write(merged_tex_content)
        print(f"{code} file saved to {output_tex_path}.")

        if args.demacro:
            print(f"WARNING: Using experimental 'demacro' processing...")
            input_tex_path = output_tex_path
            output_tex_path = f"{code}_clean.tex"
            demacro_f = LatexDemacro(inp=input_tex_path, out=output_tex_path)

            try:
                merged_clean = demacro_f.process()
                with open(f"{output_tex_path}", "w") as output_file:
                    output_file.write(merged_clean)
                print(f"{code} file saved to {output_tex_path}.")

            except Exception as e:
                print(f"Could not demacro files for {code}: {e}")
            
        print(f"Finished processing {code}.")


def _find_local_main_tex_file(code):
    if not Path(code).is_dir():
        return None

    try:
        return find_main_tex_file(code)
    except FileNotFoundError:
        print(f"Local source directory exists for {code}, but no main .tex file was found; downloading again.")
        return None


def cli():
    parser = argparse.ArgumentParser(description='Merge LaTeX files from arXiv source.')
    parser.add_argument('--arxiv_codes', nargs='+', default=[], help='The arXiv code(s) for the paper(s).')
    parser.add_argument('--n_random', default=1, help='Fetch n random papers.')
    parser.add_argument('--demacro', action='store_true', default=False, help='(Experimental/Buggy) Attempt to de-macro custom commands defined in the merged file.')
    parser.add_argument('--remove_src', action='store_true', default=False, help='Remove source folder after successful merging.')
    parser.add_argument('--no_bib', action='store_true', default=False, help='Do not inline the generated .bbl bibliography in the merged file.')
    parser.add_argument('--remove_comments', action='store_true', default=False, help='Remove LaTeX comments from the merged file while preserving syntax-sensitive percent characters.')
    parser.add_argument('--skip_download_if_exists', action='store_true', default=False, help='Skip downloading an arXiv source when a local source directory with the same code already exists.')
    args = parser.parse_args()
    main(args)
