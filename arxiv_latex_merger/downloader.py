import os
import random
import tarfile
import arxiv
from pathlib import Path
from tqdm import tqdm
import requests

ARXIV_QUERY_URL = "https://export.arxiv.org/api/query?{}"


class SourceDownloadError(RuntimeError):
    pass


def _arxiv_results(search):
    client = arxiv.Client()
    client.query_url_format = ARXIV_QUERY_URL
    return client.results(search)


def _source_url_for_paper(paper):
    return paper.pdf_url.replace('/pdf/', '/src/')


def _metadata_for_arxiv_code(arxiv_code):
    try:
        papers = _arxiv_results(arxiv.Search(id_list=[arxiv_code]))
        return next(papers)
    except StopIteration as error:
        raise SourceDownloadError(f"No arXiv metadata was found for {arxiv_code}.") from error
    except Exception as error:
        raise SourceDownloadError(f"Could not fetch arXiv metadata for {arxiv_code}: {error}") from error


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


def _remove_output_dir_if_empty(output_dir):
    try:
        Path(output_dir).rmdir()
    except OSError:
        return False

    return True


def download_arxiv_source_files(arxiv_code):
    output_dir = arxiv_code
    removed_empty_output_dir = False

    # Use arxiv API to get the paper object
    print(f"Fetching arXiv metadata for {arxiv_code}...", flush=True)
    paper = _metadata_for_arxiv_code(arxiv_code)

    # Create the output directory only after metadata is available.
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Download the source files.
    tar_file = os.path.join(output_dir, f"{arxiv_code}.tar.gz")
    try:
        print(f"Downloading source files for {arxiv_code}...", flush=True)
        _download_file_with_progress(
            _source_url_for_paper(paper),
            tar_file,
            desc=f"Downloading source for {arxiv_code}",
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
