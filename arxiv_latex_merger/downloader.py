import os
import random
import re
import shutil
import tarfile
from urllib.parse import unquote
from pathlib import Path
from tqdm import tqdm
import requests


class SourceDownloadError(RuntimeError):
    def __init__(self, message, actual_code=None, retryable=True):
        super().__init__(message)
        self.actual_code = actual_code
        self.retryable = retryable


_ARXIV_VERSION_PATTERN = re.compile(r"^(?P<base>.+?)v(?P<version>\d+)$")
_CONTENT_DISPOSITION_FILENAME_PATTERN = re.compile(
    r'filename\*?=(?:"([^"]+)"|([^;]+))',
    re.IGNORECASE,
)
_DOWNLOAD_ATTEMPTS = 3


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


def _content_disposition_filename(headers):
    content_disposition = headers.get("content-disposition") or headers.get("Content-Disposition")
    if not content_disposition:
        return None

    match = _CONTENT_DISPOSITION_FILENAME_PATTERN.search(content_disposition)
    if not match:
        return None

    filename = match.group(1) or match.group(2)
    filename = filename.strip()
    if "''" in filename:
        filename = filename.split("''", 1)[1]

    return unquote(filename)


def _versioned_arxiv_code_from_filename(filename):
    if not filename:
        return None

    filename = Path(filename).name
    if filename.startswith("arXiv-"):
        filename = filename[len("arXiv-"):]

    for suffix in (".tar.gz", ".pdf", ".gz", ".tar"):
        if filename.endswith(suffix):
            filename = filename[:-len(suffix)]
            break

    base_code, version = split_arxiv_code_version(filename)
    if version is None:
        return None

    return versioned_arxiv_code(base_code, version)


def _actual_arxiv_code_from_response(response, requested_code):
    response_code = _versioned_arxiv_code_from_filename(
        _content_disposition_filename(response.headers)
    )
    if not response_code:
        return requested_code

    requested_base_code, _ = split_arxiv_code_version(requested_code)
    response_base_code, _ = split_arxiv_code_version(response_code)
    if requested_base_code == response_base_code:
        return response_code

    return requested_code


def _response_filename(response):
    return _content_disposition_filename(response.headers) or ""


def _response_is_pdf(response):
    content_type = response.headers.get("content-type") or response.headers.get("Content-Type") or ""
    if "application/pdf" in content_type.lower():
        return True

    return _response_filename(response).lower().endswith(".pdf")


def _known_versioned_code(arxiv_code):
    _, version = split_arxiv_code_version(arxiv_code)
    if version is None:
        return None

    return arxiv_code


def _older_version_candidates(arxiv_code):
    base_arxiv_code, version = split_arxiv_code_version(arxiv_code)
    if version is None:
        return []

    return [
        versioned_arxiv_code(base_arxiv_code, candidate_version)
        for candidate_version in range(version - 1, 0, -1)
    ]


def _version_fallback_candidates(arxiv_code):
    base_arxiv_code, version = split_arxiv_code_version(arxiv_code)
    if version is None:
        return [versioned_arxiv_code(base_arxiv_code, 1)]

    return _older_version_candidates(arxiv_code)


def _attempt_code(candidate_code, error):
    return error.actual_code or candidate_code


def _request_download(url):
    response = requests.get(url, stream=True)
    response.raise_for_status()
    return response


def _download_response_with_progress(response, path, desc, url):
    content_length = response.headers.get("content-length")
    try:
        total_size = int(content_length) if content_length else None
    except ValueError:
        total_size = None

    downloaded_size = 0
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
                    downloaded_size += len(chunk)
                    progress_bar.update(len(chunk))

    if total_size is not None and downloaded_size != total_size:
        raise IOError(
            f"Downloaded {downloaded_size} bytes from {url}, expected {total_size} bytes."
        )


def _is_pdf_file(path):
    with open(path, 'rb') as downloaded_file:
        return downloaded_file.read(4) == b'%PDF'


def _move_downloaded_pdf(downloaded_path, output_dir, arxiv_code):
    pdf_path = os.path.join(output_dir, _pdf_file_name(arxiv_code))
    os.replace(downloaded_path, pdf_path)
    return pdf_path


def _pdf_file_name(arxiv_code):
    return f"{arxiv_code.replace('/', '_')}.pdf"


def _pdf_symlink_path(output_dir, arxiv_code):
    return Path(output_dir).parent / _pdf_file_name(arxiv_code)


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


def _remove_pdf_symlink(output_dir, arxiv_code):
    link_path = _pdf_symlink_path(output_dir, arxiv_code)
    if link_path.is_symlink():
        link_path.unlink()


def _clear_download_artifacts(output_dir, arxiv_code):
    _remove_pdf_symlink(output_dir, arxiv_code)

    output_path = Path(output_dir)
    if output_path.is_symlink() or output_path.is_file():
        output_path.unlink()
    elif output_path.is_dir():
        shutil.rmtree(output_path)


def _format_attempt_errors(attempt_errors):
    return '; '.join(f"{code}: {error}" for code, error in attempt_errors)


def _format_retry_errors(errors):
    return '; '.join(f"attempt {index}: {error}" for index, error in enumerate(errors, start=1))


def _run_download_attempts(arxiv_code, media_name, download_once):
    errors = []

    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        _clear_download_artifacts(arxiv_code, arxiv_code)
        try:
            return download_once()
        except KeyboardInterrupt:
            _clear_download_artifacts(arxiv_code, arxiv_code)
            raise
        except SourceDownloadError as error:
            errors.append(error)
            _clear_download_artifacts(arxiv_code, arxiv_code)
            if error.actual_code:
                _clear_download_artifacts(error.actual_code, error.actual_code)
            if not error.retryable:
                raise
            if attempt < _DOWNLOAD_ATTEMPTS:
                print(
                    f"{media_name} download attempt {attempt}/{_DOWNLOAD_ATTEMPTS} "
                    f"failed for {arxiv_code}: {error}; retrying."
                )

    actual_code = next((error.actual_code for error in errors if error.actual_code), None)
    raise SourceDownloadError(
        f"Could not download {media_name.lower()} for {arxiv_code} after "
        f"{_DOWNLOAD_ATTEMPTS} attempts: {_format_retry_errors(errors)}",
        actual_code=actual_code,
    )


def _download_arxiv_source_version_once(arxiv_code):
    actual_code = arxiv_code

    try:
        response = _request_download(_source_url_for_arxiv_code(arxiv_code))
        actual_code = _actual_arxiv_code_from_response(response, arxiv_code)
        if _response_is_pdf(response):
            close_response = getattr(response, "close", None)
            if close_response:
                close_response()
            raise SourceDownloadError(
                f"arXiv returned a PDF instead of source files for {actual_code}.",
                actual_code=_known_versioned_code(actual_code),
                retryable=False,
            )

        output_dir = actual_code
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Download the source files.
        tar_file = os.path.join(output_dir, f"{actual_code}.tar.gz")
        print(f"Downloading source files for {actual_code}...", flush=True)
        _download_response_with_progress(
            response,
            tar_file,
            desc=f"Downloading source for {actual_code}",
            url=_source_url_for_arxiv_code(arxiv_code),
        )

        if _is_pdf_file(tar_file):
            os.remove(tar_file)
            raise SourceDownloadError(
                f"arXiv returned a PDF instead of source files for {actual_code}.",
                actual_code=_known_versioned_code(actual_code),
                retryable=False,
            )

        try:
            with tarfile.open(tar_file, "r:gz") as tar:
                members = tar.getmembers()
                if not members:
                    raise SourceDownloadError(
                        f"Downloaded source for {actual_code} contained no files.",
                        actual_code=_known_versioned_code(actual_code),
                    )

                with tqdm(
                    total=len(members),
                    unit="file",
                    desc=f"Extracting source files for {actual_code}",
                ) as progress_bar:
                    for member in members:
                        tar.extract(member, output_dir)
                        progress_bar.update(1)
        except tarfile.TarError as error:
            if os.path.exists(tar_file):
                os.remove(tar_file)
            raise SourceDownloadError(
                f"Downloaded source for {actual_code} was not a gzipped tar archive. "
                "arXiv may only provide a PDF for this paper.",
                actual_code=_known_versioned_code(actual_code),
            ) from error

        # Remove the tar file
        os.remove(tar_file)
    except SourceDownloadError:
        raise
    except Exception as error:
        raise SourceDownloadError(
            f"Could not download source files for {actual_code}: {error}",
            actual_code=_known_versioned_code(actual_code),
        ) from error
    print(f"Successfully downloaded and extracted source files to {output_dir} directory")
    return actual_code


def _download_arxiv_source_version(arxiv_code):
    return _run_download_attempts(
        arxiv_code,
        "Source",
        lambda: _download_arxiv_source_version_once(arxiv_code),
    )


def _download_arxiv_pdf_version_once(arxiv_code):
    actual_code = arxiv_code
    temporary_pdf_path = None
    symlink_path = None

    try:
        response = _request_download(_pdf_url_for_arxiv_code(arxiv_code))
        actual_code = _actual_arxiv_code_from_response(response, arxiv_code)
        output_dir = actual_code
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        temporary_pdf_path = os.path.join(output_dir, f"{actual_code}.pdf.download")
        print(f"Downloading PDF for {actual_code}...", flush=True)
        _download_response_with_progress(
            response,
            temporary_pdf_path,
            desc=f"Downloading PDF for {actual_code}",
            url=_pdf_url_for_arxiv_code(arxiv_code),
        )

        if not _is_pdf_file(temporary_pdf_path):
            os.remove(temporary_pdf_path)
            raise SourceDownloadError(
                f"Downloaded PDF for {actual_code} was not a PDF file.",
                actual_code=_known_versioned_code(actual_code),
            )

        pdf_path = _move_downloaded_pdf(temporary_pdf_path, output_dir, actual_code)
        symlink_path = _create_relative_pdf_symlink(pdf_path, output_dir)
    except SourceDownloadError:
        raise
    except Exception as error:
        if temporary_pdf_path and os.path.exists(temporary_pdf_path):
            os.remove(temporary_pdf_path)
        raise SourceDownloadError(
            f"Could not download PDF for {actual_code}: {error}",
            actual_code=_known_versioned_code(actual_code),
        ) from error

    if symlink_path:
        print(f"Created relative PDF symlink at {symlink_path}")
    print(f"Successfully downloaded PDF to {pdf_path}")
    return actual_code


def _download_arxiv_pdf_version(arxiv_code):
    return _run_download_attempts(
        arxiv_code,
        "PDF",
        lambda: _download_arxiv_pdf_version_once(arxiv_code),
    )


def download_arxiv_source_files(arxiv_code):
    _, requested_version = split_arxiv_code_version(arxiv_code)
    source_errors = []
    source_candidates = [arxiv_code]
    tried_source_candidates = set()
    latest_known_code = None

    while source_candidates:
        candidate_code = source_candidates.pop(0)
        if candidate_code in tried_source_candidates:
            continue
        tried_source_candidates.add(candidate_code)
        try:
            return _download_arxiv_source_version(candidate_code)
        except SourceDownloadError as error:
            attempted_code = _attempt_code(candidate_code, error)
            source_errors.append((attempted_code, error))
            print(f"Source download failed for {attempted_code}: {error}")

            if requested_version is None and latest_known_code is None and error.actual_code:
                latest_known_code = error.actual_code
                source_candidates.extend(_version_fallback_candidates(latest_known_code))

    if requested_version is None and latest_known_code is None:
        source_candidates.extend(_version_fallback_candidates(arxiv_code))
        while source_candidates:
            candidate_code = source_candidates.pop(0)
            if candidate_code in tried_source_candidates:
                continue
            tried_source_candidates.add(candidate_code)
            try:
                return _download_arxiv_source_version(candidate_code)
            except SourceDownloadError as error:
                attempted_code = _attempt_code(candidate_code, error)
                source_errors.append((attempted_code, error))
                print(f"Source download failed for {attempted_code}: {error}")

    pdf_errors = []
    if requested_version is not None:
        pdf_candidates = [arxiv_code]
    elif latest_known_code:
        pdf_candidates = [latest_known_code] + _version_fallback_candidates(latest_known_code)
    else:
        pdf_candidates = [arxiv_code] + _version_fallback_candidates(arxiv_code)

    tried_pdf_candidates = set()
    while pdf_candidates:
        candidate_code = pdf_candidates.pop(0)
        if candidate_code in tried_pdf_candidates:
            continue
        tried_pdf_candidates.add(candidate_code)
        try:
            return _download_arxiv_pdf_version(candidate_code)
        except SourceDownloadError as error:
            attempted_code = _attempt_code(candidate_code, error)
            pdf_errors.append((attempted_code, error))
            print(f"PDF download failed for {attempted_code}: {error}")

            if requested_version is None and latest_known_code is None and error.actual_code:
                latest_known_code = error.actual_code
                pdf_candidates.extend(_older_version_candidates(latest_known_code))

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
