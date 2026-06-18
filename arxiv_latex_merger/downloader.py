import os
import random
import tarfile
import time
from math import isinf
from urllib.parse import urlencode
import arxiv
import feedparser
from pathlib import Path
from tqdm import tqdm
import requests

ARXIV_QUERY_URL = "https://export.arxiv.org/api/query?{}"
ARXIV_METADATA_MAX_ATTEMPTS = 4
ARXIV_METADATA_RETRY_DELAY_SECONDS = 5
ARXIV_METADATA_TIMEOUT_SECONDS = 20
ARXIV_TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class SourceDownloadError(RuntimeError):
    pass


class ArxivMetadataTimeoutError(RuntimeError):
    pass


class ArxivMetadataUnexpectedEmptyPageError(RuntimeError):
    pass


def _arxiv_search_url(search):
    max_results = 1 if isinf(search.max_results) else int(search.max_results)
    url_args = search._url_args()
    url_args.update({
        "start": 0,
        "max_results": max_results,
    })
    return ARXIV_QUERY_URL.format(urlencode(url_args))


def _arxiv_results(search):
    response = requests.get(_arxiv_search_url(search), timeout=ARXIV_METADATA_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise arxiv.HTTPError(response.url, 0, response.status_code)

    feed = feedparser.parse(response.content)
    if feed.bozo and not getattr(feed, "entries", None):
        raise ArxivMetadataUnexpectedEmptyPageError("arXiv returned an unparseable metadata feed")

    for entry in feed.entries:
        yield arxiv.Result._from_feed_entry(entry)


def _is_transient_arxiv_metadata_error(error):
    if isinstance(error, arxiv.HTTPError):
        return error.status in ARXIV_TRANSIENT_HTTP_STATUSES

    if isinstance(error, arxiv.UnexpectedEmptyPageError):
        return True

    if isinstance(error, ArxivMetadataTimeoutError):
        return True

    if isinstance(error, ArxivMetadataUnexpectedEmptyPageError):
        return True

    if isinstance(error, requests.Timeout):
        return True

    if isinstance(error, requests.RequestException):
        return True

    return False


def _source_url_for_paper(paper):
    return paper.pdf_url.replace('/pdf/', '/src/')


def _source_url_for_arxiv_code(arxiv_code):
    return f"https://arxiv.org/src/{arxiv_code}"


def _arxiv_id_from_url(url):
    if not url:
        return None

    arxiv_id = url.rstrip('/').rsplit('/', 1)[-1]
    if arxiv_id.lower().endswith('.pdf'):
        arxiv_id = arxiv_id[:-4]
    if arxiv_id.startswith('arXiv-'):
        arxiv_id = arxiv_id[len('arXiv-'):]

    return arxiv_id or None


def _pdf_arxiv_id_for_paper(paper, fallback_arxiv_code):
    if paper is None:
        return fallback_arxiv_code

    for attribute_name in ('entry_id', 'pdf_url'):
        arxiv_id = _arxiv_id_from_url(getattr(paper, attribute_name, None))
        if arxiv_id:
            return arxiv_id

    return fallback_arxiv_code


def _metadata_for_arxiv_code(arxiv_code):
    for attempt in range(1, ARXIV_METADATA_MAX_ATTEMPTS + 1):
        metadata_error = None
        try:
            papers = _arxiv_results(arxiv.Search(id_list=[arxiv_code]))
            return next(papers)
        except requests.Timeout as error:
            metadata_error = ArxivMetadataTimeoutError(
                f"Metadata request timed out after {ARXIV_METADATA_TIMEOUT_SECONDS} seconds"
            )
        except StopIteration as error:
            raise SourceDownloadError(f"No arXiv metadata was found for {arxiv_code}.") from error
        except Exception as error:
            metadata_error = error

        if (
            attempt < ARXIV_METADATA_MAX_ATTEMPTS
            and _is_transient_arxiv_metadata_error(metadata_error)
        ):
            print(
                f"Transient arXiv metadata error for {arxiv_code}: {metadata_error}. "
                f"Retrying in {ARXIV_METADATA_RETRY_DELAY_SECONDS} seconds "
                f"({attempt}/{ARXIV_METADATA_MAX_ATTEMPTS})...",
                flush=True,
            )
            time.sleep(ARXIV_METADATA_RETRY_DELAY_SECONDS)
            continue

        raise SourceDownloadError(
            f"Could not fetch arXiv metadata for {arxiv_code}: {metadata_error}"
        ) from metadata_error

    raise SourceDownloadError(f"Could not fetch arXiv metadata for {arxiv_code}.")


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


def _move_downloaded_pdf(downloaded_path, output_dir, paper, arxiv_code):
    pdf_arxiv_id = _pdf_arxiv_id_for_paper(paper, arxiv_code).replace('/', '_')
    pdf_path = os.path.join(output_dir, f"{pdf_arxiv_id}.pdf")
    os.replace(downloaded_path, pdf_path)
    return pdf_path


def _remove_output_dir_if_empty(output_dir):
    try:
        Path(output_dir).rmdir()
    except OSError:
        return False

    return True


def download_arxiv_source_files(arxiv_code):
    output_dir = arxiv_code
    removed_empty_output_dir = False

    try:
        # Use arxiv API to get the paper object
        print(f"Fetching arXiv metadata for {arxiv_code}...", flush=True)
        source_url = None
        try:
            paper = _metadata_for_arxiv_code(arxiv_code)
            source_url = _source_url_for_paper(paper)
        except SourceDownloadError as error:
            if _is_transient_arxiv_metadata_error(error.__cause__):
                print(
                    f"{error} Downloading source directly from arXiv instead.",
                    flush=True,
                )
                paper = None
                source_url = _source_url_for_arxiv_code(arxiv_code)
            else:
                raise

        # Create the output directory only after we know which source URL to use.
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Download the source files.
        tar_file = os.path.join(output_dir, f"{arxiv_code}.tar.gz")
        print(f"Downloading source files for {arxiv_code}...", flush=True)
        _download_file_with_progress(
            source_url,
            tar_file,
            desc=f"Downloading source for {arxiv_code}",
        )

        if _is_pdf_file(tar_file):
            pdf_path = _move_downloaded_pdf(tar_file, output_dir, paper, arxiv_code)
            raise SourceDownloadError(
                f"arXiv returned a PDF instead of source files. Saved PDF to {pdf_path}."
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


def download_random_arxiv_papers(n=1):
    
    def generate_random_arxiv_id():
        year = str(random.randint(1991, 2022))[-2:]  # arXiv was launched in 1991, change the end year as needed
        month = str(random.randint(1, 12)).rjust(2,"0")
        number = str(random.randint(1, 1412)).rjust(4,"0")  # Choose 28 to avoid issues with months having less than 31 days
        id_part = str(random.randint(1, 2))
        arxiv_id = f"{year}{month}.{number}v{id_part}"
        return arxiv_id

    def is_valid_arxiv_id(arxiv_id):
        url = f"https://arxiv.org/abs/{arxiv_id}"
        response = requests.get(url)
        return response.status_code == 200

    def find_valid_arxiv_id():
        while True:
            random_arxiv_id = generate_random_arxiv_id()
            if is_valid_arxiv_id(random_arxiv_id):
                return random_arxiv_id

    random_papers = arxiv.Search(id_list=[find_valid_arxiv_id() for _ in tqdm(range(int(n)), 
                                                                              desc=f"Generating {n} random arXiv codes...", 
                                                                              unit="code")],
                                max_results=int(n),
                                sort_by = arxiv.SortCriterion.SubmittedDate)
    arxiv_codes = []
    for paper in _arxiv_results(random_papers):
        arxiv_code = paper.entry_id.rsplit('/', 1)[-1]
        print(f"Downloading source files for paper: {arxiv_code}")
        download_arxiv_source_files(arxiv_code)
        arxiv_codes.append(arxiv_code)
    
    return arxiv_codes
