#!/usr/bin/env python3

__version__='0.2.0'
__author__='iokarkan'

import argparse
import shutil
from pathlib import Path
from .merger import merge_tex_files, find_main_tex_file
from .downloader import (
    SourceDownloadError,
    download_arxiv_source_files,
    download_random_arxiv_papers,
    split_arxiv_code_version,
)
from .demacro import LatexDemacro

def main(args):
    print(f"arxiv_latex_merger {__version__}")

    if args.arxiv_codes:
        local_main_tex_paths = {}
        available_arxiv_codes = []
        for code in args.arxiv_codes:
            actual_code = code

            local_code = _find_local_downloaded_code(code) if getattr(args, 'skip_download_if_exists', False) else None
            if local_code:
                actual_code = local_code

            local_pdf_path = _find_local_pdf_only_file(actual_code) if getattr(args, 'skip_download_if_exists', False) else None
            if local_pdf_path:
                print(f"Local PDF-only source exists for {actual_code} at {local_pdf_path}; skipping download and merge.")
                _remove_other_versions(actual_code)
                continue

            local_main_tex_path = _find_local_main_tex_file(actual_code) if getattr(args, 'skip_download_if_exists', False) else None
            if local_main_tex_path:
                print(f"Local source directory exists for {actual_code}; skipping download.")
                local_main_tex_paths[actual_code] = local_main_tex_path
            else:
                try:
                    downloaded_code = download_arxiv_source_files(code)
                    if downloaded_code:
                        actual_code = downloaded_code
                except SourceDownloadError as error:
                    print(f"Skipping {code}: {error}")
                    continue

            if _find_local_pdf_only_file(actual_code):
                print(f"PDF-only source downloaded for {actual_code}; skipping merge.")
                _remove_other_versions(actual_code)
                continue

            _remove_other_versions(actual_code)
            available_arxiv_codes.append(actual_code)
        args.arxiv_codes = available_arxiv_codes
    else:
        print(f"Downloading {args.n_random} random arXiv paper(s)...")
        args.arxiv_codes = download_random_arxiv_papers(args.n_random)
        local_main_tex_paths = {}

    for code in args.arxiv_codes:
        local_pdf_path = _find_local_pdf_only_file(code)
        if local_pdf_path:
            print(f"PDF-only source exists for {code} at {local_pdf_path}; skipping merge.")
            continue

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


def _version_sort_key(code):
    _, version = split_arxiv_code_version(code)
    return version or 0


def _find_local_downloaded_code(code):
    base_code, requested_version = split_arxiv_code_version(code)
    if requested_version is not None:
        return code if Path(code).is_dir() else None

    candidate_dirs = [
        path.name
        for path in Path('.').iterdir()
        if path.is_dir()
        and split_arxiv_code_version(path.name)[0] == base_code
        and split_arxiv_code_version(path.name)[1] is not None
    ]
    if not candidate_dirs:
        return None

    return sorted(candidate_dirs, key=_version_sort_key, reverse=True)[0]


def _find_local_main_tex_file(code):
    if not Path(code).is_dir():
        return None

    try:
        return find_main_tex_file(code)
    except FileNotFoundError:
        print(f"Local source directory exists for {code}, but no main .tex file was found; downloading again.")
        return None


def _find_local_pdf_only_file(code):
    source_dir = Path(code)
    if not source_dir.is_dir():
        return None

    file_paths = [path for path in source_dir.rglob('*') if path.is_file()]
    if any(path.suffix.lower() == '.tex' for path in file_paths):
        return None

    pdf_paths = sorted(path for path in file_paths if path.suffix.lower() == '.pdf')
    if not pdf_paths:
        return None

    return str(pdf_paths[0])


def _remove_other_versions(code):
    base_code, kept_version = split_arxiv_code_version(code)
    if kept_version is None:
        return

    for path in Path('.').iterdir():
        candidate_code = _candidate_arxiv_artifact_code(path)
        if not candidate_code or candidate_code == code:
            continue

        candidate_base_code, candidate_version = split_arxiv_code_version(candidate_code)
        if candidate_base_code != base_code:
            continue

        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        version_label = f"v{candidate_version}" if candidate_version is not None else "unversioned"
        print(f"Removed old local {version_label} artifact {path}.")


def _candidate_arxiv_artifact_code(path):
    name = path.name
    for suffix in ('.tar.gz', '_clean.tex', '.tex', '.pdf'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break

    return name


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
