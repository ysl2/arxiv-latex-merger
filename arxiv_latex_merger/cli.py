#!/usr/bin/env python3

__version__='0.2.0'
__author__='iokarkan'

import argparse
from .merger import merge_tex_files, find_main_tex_file
from .downloader import download_arxiv_source_files, download_random_arxiv_papers
from .demacro import LatexDemacro

def main(args):
    print(f"arxiv_latex_merger {__version__}")

    if args.arxiv_codes:
        for code in args.arxiv_codes:
            download_arxiv_source_files(code)
    else:
        print(f"Downloading {args.n_random} random arXiv paper(s)...")
        args.arxiv_codes = download_random_arxiv_papers(args.n_random)

    for code in args.arxiv_codes:
        main_tex_path = find_main_tex_file(code)

        merged_tex_content, encoding = merge_tex_files(
            main_tex_path,
            remove_src=args.remove_src,
            merge_bib=not getattr(args, 'no_bib', False),
        )
        
        output_tex_path = f'{code}_merged_{encoding}.tex'
        
        with open(f"{output_tex_path}", "w") as output_file:
            output_file.write(merged_tex_content)
        print(f"{code} file saved to {output_tex_path}.")

        if args.demacro:
            print(f"WARNING: Using experimental 'demacro' processing...")
            input_tex_path = output_tex_path
            output_tex_path = f"{code}_merged_clean_{encoding}.tex"
            demacro_f = LatexDemacro(inp=input_tex_path, out=output_tex_path)

            try:
                merged_clean = demacro_f.process()
                with open(f"{output_tex_path}", "w") as output_file:
                    output_file.write(merged_clean)
                print(f"{code} file saved to {output_tex_path}.")

            except Exception as e:
                print(f"Could not demacro files for {code}: {e}")
            
        print(f"Finished processing {code}.")


def cli():
    parser = argparse.ArgumentParser(description='Merge LaTeX files from arXiv source.')
    parser.add_argument('--arxiv_codes', nargs='+', default=[], help='The arXiv code(s) for the paper(s).')
    parser.add_argument('--n_random', default=1, help='Fetch n random papers.')
    parser.add_argument('--demacro', action='store_true', default=False, help='(Experimental/Buggy) Attempt to de-macro custom commands defined in the merged file.')
    parser.add_argument('--remove_src', action='store_true', default=False, help='Remove source folder after successful merging.')
    parser.add_argument('--no_bib', action='store_true', default=False, help='Do not inline the generated .bbl bibliography in the merged file.')
    args = parser.parse_args()
    main(args)
