import os
import random
import re
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from tqdm import tqdm
import requests


class SourceDownloadError(RuntimeError):
    pass


_ARXIV_API_URL = "https://export.arxiv.org/api/query"
_ARXIV_VERSION_PATTERN = re.compile(r"^(?P<base>.+?)v(?P<version>\d+)$")
_ATOM_NAMESPACE = {"atom": "http://www.w3.org/2005/Atom"}


def split_arxiv_code_version(arxiv_code):
    match = _ARXIV_VERSION_PATTERN.match(arxiv_code)
    if not match:
        return arxiv_code, None

    return match.group("base"), int(match.group("version"))


def versioned_arxiv_code(arxiv_code, version):
    return f"{arxiv_code}v{version}"


def _source_url_for_arxiv_code(arxiv_code):
    return f"https://arxiv.org/src/{arxiv_code}"


def _pdf_url_for_arxiv_code(arxiv_code):
    return f"https://arxiv.org/pdf/{arxiv_code}"


def _latest_version_for_arxiv_code(arxiv_code):
    base_arxiv_code, requested_version = split_arxiv_code_version(arxiv_code)
    if requested_version is not None:
        return requested_version

    try:
        response = requests.get(_ARXIV_API_URL, params={"id_list": base_arxiv_code})
        response.raise_for_status()
        feed = ET.fromstring(response.text)
    except Exception as error:
        raise SourceDownloadError(
            f"Could not determine latest version for {arxiv_code}: {error}"
        ) from error

    entry = feed.find("atom:entry", _ATOM_NAMESPACE)
    if entry is None:
        raise SourceDownloadError(f"Could not determine latest version for {arxiv_code}: no arXiv API entry found.")

    entry_id = entry.findtext("atom:id", default="", namespaces=_ATOM_NAMESPACE).rstrip("/").split("/")[-1]
    _, latest_version = split_arxiv_code_version(entry_id)
    if latest_version is None:
        latest_version = 1

    return latest_version


def _version_candidates_for_arxiv_code(arxiv_code):
    base_arxiv_code, requested_version = split_arxiv_code_version(arxiv_code)
    latest_version = requested_version or _latest_version_for_arxiv_code(base_arxiv_code)

    return [
        versioned_arxiv_code(base_arxiv_code, version)
        for version in range(latest_version, 0, -1)
    ]


def _download_file_with_progress(url, path, desc):
    response = requests.get(url, stream=True)
    response.raise_for_status()

    content_length = response.headers.get("content-length")
    try:
        total_size = int(content_length) if content_length else None
    except ValueError:
        total_size = None

    with open(path, "wb") as output_file:
        with tqdm(
            total=total_size,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=desc,
        ) as progress_bar:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    output_file.write(chunk)
                    progress_bar.update(len(chunk))


def _is_pdf_file(path):
    with open(path, 'rb') as downloaded_file:
        return downloaded_file.read(4) == b'%PDF'


def _move_downloaded_pdf(downloaded_path, output_dir, arxiv_code):
    pdf_arxiv_id = arxiv_code.replace('/', '_')
    pdf_path = os.path.join(output_dir, f"{pdf_arxiv_id}.pdf")
    os.replace(downloaded_path, pdf_path)
    return pdf_path


def _create_relative_pdf_symlink(pdf_path, output_dir):
    pdf_path = Path(pdf_path)
    link_path = Path(output_dir).parent / pdf_path.name
    relative_target = os.path.relpath(pdf_path, start=link_path.parent)

    if link_path.is_symlink():
        link_path.unlink()
    elif link_path.exists():
        print(f"PDF symlink path {link_path} already exists; leaving it unchanged.")
        return None

    os.symlink(relative_target, link_path)
    return link_path


def _remove_output_dir_if_empty(output_dir):
    try:
        Path(output_dir).rmdir()
    except OSError:
        return False

    return True


def _download_arxiv_source_version(arxiv_code):
    output_dir = arxiv_code
    removed_empty_output_dir = False

    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Download the source files.
        tar_file = os.path.join(output_dir, f"{arxiv_code}.tar.gz")
        print(f"Downloading source files for {arxiv_code}...", flush=True)
        _download_file_with_progress(
            _source_url_for_arxiv_code(arxiv_code),
            tar_file,
            desc=f"Downloading source for {arxiv_code}",
        )

        if _is_pdf_file(tar_file):
            os.remove(tar_file)
            raise SourceDownloadError(
                f"arXiv returned a PDF instead of source files for {arxiv_code}."
            )

        try:
            with tarfile.open(tar_file, "r:gz") as tar:
                members = tar.getmembers()
                with tqdm(
                    total=len(members),
                    unit="file",
                    desc=f"Extracting source files for {arxiv_code}",
                ) as progress_bar:
                    for member in members:
                        tar.extract(member, output_dir)
                        progress_bar.update(1)
        except tarfile.TarError as error:
            if os.path.exists(tar_file):
                os.remove(tar_file)
            raise SourceDownloadError(
                f"Downloaded source for {arxiv_code} was not a gzipped tar archive. "
                "arXiv may only provide a PDF for this paper."
            ) from error

        # Remove the tar file
        os.remove(tar_file)
    except SourceDownloadError:
        raise
    except Exception as error:
        raise SourceDownloadError(f"Could not download source files for {arxiv_code}: {error}") from error
    finally:
        removed_empty_output_dir = _remove_output_dir_if_empty(output_dir)

    if removed_empty_output_dir:
        raise SourceDownloadError(f"Downloaded source for {arxiv_code} contained no files.")

    print(f"Successfully downloaded and extracted source files to {output_dir} directory")
    return arxiv_code


def _download_arxiv_pdf_version(arxiv_code):
    output_dir = arxiv_code
    removed_empty_output_dir = False
    temporary_pdf_path = None

    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        temporary_pdf_path = os.path.join(output_dir, f"{arxiv_code}.pdf.download")
        print(f"Downloading PDF for {arxiv_code}...", flush=True)
        _download_file_with_progress(
            _pdf_url_for_arxiv_code(arxiv_code),
            temporary_pdf_path,
            desc=f"Downloading PDF for {arxiv_code}",
        )

        if not _is_pdf_file(temporary_pdf_path):
            os.remove(temporary_pdf_path)
            raise SourceDownloadError(f"Downloaded PDF for {arxiv_code} was not a PDF file.")

        pdf_path = _move_downloaded_pdf(temporary_pdf_path, output_dir, arxiv_code)
        symlink_path = _create_relative_pdf_symlink(pdf_path, output_dir)
    except SourceDownloadError:
        raise
    except Exception as error:
        if temporary_pdf_path and os.path.exists(temporary_pdf_path):
            os.remove(temporary_pdf_path)
        raise SourceDownloadError(f"Could not download PDF for {arxiv_code}: {error}") from error
    finally:
        removed_empty_output_dir = _remove_output_dir_if_empty(output_dir)

    if removed_empty_output_dir:
        raise SourceDownloadError(f"Downloaded PDF for {arxiv_code} contained no files.")

    if symlink_path:
        print(f"Created relative PDF symlink at {symlink_path}")
    print(f"Successfully downloaded PDF to {pdf_path}")
    return arxiv_code


def _format_attempt_errors(attempt_errors):
    return '; '.join(f"{code}: {error}" for code, error in attempt_errors)


def download_arxiv_source_files(arxiv_code):
    arxiv_code_candidates = _version_candidates_for_arxiv_code(arxiv_code)
    source_errors = []

    for candidate_code in arxiv_code_candidates:
        try:
            return _download_arxiv_source_version(candidate_code)
        except SourceDownloadError as error:
            source_errors.append((candidate_code, error))
            print(f"Source download failed for {candidate_code}: {error}")

    pdf_errors = []
    for candidate_code in arxiv_code_candidates:
        try:
            return _download_arxiv_pdf_version(candidate_code)
        except SourceDownloadError as error:
            pdf_errors.append((candidate_code, error))
            print(f"PDF download failed for {candidate_code}: {error}")

    raise SourceDownloadError(
        f"Could not download source or PDF for {arxiv_code}. "
        f"Source attempts: {_format_attempt_errors(source_errors)}. "
        f"PDF attempts: {_format_attempt_errors(pdf_errors)}."
    )


def download_random_arxiv_papers(n=1):
    
    def generate_random_arxiv_id():
        year = str(random.randint(1991, 2022))[-2:]  # arXiv was launched in 1991, change the end year as needed
        month = str(random.randint(1, 12)).rjust(2,"0")
        number = str(random.randint(1, 1412)).rjust(4,"0")  # Choose 28 to avoid issues with months having less than 31 days
        id_part = str(random.randint(1, 2))
        arxiv_id = f"{year}{month}.{number}v{id_part}"
        return arxiv_id

    arxiv_codes = [
        generate_random_arxiv_id()
        for _ in tqdm(
            range(int(n)),
            desc=f"Generating {n} random arXiv codes...",
            unit="code",
        )
    ]
    for arxiv_code in arxiv_codes:
        print(f"Downloading source files for paper: {arxiv_code}")
        download_arxiv_source_files(arxiv_code)
    
    return arxiv_codes
