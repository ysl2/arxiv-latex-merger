import os
import random
import tarfile
from pathlib import Path
from tqdm import tqdm
import requests


class SourceDownloadError(RuntimeError):
    pass


def _source_url_for_arxiv_code(arxiv_code):
    return f"https://arxiv.org/src/{arxiv_code}"


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
            pdf_path = _move_downloaded_pdf(tar_file, output_dir, arxiv_code)
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
