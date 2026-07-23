#!/usr/bin/env python3

__version__='0.2.0'
__author__='iokarkan'

import argparse
import asyncio
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
        arxiv_codes = list(args.arxiv_codes)
        if len(arxiv_codes) > 1:
            asyncio.run(
                _process_arxiv_codes_concurrently(
                    arxiv_codes,
                    args,
                    max_threads=getattr(args, 'threds', 8),
                )
            )
        else:
            _process_arxiv_code(arxiv_codes[0], args)
    else:
        print(f"Downloading {args.n_random} random arXiv paper(s)...")
        download_options = _download_options(args)
        arxiv_codes = download_random_arxiv_papers(args.n_random, **download_options)
        for code in arxiv_codes:
            _merge_arxiv_code(code, args)


async def _process_arxiv_codes_concurrently(arxiv_codes, args, max_threads):
    worker_limit = asyncio.Semaphore(max_threads)

    async def process_one(code):
        async with worker_limit:
            try:
                await asyncio.to_thread(_process_arxiv_code, code, args)
            except Exception as error:
                print(f"Failed processing {code}: {error}")

    await asyncio.gather(*(process_one(code) for code in arxiv_codes))


def _download_options(args):
    options = {}
    if not getattr(args, 'skip_download_if_exists', True):
        options['skip_download_if_exists'] = False
    if getattr(args, 'delete_tar_after_download', False):
        options['delete_tar_after_download'] = True
    return options


def _delete_folder_after_merge(args):
    return getattr(
        args,
        'delete_folder_after_merge',
        getattr(args, 'remove_src', False),
    )


def _process_arxiv_code(code, args):
    actual_code = code
    local_main_tex_path = None
    skip_download_if_exists = getattr(args, 'skip_download_if_exists', True)

    if skip_download_if_exists:
        local_code = _find_local_downloaded_code(code)
        if local_code:
            actual_code = local_code

        # An archive is authoritative and must be re-extracted, even when the
        # matching source directory also exists.
        local_archive_exists = bool(
            local_code and Path(f"{local_code}.tar.gz").is_file()
        )
        if not local_archive_exists:
            local_pdf_path = _find_local_pdf_only_file(actual_code)
            if local_pdf_path:
                print(
                    f"Local PDF-only source exists for {actual_code} at "
                    f"{local_pdf_path}; skipping download and merge."
                )
                _remove_other_versions(actual_code)
                return None

            local_main_tex_path = _find_local_main_tex_file(actual_code)
            if local_main_tex_path:
                print(f"Local source directory exists for {actual_code}; skipping download.")
            elif local_code and Path(local_code).is_dir():
                print(
                    f"Skipping {actual_code}: the local source directory has no "
                    "main .tex file, and local reuse is enabled."
                )
                return None

    if not local_main_tex_path:
        try:
            downloaded_code = download_arxiv_source_files(
                code,
                **_download_options(args),
            )
            if downloaded_code:
                actual_code = downloaded_code
        except SourceDownloadError as error:
            print(f"Skipping {code}: {error}")
            return None

    if _find_local_pdf_only_file(actual_code):
        print(f"PDF-only source downloaded for {actual_code}; skipping merge.")
        _remove_other_versions(actual_code)
        return None

    _remove_other_versions(actual_code)
    return _merge_arxiv_code(actual_code, args, main_tex_path=local_main_tex_path)


def _merge_arxiv_code(code, args, main_tex_path=None):
    local_pdf_path = _find_local_pdf_only_file(code)
    if local_pdf_path:
        print(f"PDF-only source exists for {code} at {local_pdf_path}; skipping merge.")
        return None

    if main_tex_path is None:
        try:
            main_tex_path = find_main_tex_file(code)
        except FileNotFoundError as error:
            print(f"Skipping {code}: {error}")
            return None

    merged_tex_content, _encoding = merge_tex_files(
        main_tex_path,
        remove_src=_delete_folder_after_merge(args),
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
    return code


def _version_sort_key(code):
    _, version = split_arxiv_code_version(code)
    return version or 0


def _find_local_downloaded_code(code):
    base_code, requested_version = split_arxiv_code_version(code)
    if requested_version is not None:
        return code if Path(code).is_dir() or Path(f"{code}.tar.gz").is_file() else None

    candidate_codes = [
        _candidate_arxiv_artifact_code(path)
        for path in Path('.').iterdir()
        if (path.is_dir() or (path.is_file() and path.name.endswith('.tar.gz')))
        and split_arxiv_code_version(_candidate_arxiv_artifact_code(path))[0] == base_code
        and split_arxiv_code_version(_candidate_arxiv_artifact_code(path))[1] is not None
    ]
    if not candidate_codes:
        return None

    return sorted(candidate_codes, key=_version_sort_key, reverse=True)[0]


def _find_local_main_tex_file(code):
    if not Path(code).is_dir():
        return None

    try:
        return find_main_tex_file(code)
    except FileNotFoundError:
        print(f"Local source directory exists for {code}, but no main .tex file was found.")
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


def _parse_bool(value):
    if isinstance(value, bool):
        return value

    normalized_value = value.lower()
    if normalized_value in {'true', '1', 'yes', 'on'}:
        return True
    if normalized_value in {'false', '0', 'no', 'off'}:
        return False

    raise argparse.ArgumentTypeError(
        f"Expected true or false, received: {value}"
    )


def _positive_int(value):
    parsed_value = int(value)
    if parsed_value < 1:
        raise argparse.ArgumentTypeError("Thread count must be at least 1.")
    return parsed_value


def _build_parser():
    parser = argparse.ArgumentParser(description='Merge LaTeX files from arXiv source.')
    parser.add_argument('--arxiv_codes', nargs='+', default=[], help='The arXiv code(s) for the paper(s).')
    parser.add_argument('--n_random', default=1, help='Fetch n random papers.')
    parser.add_argument('--demacro', action='store_true', default=False, help='(Experimental/Buggy) Attempt to de-macro custom commands defined in the merged file.')
    parser.add_argument('--no_bib', action='store_true', default=False, help='Do not inline the generated .bbl bibliography in the merged file.')
    parser.add_argument('--remove_comments', action='store_true', default=False, help='Remove LaTeX comments from the merged file while preserving syntax-sensitive percent characters.')
    parser.add_argument(
        '--skip_download_if_exists',
        type=_parse_bool,
        nargs='?',
        const=True,
        default=True,
        help='Reuse a matching local archive or source folder (default: true). Set to false to force a fresh download and extraction.',
    )
    parser.add_argument(
        '--delete_tar_after_download',
        type=_parse_bool,
        nargs='?',
        const=True,
        default=False,
        help='Delete the source .tar.gz after successful extraction (default: false).',
    )
    parser.add_argument(
        '--delete_folder_after_merge',
        type=_parse_bool,
        nargs='?',
        const=True,
        default=False,
        help='Delete the extracted source folder after a successful merge (default: false).',
    )
    parser.add_argument(
        '--threds',
        '--threads',
        dest='threds',
        type=_positive_int,
        default=8,
        help='Maximum concurrent paper-processing workers (default: 8).',
    )
    return parser


def cli():
    args = _build_parser().parse_args()
    main(args)
